"""Installer failure-path checks; real systemd execution is verified on Linux."""

import gzip
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("backup_timer", ROOT / "scripts/install_backup_timer.py")
timer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timer)


@pytest.fixture
def host(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts/backup_mysql.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (project / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    unit_dir = tmp_path / "system"
    unit_dir.mkdir()
    units = {name: (ROOT / "deploy/systemd" / name).read_text(encoding="utf-8") for name in timer.NAMES}
    calls = []
    behavior = {"backup": "good", "fail": None}
    private_files = set()
    # Windows chmod cannot express Unix 0600. Emulate only those permission
    # bits for fixture backup files; all bytes, CRCs and timestamps remain real.
    if os.name == "nt":
        original_stat = Path.stat

        def linux_private_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path in private_files:
                fields = list(result)
                fields[0] = (fields[0] & ~0o777) | 0o600
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(Path, "stat", linux_private_stat)

    def runner(args):
        calls.append(args)
        if behavior["fail"] and args[:len(behavior["fail"])] == behavior["fail"]:
            raise RuntimeError("simulated command failure")
        if args[:2] == ["systemctl", "start"]:
            if behavior["backup"] == "missing":
                return ""
            backup = project / "backups/daily/monipan-fixture.sql.gz"
            payload = gzip.compress(b"-- fixture SQL\n")
            backup.write_bytes(payload)
            checksum = backup.with_name(backup.name + ".sha256")
            digest = "0" * 64 if behavior["backup"] == "corrupt" else hashlib.sha256(payload).hexdigest()
            checksum.write_text(digest + "  " + backup.name + "\n", encoding="ascii")
            for path in (backup, checksum):
                path.chmod(0o600)
                private_files.add(path)
            if behavior["backup"] == "stale":
                os.utime(backup, (1, 1))
        if args[0] == "docker" and "ps" in args:
            return "a" * 64
        if args[:2] == ["docker", "inspect"]:
            return "true"
        if args[:2] == ["systemctl", "show"]:
            return "Result=success\nExecMainStatus=0"
        if args[:2] == ["systemctl", "is-enabled"]:
            return "enabled"
        if args[:2] == ["systemctl", "is-active"]:
            return "active"
        return ""

    monkeypatch.setattr(timer, "run", runner)
    monkeypatch.setattr(timer.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout="not-found\n", stderr=""))
    previous_mask = os.umask(0o077)
    try:
        yield project, unit_dir, units, calls, behavior
    finally:
        os.umask(previous_mask)


def execute(host):
    project, unit_dir, units, _, _ = host
    timer.install(units, project=project, unit_dir=unit_dir)


def timer_enabled(calls):
    return ["systemctl", "enable", "--now", "monipan-backup.timer"] in calls


def test_first_backup_is_verified_before_timer_enablement(host, capsys):
    execute(host)
    project, unit_dir, units, calls, _ = host
    assert timer_enabled(calls)
    assert calls.index(["systemctl", "start", "monipan-backup.service"]) < calls.index(
        ["systemctl", "enable", "--now", "monipan-backup.timer"])
    assert all((unit_dir / name).read_text(encoding="utf-8") == source for name, source in units.items())
    assert (project / "backups/daily/monipan-fixture.sql.gz").is_file()
    assert "DAILY_BACKUP_INSTALLED" in capsys.readouterr().out


def test_existing_unit_is_never_overwritten(host):
    _, unit_dir, _, calls, _ = host
    path = unit_dir / "monipan-backup.timer"
    path.write_text("existing configuration", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Existing unit"):
        execute(host)
    assert path.read_text(encoding="utf-8") == "existing configuration"
    assert calls == []


def test_unit_validation_failure_has_no_installation_side_effects(host):
    project, unit_dir, _, calls, behavior = host
    behavior["fail"] = ["systemd-analyze", "verify"]
    with pytest.raises(RuntimeError, match="simulated"):
        execute(host)
    assert not list(unit_dir.iterdir())
    assert not (project / "backups/daily").exists()
    assert not timer_enabled(calls)


def test_first_service_failure_never_enables_the_timer(host):
    _, _, _, calls, behavior = host
    behavior["fail"] = ["systemctl", "start"]
    with pytest.raises(RuntimeError, match="simulated"):
        execute(host)
    assert not timer_enabled(calls)


@pytest.mark.parametrize("backup", ["missing", "corrupt", "stale"])
def test_missing_corrupt_or_old_backup_prevents_enablement(host, backup):
    _, _, _, calls, behavior = host
    behavior["backup"] = backup
    with pytest.raises(RuntimeError):
        execute(host)
    assert not timer_enabled(calls)


def test_only_installing_service_files_does_not_count_as_success(host, capsys):
    _, _, _, calls, behavior = host
    behavior["fail"] = ["systemctl", "enable"]
    with pytest.raises(RuntimeError, match="simulated"):
        execute(host)
    assert "DAILY_BACKUP_INSTALLED" not in capsys.readouterr().out
    assert ["systemctl", "start", "monipan-backup.service"] in calls
