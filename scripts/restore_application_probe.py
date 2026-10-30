"""Run inside the one-shot application container, against the restored DB only."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
import secrets


class ProbeFailure(Exception):
    pass


def require(condition, check):
    if not condition:
        raise ProbeFailure(check)


def run_probe(*, allow_sqlite=False):
    logging.disable(logging.CRITICAL)
    from sqlalchemy import MetaData, func, inspect, select, text
    from sqlalchemy.engine import make_url
    url = make_url(os.environ["MONIPAN_DATABASE_URL"])
    sqlite = url.drivername.startswith("sqlite")
    require((allow_sqlite and sqlite and os.environ.get("MONIPAN_ENVIRONMENT") == "test")
            or (url.drivername == "mysql+pymysql" and url.host == "127.0.0.1"
                and url.port == 3306 and url.database == "monipan_restore"), "database_target")
    require(os.environ.get("MONIPAN_DISABLE_MARKET_LOOP") == "1", "background_tasks_disabled")
    require(os.environ.get("MONIPAN_COOKIE_SECURE") == "1", "secure_cookie_configuration")
    from app.database import SessionLocal, engine
    from app.models import AuthSession, InvitationCode
    from app.auth import SESSION_COOKIE_NAME, hash_invite_code, hash_token
    metadata = MetaData()
    metadata.reflect(bind=engine)
    original_limits, digests, counts = {}, {}, {}
    runtime_tables = {"auth_sessions", "rate_limit_counters", "rate_limit_leases"}

    def digest_table(connection, table, limit):
        key = list(table.primary_key.columns)[0]
        query = select(table).where(key <= limit) if limit is not None else select(table).where(False)
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(query.order_by(key)):
            digest.update(json.dumps(list(row), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        return count, digest.hexdigest()

    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            require(len(table.primary_key.columns) == 1, "single_primary_key:" + name)
            key = list(table.primary_key.columns)[0]
            limit = connection.scalar(select(func.max(key)))
            original_limits[name] = limit
            counts[name], digests[name] = digest_table(connection, table, limit)
        expected = json.loads(os.environ.get("MONIPAN_RESTORE_COUNTS_JSON", "{}"))
        require(not expected or counts == expected, "restored_table_counts_unchanged_since_import")
        foreign_keys = 0
        inspector = inspect(engine)
        quote = engine.dialect.identifier_preparer.quote
        for table in inspector.get_table_names():
            for fk in inspector.get_foreign_keys(table):
                require(fk.get("referred_schema") in (None, url.database), "foreign_key_schema")
                pairs = list(zip(fk["constrained_columns"], fk["referred_columns"]))
                require(bool(pairs), "foreign_key_columns")
                join = " AND ".join(f"c.{quote(left)}=p.{quote(right)}" for left, right in pairs)
                present = " AND ".join(f"c.{quote(left)} IS NOT NULL" for left, _ in pairs)
                sql = (f"SELECT COUNT(*) FROM {quote(table)} c LEFT JOIN {quote(fk['referred_table'])} p "
                       f"ON {join} WHERE {present} AND p.{quote(pairs[0][1])} IS NULL")
                require(connection.scalar(text(sql)) == 0, "foreign_key_orphan:" + table)
                foreign_keys += 1
        accounts = [dict(row) for row in connection.execute(text("SELECT * FROM simulation_accounts")).mappings()]
        users = {row["id"]: dict(row) for row in connection.execute(text("SELECT * FROM users")).mappings()}
        stocks = {row["id"]: dict(row) for row in connection.execute(text("SELECT * FROM stocks")).mappings()}
        positions = [dict(row) for row in connection.execute(text("SELECT * FROM positions")).mappings()]
        orders = [dict(row) for row in connection.execute(text("SELECT * FROM orders")).mappings()]
        trades = [dict(row) for row in connection.execute(text("SELECT * FROM trades")).mappings()]
        transactions = [dict(row) for row in connection.execute(text(
            "SELECT * FROM account_transactions ORDER BY created_at, id")).mappings()]
        runs = [dict(row) for row in connection.execute(text("SELECT id,user_id FROM agent_runs")).mappings()]

    dec = lambda value: Decimal(str(value if value is not None else 0))
    cash_by_account, cash_by_order = defaultdict(Decimal), defaultdict(Decimal)
    traded_cash, traded_cash_by_order = defaultdict(Decimal), defaultdict(Decimal)
    quantities, position_quantities = defaultdict(int), {}
    account_by_id = {account["id"]: account for account in accounts}
    running = {account["id"]: dec(account["initial_cash"]) for account in accounts}
    order_by_id = {order["id"]: order for order in orders}
    for entry in transactions:
        require(entry["account_id"] in running, "transaction_account")
        amount = dec(entry["amount"])
        cash_by_account[entry["account_id"]] += amount
        running[entry["account_id"]] += amount
        require(running[entry["account_id"]] == dec(entry["balance_after"]), "transaction_running_balance")
        if entry["order_id"] is not None:
            cash_by_order[(entry["account_id"], entry["order_id"])] += amount
    for trade in trades:
        require(trade["side"] in ("BUY", "SELL"), "trade_side")
        order = order_by_id[trade["order_id"]]
        require(all(order[field] == trade[field] for field in ("account_id", "stock_id", "side")),
                "trade_order_ownership")
        amount, fee = dec(trade["amount"]), dec(trade["fee"])
        cash = -amount - fee if trade["side"] == "BUY" else amount - fee
        traded_cash[trade["account_id"]] += cash
        traded_cash_by_order[(trade["account_id"], trade["order_id"])] += cash
        quantities[(trade["account_id"], trade["stock_id"])] += (
            trade["quantity"] if trade["side"] == "BUY" else -trade["quantity"])
    require(dict(cash_by_order) == dict(traded_cash_by_order), "transaction_trade_cash_by_order")
    for account in accounts:
        actual = dec(account["available_cash"])
        require(actual == dec(account["initial_cash"]) + cash_by_account[account["id"]], "account_cash_ledger")
        require(actual == dec(account["initial_cash"]) + traded_cash[account["id"]], "account_cash_trades")
    for position in positions:
        key = (position["account_id"], position["stock_id"])
        position_quantities[key] = position["quantity"]
    require(all(position_quantities.get(key, 0) == quantities.get(key, 0)
                for key in set(position_quantities) | set(quantities)), "position_quantity_replay")

    # Synthetic credentials exist only in the isolated clone. Restored users are
    # read through short-lived test sessions; their password hashes stay intact.
    from app.main import app
    from fastapi.testclient import TestClient
    username = "restore_probe_" + secrets.token_hex(5)
    password = "Restore9_" + secrets.token_hex(20)
    invite = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        db.add(InvitationCode(code_hash=hash_invite_code(invite), label="isolated_restore_probe",
                              expires_at=now + timedelta(hours=1)))
        db.commit()
    original_sessions = {}
    for account in accounts:
        user = users[account["user_id"]]
        if user["status"] != "ACTIVE":
            continue
        token = secrets.token_urlsafe(48)
        with SessionLocal() as db:
            db.add(AuthSession(user_id=user["id"], token_hash=hash_token(token),
                               expires_at=now + timedelta(hours=1)))
            db.commit()
        original_sessions[user["id"]] = token

    def session(client, token):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, token)

    def checked(client, path, status=200):
        response = client.get(path)
        require(response.status_code == status, "GET:" + path + ":status=" + str(response.status_code))
        return response.json()

    read_accounts, checked_runs = 0, 0
    with TestClient(app, base_url="https://restore.test", headers={"X-Real-IP": "127.0.0.251"}) as client:
        checked(client, "/livez")
        checked(client, "/readyz")
        checked(client, "/api/account", 401)
        registration = client.post("/api/auth/register", json={
            "username": username, "password": password, "invite_code": invite})
        require(registration.status_code == 201, "synthetic_registration")
        cookie = registration.headers.get("set-cookie", "").lower()
        require("secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie,
                "synthetic_cookie_attributes")
        require(client.post("/api/auth/logout").status_code == 204, "synthetic_registration_logout")
        require(client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200,
                "synthetic_login")
        synthetic_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        require(checked(client, "/api/auth/me")["username"] == username, "synthetic_identity")
        require(checked(client, "/api/orders") == [] and checked(client, "/api/trades") == [],
                "synthetic_account_isolation")
        for account in accounts:
            user = users[account["user_id"]]
            if user["id"] not in original_sessions:
                continue
            session(client, original_sessions[user["id"]])
            require(checked(client, "/api/auth/me")["username"] == user["username"], "restored_identity")
            summary = checked(client, "/api/account")
            require(summary["username"] == user["username"]
                    and dec(summary["available_cash"]) == dec(account["available_cash"]), "restored_cash_read")
            expected_positions = {stocks[p["stock_id"]]["symbol"]: p["quantity"] for p in positions
                                  if p["account_id"] == account["id"] and p["quantity"] > 0}
            actual_positions = {item["symbol"]: item["quantity"] for item in checked(client, "/api/positions")}
            require(actual_positions == expected_positions, "restored_positions_and_isolation")
            for path, rows, key in (("/api/orders", orders, "order_no"), ("/api/trades", trades, "trade_no")):
                expected_keys = {row[key] for row in rows if row["account_id"] == account["id"]}
                actual = checked(client, path)
                require(len(actual) == min(len(expected_keys), 100)
                        and {item[key] for item in actual} <= expected_keys, "restored_records_and_isolation:" + path)
            read_accounts += 1
        for run in runs:
            if run["user_id"] not in original_sessions:
                continue
            session(client, original_sessions[run["user_id"]])
            checked(client, "/api/agent/runs/" + str(run["id"]))
            session(client, synthetic_cookie)
            checked(client, "/api/agent/runs/" + str(run["id"]), 404)
            checked_runs += 1
        session(client, synthetic_cookie)
        require(client.post("/api/auth/logout").status_code == 204, "synthetic_login_logout")
        checked(client, "/api/auth/me", 401)
    require(read_accounts > 0, "restored_active_account_coverage")
    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            if name in runtime_tables:
                continue  # Authentication may expire sessions and quota buckets.
            count, digest = digest_table(connection, table, original_limits[name])
            require(count == counts[name] and digest == digests[name], "original_rows_changed:" + name)
    engine.dispose()
    return {"status": "passed", "restored_accounts_read": read_accounts,
            "inactive_accounts_skipped": len(accounts) - read_accounts, "agent_runs_read_and_isolated": checked_runs,
            "foreign_keys_checked": foreign_keys, "cash_ledger_reconciled_accounts": len(accounts),
            "position_quantity_replay": "passed", "synthetic_register_login_logout": "passed",
            "original_tables_fingerprinted": len(counts) - len(runtime_tables & counts.keys()),
            "runtime_tables_excluded_from_immutability": sorted(runtime_tables & counts.keys()),
            "original_rows_unchanged": True,
            "original_user_passwords_tested": False, "browser_ui_tested": False,
            "note": "Application API smoke and clone ledger checks; no full-host disaster recovery claim"}


if __name__ == "__main__":
    try:
        print("RESTORE_APPLICATION_RESULT=" + json.dumps(run_probe(), ensure_ascii=True))
    except Exception as exc:
        print("RESTORE_APPLICATION_RESULT=" + json.dumps({
            "status": "failed", "error_type": type(exc).__name__,
            "check": str(exc) if isinstance(exc, ProbeFailure) else "Private diagnostic details omitted"}))
        raise SystemExit(1)
