"""Real schema creation/adoption tests; MySQL rehearsal runs on the server clone."""

import json

from alembic import command
import pytest
from sqlalchemy import CheckConstraint, create_engine, event, inspect, select, text
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app import models
from app.migrations.check_expressions import expression_tree
from app.migrations.frozen_v04 import metadata
from app.schema import (BASELINE, SchemaStateError, adopt_baseline, baseline_check, configuration,
                        current_revisions, prepare_database, require_current, upgrade_database)
from app.schema_audit import model_schema, audit


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_frozen_baseline_matches_v04_for_both_supported_dialects():
    frozen = metadata()
    for dialect in (mysql.dialect(), sqlite.dialect()):
        current = model_schema(Base.metadata, dialect)
        current.pop("security_catalog_entries")
        current["stocks"]["columns"].pop("research_only")
        assert model_schema(frozen, dialect) == current
    assert len(frozen.tables) == 31


def test_empty_database_upgrades_to_a_checked_head_and_repeated_upgrade_is_safe(database):
    upgrade_database(database)
    upgrade_database(database)
    with database.connect() as connection:
        require_current(connection)
        report = audit(connection, model_schema(Base.metadata, connection.dialect))
        assert current_revisions(connection) == ("20261026_02",)
    assert report["differences"] == []


def test_existing_business_data_adoption_only_writes_version_metadata(database):
    metadata().create_all(database)
    with Session(database) as session:
        session.add(models.User(username="migration_fixture", status="ACTIVE"))
        session.commit()
    statements = []
    event.listen(database, "before_cursor_execute", lambda _c, _u, statement, *_a: statements.append(statement))
    first = adopt_baseline(database)
    repeat = adopt_baseline(database)  # A repeat verifies the baseline without rewriting it.
    assert first["schema_modified"] is first["version_metadata_written"] is True
    assert first["business_schema_modified"] is False
    assert repeat["schema_modified"] is repeat["version_metadata_written"] is False
    writes = [item.strip().upper() for item in statements
              if item.strip().split()[0].upper() in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE"}]
    assert writes and all("ALEMBIC_VERSION" in item for item in writes)
    with Session(database) as session:
        assert session.scalars(select(models.User.username)).all() == ["migration_fixture"]


def test_stage1_upgrade_preserves_every_original_column_and_cash_position_order_trade(tmp_path):
    # Use the meaningful two-account ledger fixture, strip the Stage 1 additions
    # to reproduce a versioned production baseline, then compare all old columns.
    import ast
    from pathlib import Path
    from sqlalchemy import MetaData
    tree = ast.parse(Path(__file__).with_name("test_restore_application.py").read_text(encoding="utf-8"))
    source = next(ast.literal_eval(item.value) for item in tree.body if isinstance(item, ast.Assign)
                  and any(isinstance(n, ast.Name) and n.id == "FIXTURE" for n in item.targets))
    source = source.replace("upgrade_database(engine)\n", "Base.metadata.create_all(engine)\n")
    # The fixture imports global configuration, so run it against this test's
    # engine by substituting only its database import.
    database = create_engine("sqlite:///" + (tmp_path / "baseline.db").as_posix())
    source = source.replace("from app.database import Base, engine, SessionLocal", "")
    exec(source, {"Base": Base, "engine": database, "SessionLocal": sessionmaker(bind=database)})
    with database.begin() as connection:
        connection.exec_driver_sql("DROP TABLE security_catalog_entries")
        connection.exec_driver_sql("ALTER TABLE stocks DROP COLUMN research_only")
    frozen = MetaData()
    frozen.reflect(bind=database)
    def snapshot():
        with database.connect() as connection:
            return {name: connection.execute(select(table).order_by(*table.primary_key.columns)).all()
                    for name, table in frozen.tables.items()}
    before = snapshot()
    adopt_baseline(database)
    upgrade_database(database)
    upgrade_database(database)
    assert snapshot() == before
    with database.connect() as connection:
        require_current(connection)
        assert connection.scalar(text("SELECT research_only FROM stocks LIMIT 1")) == 0
        assert audit(connection, model_schema(Base.metadata, connection.dialect))["differences"] == []
    database.dispose()


def test_unversioned_existing_database_cannot_accidentally_run_initial_upgrade(database):
    metadata().create_all(database)
    with pytest.raises(SchemaStateError, match="adopt-baseline"):
        upgrade_database(database)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_column_drift_prevents_version_creation(database):
    metadata().create_all(database)
    with database.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users DROP COLUMN password_hash")
    with pytest.raises(SchemaStateError, match="Baseline check failed"):
        adopt_baseline(database)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_missing_check_constraint_prevents_version_creation(database):
    frozen = metadata()
    table = frozen.tables["positions"]
    table.constraints.remove(next(item for item in table.constraints if isinstance(item, CheckConstraint)))
    frozen.create_all(database)
    with pytest.raises(SchemaStateError, match="Baseline check failed"):
        adopt_baseline(database)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_unknown_view_requires_review_and_is_preserved(database):
    metadata().create_all(database)
    with database.begin() as connection:
        connection.exec_driver_sql("CREATE VIEW migration_fixture_view AS SELECT id FROM users")
    with pytest.raises(SchemaStateError):
        adopt_baseline(database)
    assert inspect(database).get_view_names() == ["migration_fixture_view"]


def test_production_startup_cannot_initialize_or_adopt_a_database(database):
    with pytest.raises(SchemaStateError):
        prepare_database(database, production=True)
    assert inspect(database).get_table_names() == []
    metadata().create_all(database)
    with pytest.raises(SchemaStateError):
        prepare_database(database, production=True)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_empty_development_database_uses_the_versioned_baseline(database):
    prepare_database(database, production=False)
    with database.connect() as connection:
        require_current(connection)


def test_baseline_downgrade_cannot_delete_business_tables(database):
    upgrade_database(database)
    with database.begin() as connection:
        with pytest.raises(RuntimeError, match="data-preserving rollback"):
            command.downgrade(configuration(connection, "upgrade"), "base")
    assert len(inspect(database).get_table_names()) == 33
    with database.connect() as connection:
        require_current(connection)


def test_raw_stamp_command_cannot_bypass_the_preflight(database):
    metadata().create_all(database)
    with database.begin() as connection:
        with pytest.raises(RuntimeError, match="required preflight"):
            command.stamp(configuration(connection), BASELINE)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_readiness_detects_version_drift_without_exposing_connection_details(database, monkeypatch):
    from app import main
    upgrade_database(database)
    with database.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='older_fixture'"))
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=database))
    response = main.readiness()
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "unavailable", "database": "ok", "schema": "out_of_date"}
    assert main.liveness() == {"status": "ok"}


@pytest.mark.parametrize("left,right", [
    ("quantity >= 0", "(`quantity` >= 0)"),
    ("status IN ('LOW', 'HIGH')", "(`status` in (_utf8mb4'LOW',_utf8mb4'HIGH'))"),
    ("a <> 'X' OR b IS NOT NULL", "((`a` != 'X') or (`b` is not null))"),
    ("a >= 0 AND b >= 0 AND c >= 0", "((`a` >= 0) and (`b` >= 0) and (`c` >= 0))"),
])
def test_check_expression_comparison_preserves_mysql_semantics(left, right):
    assert expression_tree(left) == expression_tree(right)


def test_check_expression_comparison_does_not_erase_boolean_precedence():
    assert expression_tree("(a=1 OR b=2) AND c=3") != expression_tree("a=1 OR (b=2 AND c=3)")
    with pytest.raises(ValueError):
        expression_tree("UNKNOWN_FUNCTION(a) > 1")
