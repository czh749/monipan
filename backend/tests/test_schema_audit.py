"""Verify a read-only baseline precheck against real disposable SQL databases."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine, event, inspect, text

from app.database import Base
from app import models
from app.schema_audit import audit, model_schema, reject_writes


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("schema_audit_host", ROOT / "scripts/audit_production_schema.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    expected = model_schema(Base.metadata, engine.dialect)
    yield engine, expected
    engine.dispose()


def test_complete_schema_matches_and_cannot_authorize_stamping(database):
    engine, expected = database
    event.listen(engine, "before_cursor_execute", reject_writes)
    with engine.connect() as connection:
        report = audit(connection, expected)
    assert report["status"] == "matches_model_precheck"
    assert report["model_tables"] == report["actual_business_tables"] == 32
    assert report["foreign_keys_inspected"] == 37
    assert report["differences"] == []
    assert report["schema_modified"] is report["business_rows_read"] is report["ready_to_stamp"] is False
    assert "alembic_version" not in inspect(engine).get_table_names()


def test_missing_extra_and_changed_columns_are_reported(database):
    engine, expected = database
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users DROP COLUMN password_hash")
        connection.exec_driver_sql("ALTER TABLE users RENAME COLUMN status TO status_old")
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN status INTEGER")
        report = audit(connection, expected)
    diff = next(item for item in report["differences"] if item.get("table") == "users" and item["kind"] == "columns")
    assert diff["missing"] == ["password_hash"]
    assert diff["extra"] == ["status_old"]
    assert diff["changed"][0]["column"] == "status"


def test_missing_unique_index_is_detected(database):
    engine, expected = database
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_users_username")
        report = audit(connection, expected)
    kinds = {item["kind"] for item in report["differences"] if item.get("table") == "users"}
    assert {"missing_or_changed_indexes", "unique_keys"} <= kinds


def test_version_table_is_inspected_without_becoming_a_business_table(database):
    engine, expected = database
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('baseline_fixture')")
    event.listen(engine, "before_cursor_execute", reject_writes)
    with engine.connect() as connection:
        report = audit(connection, expected)
    assert report["schema_versions"] == ["baseline_fixture"]
    assert report["actual_business_tables"] == 32
    assert report["differences"] == []


@pytest.mark.parametrize("sql", [
    "CREATE TABLE forbidden (id INTEGER)", "ALTER TABLE users ADD COLUMN forbidden INTEGER",
    "DROP TABLE users", "DELETE FROM users", "UPDATE users SET username='forbidden'",
    "INSERT INTO users (username) VALUES ('forbidden')", "PRAGMA user_version=1",
    "SET SESSION TRANSACTION READ WRITE",
])
def test_audit_connection_rejects_writes_before_execution(database, sql):
    engine, _ = database
    event.listen(engine, "before_cursor_execute", reject_writes)
    with engine.connect() as connection:
        with pytest.raises(RuntimeError, match="read-only allowlist"):
            connection.exec_driver_sql(sql)
    assert "users" in inspect(engine).get_table_names()


def mock_host(monkeypatch, service="backend", failed=False):
    calls = []
    identity = {"id": "a" * 64, "running": True,
                "labels": {"com.docker.compose.project": "monipan", "com.docker.compose.service": service}}

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "compose"]:
            return subprocess.CompletedProcess(args, 0, stdout="a" * 64, stderr="")
        if args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(identity), stderr="")
        result = {"status": "error", "error_type": "OperationalError"} if failed else {"status": "matches_model_precheck"}
        return subprocess.CompletedProcess(args, int(failed), stdout=host.PREFIX + json.dumps(result),
                                           stderr="fixture-private-password-that-must-not-print")
    monkeypatch.setattr(host.subprocess, "run", run)
    return calls


def test_wrong_container_identity_blocks_docker_exec(monkeypatch):
    calls = mock_host(monkeypatch, service="mysql")
    with pytest.raises(RuntimeError, match="identity"):
        host.audit_host("", {})
    assert not any(args[:2] == ["docker", "exec"] for args in calls)


def test_backend_error_reports_only_its_safe_type(monkeypatch, capsys):
    mock_host(monkeypatch, failed=True)
    with pytest.raises(RuntimeError, match="OperationalError") as failure:
        host.audit_host("", {})
    assert "private-password" not in str(failure.value) + capsys.readouterr().out


def test_successful_audit_uses_only_inspection_and_one_existing_container_process(monkeypatch, capsys):
    calls = mock_host(monkeypatch)
    host.audit_host("", {})
    assert [args[1] for args in calls] == ["compose", "inspect", "exec"]
    assert "SCHEMA_AUDIT_OK_READ_ONLY" in capsys.readouterr().out
