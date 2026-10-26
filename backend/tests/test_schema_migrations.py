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
from app.schema_audit import model_schema


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_frozen_baseline_matches_v04_for_both_supported_dialects():
    frozen = metadata()
    for dialect in (mysql.dialect(), sqlite.dialect()):
        assert model_schema(frozen, dialect) == model_schema(Base.metadata, dialect)
    assert len(frozen.tables) == 31


def test_empty_database_upgrades_to_a_checked_head_and_repeated_upgrade_is_safe(database):
    upgrade_database(database)
    upgrade_database(database)
    with database.connect() as connection:
        require_current(connection)
        report = baseline_check(connection)
        assert current_revisions(connection) == (BASELINE,)
    assert report["status"] == "baseline_compatible"
    assert report["check_constraints_inspected"] == 14
    assert report["adoption_problems"] == []


def test_existing_business_data_adoption_only_writes_version_metadata(database):
    Base.metadata.create_all(database)
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


def test_unversioned_existing_database_cannot_accidentally_run_initial_upgrade(database):
    Base.metadata.create_all(database)
    with pytest.raises(SchemaStateError, match="adopt-baseline"):
        upgrade_database(database)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_column_drift_prevents_version_creation(database):
    Base.metadata.create_all(database)
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
    Base.metadata.create_all(database)
    with database.begin() as connection:
        connection.exec_driver_sql("CREATE VIEW migration_fixture_view AS SELECT id FROM users")
    with pytest.raises(SchemaStateError):
        adopt_baseline(database)
    assert inspect(database).get_view_names() == ["migration_fixture_view"]


def test_production_startup_cannot_initialize_or_adopt_a_database(database):
    with pytest.raises(SchemaStateError):
        prepare_database(database, production=True)
    assert inspect(database).get_table_names() == []
    Base.metadata.create_all(database)
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
        with pytest.raises(RuntimeError, match="intentionally blocked"):
            command.downgrade(configuration(connection, "upgrade"), "base")
    assert len(inspect(database).get_table_names()) == 32
    with database.connect() as connection:
        require_current(connection)


def test_raw_stamp_command_cannot_bypass_the_preflight(database):
    Base.metadata.create_all(database)
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
