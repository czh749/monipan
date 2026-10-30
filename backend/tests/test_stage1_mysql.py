"""Opt-in rehearsal against an explicitly empty, disposable local MySQL DB.

MONIPAN_STAGE1_MYSQL_URL must name monipan_stage1_test_<id> on loopback.
The caller creates and owns that database; this test refuses any existing table.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from pathlib import Path
import re
from threading import Barrier

from fastapi import HTTPException
import pytest
from sqlalchemy import MetaData, create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Order, SimulationAccount, Stock, Trade, User, WatchlistItem
from app.schema import adopt_baseline, require_current, upgrade_database
from app.schema_audit import audit, model_schema
from app.services.research_watchlist import add_research_watchlist
from app.services.security_catalog import replace_directory, search_directory
from app.services import trading
from .test_security_catalog import records


def test_real_mysql_preserves_baseline_and_serializes_quota_and_shared_creation(monkeypatch, record_testsuite_property):
    raw_url = os.getenv("MONIPAN_STAGE1_MYSQL_URL")
    if not raw_url:
        pytest.skip("Requires an explicitly owned empty local MySQL Stage 1 database")
    url = make_url(raw_url)
    if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1"
            or not re.fullmatch(r"monipan_stage1_test_[a-z0-9_]+", url.database or "")):
        pytest.fail("Refusing a database outside the disposable local Stage 1 namespace")
    engine = create_engine(url, isolation_level="REPEATABLE READ", pool_size=8)
    try:
        inspector = inspect(engine)
        if inspector.get_table_names() or inspector.get_view_names():
            pytest.fail("Refusing a nonempty rehearsal database; all existing rows preserved")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        tree = ast.parse(Path(__file__).with_name("test_restore_application.py").read_text(encoding="utf-8"))
        source = next(ast.literal_eval(item.value) for item in tree.body if isinstance(item, ast.Assign)
                      and any(isinstance(n, ast.Name) and n.id == "FIXTURE" for n in item.targets))
        source = source.replace("upgrade_database(engine)\n", "Base.metadata.create_all(engine)\n")
        source = source.replace("from app.database import Base, engine, SessionLocal", "")
        exec(source, {"Base": Base, "engine": engine, "SessionLocal": factory})
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE security_catalog_entries")
            connection.exec_driver_sql("ALTER TABLE stocks DROP COLUMN research_only")
        original = MetaData()
        original.reflect(bind=engine)
        def snapshot():
            with engine.connect() as connection:
                return {name: connection.execute(select(table).order_by(*table.primary_key.columns)).all()
                        for name, table in original.tables.items()}
        before = snapshot()
        adopt_baseline(engine)
        upgrade_database(engine)
        upgrade_database(engine)
        assert snapshot() == before
        with engine.connect() as connection:
            require_current(connection)
            assert audit(connection, model_schema(Base.metadata, connection.dialect))["differences"] == []
        accounts = []
        with factory() as db:
            for name in ("quota_mysql", "shared_mysql_a", "shared_mysql_b"):
                user = User(username=name, status="ACTIVE")
                db.add(user); db.flush()
                account = SimulationAccount(user_id=user.id)
                db.add(account); db.flush()
                accounts.append(account.id)
            db.commit()
            replace_directory(db, records())
            for i in range(29):
                add_research_watchlist(db, accounts[0], f"688{900+i:03}")

        def race(jobs):
            barrier = Barrier(len(jobs))
            def add(job):
                account_id, symbol = job
                with factory() as db:
                    # Establish an authentication-like consistent read BEFORE
                    # locking. This catches stale REPEATABLE READ quota checks.
                    db.scalar(select(User.id).order_by(User.id).limit(1))
                    barrier.wait(timeout=10)
                    try:
                        add_research_watchlist(db, account_id, symbol)
                        return 201
                    except HTTPException as exc:
                        return exc.status_code
            with ThreadPoolExecutor(len(jobs)) as pool:
                return list(pool.map(add, jobs))

        assert sorted(race([(accounts[0], "688929"), (accounts[0], "688930")])) == [201, 409]
        assert race([(accounts[1], "688931"), (accounts[2], "688931")]) == [201, 201]
        with factory() as db:
            assert search_directory(db, "", accounts[0])["quota"]["used"] == 30
            assert db.scalar(select(func.count()).select_from(Stock).where(Stock.symbol == "688931")) == 1
            stock = db.scalar(select(Stock).where(Stock.symbol == "688931"))
            assert db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.stock_id == stock.id)) == 2
            # A full account can still repeat an existing addition.
            add_research_watchlist(db, accounts[0], "688900")
            now = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
            monkeypatch.setattr(trading, "trading_now", lambda: now)
            stock.price = stock.prev_close = stock.open_price = stock.high_price = stock.low_price = 10
            stock.updated_at = stock.quote_source_at = now.replace(tzinfo=None)
            db.add(Order(order_no="mysql_readonly", stock_id=stock.id, account_id=accounts[1],
                         quantity=100, price=10, limit_price=10, order_type="LIMIT", side="BUY",
                         created_at=now.replace(tzinfo=None)))
            db.commit()
            old_trades = db.scalar(select(func.count()).select_from(Trade))
            for function in (trading.build_order_preview, trading.place_order):
                with pytest.raises(HTTPException) as error:
                    function(db, db.get(SimulationAccount, accounts[1]), stock.symbol, "BUY", 100)
                assert error.value.status_code == 403
            assert trading.match_pending_orders(db) == 0
            assert db.scalar(select(func.count()).select_from(Trade)) == old_trades
        # A bad candidate really fails its startup checks. Recovery uses the
        # upgraded schema with discovery disabled, retaining all trading guards.
        from fastapi.testclient import TestClient
        from app import main
        from app.auth import get_current_account, get_current_user
        from app.database import get_db
        monkeypatch.setattr(main, "engine", engine)
        monkeypatch.setattr(main, "SessionLocal", factory)
        monkeypatch.setenv("MONIPAN_DATABASE_URL", raw_url)
        monkeypatch.setenv("MONIPAN_ENVIRONMENT", "production")
        monkeypatch.setenv("MONIPAN_DISABLE_MARKET_LOOP", "1")
        monkeypatch.setenv("MONIPAN_COOKIE_SECURE", "0")
        protected_tables = {"users", "simulation_accounts", "positions", "orders", "trades",
                            "account_transactions", "watchlist_items", "agent_runs", "agent_evidence",
                            "agent_recommendations"} & set(original.tables)
        def protected_snapshot():
            current = MetaData()
            current.reflect(bind=engine)
            with engine.connect() as connection:
                return {name: connection.execute(select(current.tables[name]).order_by(
                    *current.tables[name].primary_key.columns)).all() for name in protected_tables}
        before_recovery = protected_snapshot()
        with pytest.raises(RuntimeError):
            with TestClient(main.app, base_url="https://testserver"):
                pass
        monkeypatch.setenv("MONIPAN_COOKIE_SECURE", "1")
        monkeypatch.setenv("MONIPAN_RESEARCH_ENABLED", "0")
        previous_overrides = dict(main.app.dependency_overrides)
        def database():
            with factory() as session:
                yield session
        with factory() as db:
            recovered_account = db.get(SimulationAccount, accounts[1])
            recovered_user = db.get(User, recovered_account.user_id)
            recovered_account.user = recovered_user
        main.app.dependency_overrides[get_db] = database
        main.app.dependency_overrides[get_current_user] = lambda: recovered_user
        main.app.dependency_overrides[get_current_account] = lambda: recovered_account
        try:
            with TestClient(main.app, base_url="https://testserver") as client:
                assert client.get("/readyz").status_code == 200
                assert client.get("/api/account").status_code == 200
                assert client.get("/api/securities/search?keyword=688931").status_code == 503
                assert client.post("/api/watchlist/688901").status_code == 503
                assert client.get("/api/stocks").status_code == 200
            assert protected_snapshot() == before_recovery
            with engine.connect() as connection:
                require_current(connection)
            # Exercise the exact read-only probe delivered for server checks.
            import importlib.util
            import contextlib
            import io
            import json
            import app.database as database_module
            probe_path = Path(__file__).resolve().parents[2] / "scripts/stage1_preflight.py"
            spec = importlib.util.spec_from_file_location("stage1_preflight", probe_path)
            preflight = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(preflight)
            monkeypatch.setattr(database_module, "engine", engine)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(preflight.PROBE, {})
            preflight_report = json.loads(output.getvalue())
            assert preflight_report["schema_versions"] == ["20261026_02"]
            assert preflight_report["tables"]["users"]["rows"] == len(before_recovery["users"])
            assert protected_snapshot() == before_recovery
            with factory() as db:
                for function in (trading.build_order_preview, trading.place_order):
                    with pytest.raises(HTTPException) as error:
                        function(db, db.get(SimulationAccount, accounts[1]), "688931", "BUY", 100)
                    assert error.value.status_code == 403
            record_testsuite_property("recovery", "startup_failure_then_maintenance_recovery_verified")
            record_testsuite_property("preserved_tables", ",".join(sorted(protected_tables)))
        finally:
            main.app.dependency_overrides.clear()
            main.app.dependency_overrides.update(previous_overrides)
    finally:
        engine.dispose()
