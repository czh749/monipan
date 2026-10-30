"""Check the new schema code inside the owned, disposable restored database."""

import hashlib
import json
import os


def run_migration_probe(*, allow_sqlite=False):
    from sqlalchemy import MetaData, create_engine, inspect, select, text
    from sqlalchemy.engine import make_url
    from app.database import engine
    from app.schema import BASELINE, adopt_baseline, baseline_check, head_revision, require_current, upgrade_database
    from restore_application_probe import run_probe

    if head_revision() != BASELINE:
        raise RuntimeError("Stage 0 probe supports the frozen baseline only; use the Stage 1 migration rehearsal")

    url = make_url(os.environ["MONIPAN_DATABASE_URL"])
    sqlite = url.drivername.startswith("sqlite")
    if not ((allow_sqlite and sqlite and os.environ.get("MONIPAN_ENVIRONMENT") == "test")
            or (url.drivername == "mysql+pymysql" and url.host == "127.0.0.1" and url.port == 3306
                and url.database == "monipan_restore")):
        raise RuntimeError("Migration rehearsal requires the isolated restored database")
    if os.environ.get("MONIPAN_DISABLE_MARKET_LOOP") != "1":
        raise RuntimeError("Migration rehearsal requires disabled background tasks")

    def business_snapshot():
        metadata = MetaData()
        metadata.reflect(bind=engine)
        result = {}
        with engine.connect() as connection:
            for name, table in metadata.tables.items():
                if name == "alembic_version":
                    continue
                keys = list(table.primary_key.columns)
                if not keys:
                    raise RuntimeError("Business fingerprint requires a primary key")
                digest = hashlib.sha256()
                count = 0
                for row in connection.execute(select(table).order_by(*keys)):
                    digest.update(json.dumps(list(row), ensure_ascii=True, default=str,
                                             sort_keys=True).encode("utf-8") + b"\n")
                    count += 1
                result[name] = (count, digest.hexdigest())
        return result

    before = business_snapshot()
    with engine.connect() as connection:
        if "alembic_version" in inspect(connection).get_table_names():
            raise RuntimeError("Rehearsal expects an unversioned backup")
        report = baseline_check(connection)
    if not report["ready_to_stamp"]:
        return {"status": "failed", "stage": "baseline_preflight", "baseline_report": report}
    adopt_baseline(engine)
    upgrade_database(engine)  # At head: no business DDL should be performed.
    with engine.connect() as connection:
        require_current(connection)
    if business_snapshot() != before:
        raise RuntimeError("Business rows changed during baseline adoption")

    # Create the empty-schema case only in the same owned disposable MySQL.
    if sqlite:
        empty = create_engine("sqlite://")
    else:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "CREATE DATABASE monipan_baseline_fresh CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            connection.commit()
        empty = create_engine(url.set(database="monipan_baseline_fresh"))
    try:
        upgrade_database(empty)
        with empty.connect() as connection:
            empty_report = baseline_check(connection)
            require_current(connection)
        if not empty_report["ready_to_stamp"]:
            return {"status": "failed", "stage": "fresh_database_upgrade", "baseline_report": empty_report}
    finally:
        empty.dispose()

    expected_counts = json.loads(os.environ.get("MONIPAN_RESTORE_COUNTS_JSON", "{}"))
    if expected_counts:
        expected_counts["alembic_version"] = 1
        os.environ["MONIPAN_RESTORE_COUNTS_JSON"] = json.dumps(expected_counts)
    application = run_probe(allow_sqlite=allow_sqlite)
    return {"status": "passed", "dialect": engine.dialect.name, "baseline_revision": BASELINE,
            "existing_database_adopted": True,
            "business_tables_fingerprinted": len(before), "business_rows_unchanged_by_adoption": True,
            "supporting_indexes_classified": report["supporting_indexes_classified"],
            "check_constraints_verified": report["check_constraints_inspected"],
            "fresh_database_upgrade": "passed", "application": application,
            "production_schema_modified": False}
