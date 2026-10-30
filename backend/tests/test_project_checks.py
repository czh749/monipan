"""The check runner must fail closed and keep deployment configuration out of tests."""

import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("project_checks", ROOT / "scripts/check_project.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def test_environment_excludes_live_database_and_credentials(tmp_path, monkeypatch):
    for name in ("MONIPAN_DATABASE_URL", "MONIPAN_AGENT_API_KEY", "MONIPAN_STAGE1_MYSQL_URL",
                 "COMPOSE_FILE", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.setenv(name, "private-sentinel")
    env = checks.test_environment(tmp_path)
    assert "private-sentinel" not in env.values()
    assert env["MONIPAN_DATABASE_URL"].startswith("sqlite:///")
    assert env["MONIPAN_DISABLE_MARKET_LOOP"] == "1"
    assert os.environ["MONIPAN_DATABASE_URL"] == "private-sentinel"


@pytest.mark.parametrize("body", ["", '<testcase name="x"><skipped message="missing MySQL"/></testcase>'])
def test_mysql_report_without_any_pass_is_failure(tmp_path, body):
    path = tmp_path / "results.xml"
    path.write_text("<testsuite>" + body + "</testsuite>")
    assert checks.junit_result(path, require_all=True)["ok"] is False


def test_ci_only_allows_explicit_historical_archive_skips(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text('<testsuite><testcase name="pass"/><testcase name="skip"><skipped '
                    'message="Historical release artifact is not present: fixture"/></testcase></testsuite>')
    assert checks.junit_result(path, strict_skips=True)["ok"] is True
    assert checks.junit_result(path, require_all=True)["ok"] is False
    path.write_text('<testsuite><testcase name="pass"/><testcase name="skip"><skipped '
                    'message="Nginx is absent"/></testcase></testsuite>')
    assert checks.junit_result(path)["ok"] is True
    assert checks.junit_result(path, strict_skips=True)["ok"] is False


def test_mysql_request_cannot_fall_back_to_offline_tests(tmp_path, monkeypatch):
    monkeypatch.delenv("MONIPAN_STAGE1_MYSQL_URL", raising=False)
    monkeypatch.setattr(checks, "run_command", lambda *a, **k: pytest.fail("Must refuse before running tests"))
    assert checks.check_pytest("mysql", tmp_path, {})["status"] == "failed"
    monkeypatch.setenv("MONIPAN_STAGE1_MYSQL_URL", "mysql+pymysql://u:private@mysql/monipan")
    result = checks.check_pytest("mysql", tmp_path, {})
    assert result["status"] == "failed" and "private" not in json.dumps(result)


def test_nonzero_exit_cannot_be_hidden_by_a_passing_xml_report(tmp_path, monkeypatch):
    (tmp_path / "backend.xml").write_text('<testsuite><testcase name="passed"/></testsuite>')
    monkeypatch.setattr(checks, "run_command", lambda *a, **k: 1)
    assert checks.check_pytest("backend", tmp_path, {})["status"] == "failed"


def test_changed_requirements_cannot_reuse_an_old_lock(tmp_path, monkeypatch):
    import hashlib
    folder = tmp_path / "backend"
    folder.mkdir()
    source = folder / "requirements.txt"
    source.write_bytes(b"fixture==1\r\n")
    digest = hashlib.sha256(b"fixture==1\n").hexdigest()
    (folder / "requirements.lock").write_text("# requirements.txt sha256: " + digest + "\n")
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    assert checks.backend_lock_matches_source()
    source.write_bytes(b"fixture==2\n")
    assert not checks.backend_lock_matches_source()
    monkeypatch.setattr(checks, "run_command", lambda *a, **k: pytest.fail("Must fail before running tests"))
    assert checks.check_pytest("backend", tmp_path, {})["status"] == "failed"
