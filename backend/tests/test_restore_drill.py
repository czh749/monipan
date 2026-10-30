"""Offline safety tests; real MySQL import is verified on the server separately."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "scripts/restore_mysql_drill.py"
spec = importlib.util.spec_from_file_location("restore_drill", SOURCE)
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def backup_file(tmp_path, content=b"-- harmless backup fixture\n"):
    path = tmp_path / "monipan-fixture.sql.gz"
    path.write_bytes(gzip.compress(content))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(digest + "  " + path.name + "\n", encoding="utf-8")
    return path


def containers():
    source = {"Id": "a" * 64, "Image": "sha256:" + "c" * 64,
              "Config": {"Labels": {"com.docker.compose.project": "monipan",
                                      "com.docker.compose.service": "mysql"}},
              "State": {"Running": True},
              "Mounts": [{"Type": "volume", "Source": "/production/mysql", "Destination": "/var/lib/mysql"}]}
    target = {"Id": "b" * 64, "Image": source["Image"],
              "Config": {"Labels": {drill.LABEL: "fixture-token"}},
              "State": {"Running": True},
              "HostConfig": {"NetworkMode": "none", "PortBindings": {}, "Memory": drill.MEMORY,
                             "MemorySwap": drill.MEMORY, "NanoCpus": 750_000_000, "Privileged": False},
              "Mounts": [{"Type": "volume", "Source": "/temporary/mysql", "Destination": "/var/lib/mysql"}]}
    return source, target


@pytest.mark.parametrize("alter", [
    lambda target, source: target.update(Id=source["Id"]),
    lambda target, source: target["HostConfig"].update(NetworkMode="monipan_default"),
    lambda target, source: target["HostConfig"].update(PortBindings={"3306/tcp": [{"HostPort": "3306"}]}),
    lambda target, source: target["HostConfig"].update(Memory=0),
    lambda target, source: target["HostConfig"].update(NanoCpus=0),
    lambda target, source: target["HostConfig"].update(Privileged=True),
    lambda target, source: target["Mounts"][0].update(Source=source["Mounts"][0]["Source"]),
    lambda target, source: target["Config"]["Labels"].update({drill.LABEL: "someone-else"}),
    lambda target, source: target["Config"]["Labels"].update({"com.docker.compose.project": "monipan"}),
])
def test_unsafe_restore_target_is_rejected(alter):
    source, target = containers()
    alter(target, source)
    with pytest.raises(RuntimeError):
        drill.validate_target(target, source, "fixture-token")


@pytest.mark.parametrize("damage", ["checksum", "filename", "gzip"])
def test_invalid_backup_does_not_start_any_container(tmp_path, monkeypatch, damage):
    path = backup_file(tmp_path)
    if damage == "checksum":
        path.write_bytes(path.read_bytes() + b"changed")
    elif damage == "filename":
        checksum = Path(str(path) + ".sha256")
        checksum.write_text(checksum.read_text().replace(path.name, "another.sql.gz"))
    else:
        path = backup_file(tmp_path, b"example")
        path.write_bytes(b"not-a-gzip")
        Path(str(path) + ".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
    calls = []
    monkeypatch.setattr(drill, "command", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(Exception):
        drill.restore(path, tmp_path)
    assert calls == []


class FakeDocker:
    def __init__(self, unsafe=False, missing_core=False, import_failure=False):
        self.source, self.target = containers()
        self.unsafe = unsafe
        self.missing_core = missing_core
        self.import_failure = import_failure
        self.calls = []
        self.imports = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["compose", "ps"]:
            return self.source["Id"]
        if args[1] == "inspect":
            return json.dumps([self.source if args[2] == self.source["Id"] else self.target])
        if args[1] == "run":
            token = args[args.index("--label") + 1].split("=", 1)[1]
            self.target["Config"]["Labels"][drill.LABEL] = token
            assert kwargs["env"]["MYSQL_ROOT_PASSWORD"] not in args
            assert args[args.index("--pull=never")] == "--pull=never"
            assert self.source["Image"] in args
            if self.unsafe:
                self.target["Mounts"][0]["Source"] = self.source["Mounts"][0]["Source"]
            return self.target["Id"]
        if args[1] == "stop":
            assert args[-1] == self.target["Id"]
            return self.target["Id"]
        assert args[1] == "exec" and args[3] == self.target["Id"]
        assert drill.DATABASE in args[-1]
        if kwargs.get("stdin") is not None:
            self.imports.append((args[3], kwargs["stdin"].read()))
            if self.import_failure:
                raise RuntimeError("fixture import failure")
            return ""
        sql = kwargs["input"].decode()
        if sql == "SELECT 1;":
            return "1"
        tables = sorted(drill.CORE_TABLES - ({"users"} if self.missing_core else set()))
        if "information_schema.TABLES" in sql:
            return "\n".join(tables)
        if "CONVERT(0x" in sql:
            return "\n".join(table + "\t1" for table in tables)
        return "\n".join(key + "\t0" for key in ("negative_cash", "negative_position_quantity",
                                                  "accounts_without_user", "trades_without_order"))


def test_import_targets_only_new_container_and_retains_precise_scope(tmp_path, monkeypatch, capsys):
    path = backup_file(tmp_path)
    fake = FakeDocker()
    monkeypatch.setattr(drill, "command", fake)
    drill.restore(path, tmp_path)
    assert fake.imports == [(fake.target["Id"], b"-- harmless backup fixture\n")]
    state = json.loads(next(tmp_path.glob("restore-run-*/restore-state.json")).read_text())
    assert state["status"] == "import_verified_application_pending"
    assert state["table_counts"]["users"] == 1
    assert state["backup_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    output = capsys.readouterr().out
    assert "RESTORE_IMPORT_OK" in output
    assert "MYSQL_ROOT_PASSWORD" not in output
    assert not any(args[1] in ("rm", "restart") for args in fake.calls)


@pytest.mark.parametrize("failure", ["unsafe", "missing_core", "import_failure"])
def test_failure_never_stops_or_writes_to_production(tmp_path, monkeypatch, capsys, failure):
    fake = FakeDocker(**{failure: True})
    monkeypatch.setattr(drill, "command", fake)
    with pytest.raises(RuntimeError):
        drill.restore(backup_file(tmp_path), tmp_path)
    assert all(args[-1] == fake.target["Id"] for args in fake.calls if args[1] == "stop")
    assert not any(args[1] == "rm" for args in fake.calls)
    if failure == "unsafe":
        assert fake.imports == []
    state = json.loads(next(tmp_path.glob("restore-run-*/restore-state.json")).read_text())
    assert state["status"] == "failed"


def test_command_error_does_not_expose_sql_or_credentials(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, b"private-data", b"secret-password"))
    with pytest.raises(RuntimeError) as error:
        drill.command(["docker", "exec", "fixture"])
    assert "private-data" not in str(error.value)
    assert "secret-password" not in str(error.value)
