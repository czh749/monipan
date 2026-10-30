"""Restore a checked backup into a new, network-isolated MySQL container.

Linux server only. Retains the temporary container for application checks.
Never imports into the source Compose service; uses its immutable image ID only.
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time


DATABASE = "monipan_restore"
LABEL = "com.monipan.restore-drill"
MEMORY = 768 * 1024 * 1024
CORE_TABLES = {"users", "simulation_accounts", "stocks", "positions", "orders",
               "trades", "account_transactions"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(arguments, *, cwd=None, env=None, stdin=None, input=None, timeout=30):
    result = subprocess.run(arguments, cwd=cwd, env=env, stdin=stdin, input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(result.returncode == 0,
            f"Command failed: {arguments[0]} {arguments[1]}; exit={result.returncode}. "
            "Raw output omitted to protect credentials and restored data.")
    return result.stdout.decode("utf-8", errors="strict").strip()


def inspect(container):
    return json.loads(command(["docker", "inspect", container]))[0]


def check_backup(path):
    require(path.is_file() and path.name.endswith(".sql.gz"), "Expected an existing .sql.gz backup")
    checksum = Path(str(path) + ".sha256")
    require(checksum.is_file(), "Missing checksum sidecar")
    fields = checksum.read_text(encoding="utf-8").strip().split(maxsplit=1)
    require(len(fields) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]),
            "Invalid checksum sidecar")
    require(fields[1] == path.name, "Checksum sidecar names a different backup")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    require(digest.hexdigest() == fields[0].lower(), "Backup checksum mismatch")
    return digest.hexdigest()


def validate_target(target, source, token):
    require(target["Id"] != source["Id"], "Restore target is the production container")
    require(target["Image"] == source["Image"], "Restore image differs from production")
    require(target["Config"].get("Labels", {}).get(LABEL) == token, "Restore ownership label mismatch")
    require(target["HostConfig"]["NetworkMode"] == "none", "Restore network is not isolated")
    require(not target["HostConfig"].get("PortBindings"), "Restore ports must not be published")
    require(target["HostConfig"].get("Memory") == MEMORY, "Restore memory limit was not applied")
    require(target["HostConfig"].get("MemorySwap") == MEMORY, "Restore swap limit was not applied")
    require(target["HostConfig"].get("NanoCpus") == 750_000_000, "Restore CPU limit was not applied")
    require(not target["HostConfig"].get("Privileged"), "Restore container must not be privileged")
    mounts = target.get("Mounts", [])
    require(len(mounts) == 1 and mounts[0]["Type"] == "volume"
            and mounts[0]["Destination"] == "/var/lib/mysql", "Unexpected restore mounts")
    sources = {mount.get("Source") for mount in source.get("Mounts", [])}
    require(mounts[0].get("Source") and mounts[0]["Source"] not in sources,
            "Restore container shares a production data mount")
    require(not target["Config"].get("Labels", {}).get("com.docker.compose.project"),
            "Restore target unexpectedly belongs to a Compose project")


def identifier(value):
    return "`" + value.replace("`", "``") + "`"


def literal(value):
    return "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)"


def restore(backup, project):
    started = time.monotonic()
    os.umask(0o077)
    backup = backup.resolve(strict=True)
    digest = check_backup(backup)
    output = Path(tempfile.mkdtemp(prefix="restore-run-", dir=backup.parent))
    state_file = output / "restore-state.json"
    state = {"status": "preparing", "backup": str(backup), "backup_sha256": digest,
             "started_at_utc": datetime.now(timezone.utc).isoformat(), "database": DATABASE}
    container = None
    token = secrets.token_hex(12)
    environment = dict(os.environ, COMPOSE_PROJECT_NAME="monipan")
    state["token"] = token

    def save():
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def mysql(*, sql=None, stream=None):
        arguments = ["docker", "exec", "-i", container, "sh", "-c",
                     'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=TCP '
                     '-h127.0.0.1 --connect-timeout=3 -uroot --default-character-set=utf8mb4 '
                     '--batch --skip-column-names ' + DATABASE]
        return command(arguments, stdin=stream,
                       input=sql.encode("utf-8") if sql is not None else None, timeout=180)

    try:
        # Decompress and verify the gzip CRC before starting any database.
        with tempfile.TemporaryFile(dir=output) as sql_file:
            with gzip.open(backup, "rb") as compressed:
                shutil.copyfileobj(compressed, sql_file)
            require(sql_file.tell() > 0, "Backup decompressed to an empty file")
            sql_file.seek(0)
            source_id = command(["docker", "compose", "ps", "-q", "mysql"], cwd=project, env=environment)
            require(bool(re.fullmatch(r"[0-9a-f]{12,64}", source_id)), "Expected one production MySQL container")
            source = inspect(source_id)
            labels = source["Config"].get("Labels", {})
            require(labels.get("com.docker.compose.project") == "monipan"
                    and labels.get("com.docker.compose.service") == "mysql", "Unexpected source Compose labels")
            require(source["State"]["Running"], "Production MySQL is not running")
            state.update(source_container=source["Id"], mysql_image=source["Image"])
            save()
            name = "monipan-restore-drill-" + token
            child_env = dict(os.environ, MYSQL_ROOT_PASSWORD=secrets.token_hex(32))
            container = command([
                "docker", "run", "-d", "--pull=never", "--name", name,
                "--label", LABEL + "=" + token, "--network", "none",
                "--memory", "768m", "--memory-swap", "768m", "--cpus", "0.75",
                "--pids-limit", "256", "--log-driver", "json-file",
                "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
                "--env", "MYSQL_ROOT_PASSWORD", "--env", "MYSQL_DATABASE=" + DATABASE,
                source["Image"], "--performance-schema=OFF", "--innodb-buffer-pool-size=64M",
                "--max-connections=15", "--skip-log-bin",
            ], env=child_env, timeout=60)
            require(bool(re.fullmatch(r"[0-9a-f]{64}", container)), "Unexpected new container ID")
            state.update(container=container, container_name=name)
            save()
            validate_target(inspect(container), source, token)
            print("ISOLATED_CONTAINER_CREATED " + name, flush=True)
            deadline = time.monotonic() + 240
            while True:
                info = inspect(container)
                require(info["State"]["Running"], "Temporary MySQL exited during startup")
                try:
                    if mysql(sql="SELECT 1;") == "1":
                        break
                except RuntimeError:
                    pass
                require(time.monotonic() < deadline, "Temporary MySQL readiness timed out")
                time.sleep(2)
            # Recheck every safety boundary immediately before the SQL write.
            validate_target(inspect(container), source, token)
            mysql(stream=sql_file)
        tables = mysql(sql="SELECT TABLE_NAME FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;").splitlines()
        state["restored_tables"] = tables
        missing = CORE_TABLES - set(tables)
        require(not missing, "Restored database is missing core tables: " + ", ".join(sorted(missing)))
        queries = [f"SELECT {literal(table)}, COUNT(*) FROM {identifier(table)};" for table in tables]
        rows = mysql(sql="\n".join(queries))
        counts = {name: int(count) for name, count in (line.split("\t") for line in rows.splitlines())}
        checks = mysql(sql="SELECT 'negative_cash', COUNT(*) FROM simulation_accounts WHERE available_cash < 0;"
                       "SELECT 'negative_position_quantity', COUNT(*) FROM positions WHERE quantity < 0;"
                       "SELECT 'accounts_without_user', COUNT(*) FROM simulation_accounts a "
                       "LEFT JOIN users u ON u.id=a.user_id WHERE u.id IS NULL;"
                       "SELECT 'trades_without_order', COUNT(*) FROM trades t "
                       "LEFT JOIN orders o ON o.id=t.order_id WHERE o.id IS NULL;")
        checks = {name: int(count) for name, count in (line.split("\t") for line in checks.splitlines())}
        state.update(table_counts=counts, basic_integrity_checks=checks)
        require(all(count == 0 for count in checks.values()), "Restored data failed basic integrity checks")
        require(inspect(source["Id"])["State"]["Running"], "Production MySQL is no longer running")
        state.update(status="import_verified_application_pending", table_counts=counts,
                     basic_integrity_checks=checks, elapsed_seconds=round(time.monotonic() - started, 2),
                     validation_scope="SQL import, table counts, four basic integrity checks; application and full reconciliation pending")
        save()
        print("RESTORE_IMPORT_OK")
        print(json.dumps({"table_counts": counts, "basic_integrity_checks": checks}, ensure_ascii=True, indent=2))
        print("RESTORE_STATE=" + str(state_file))
        print("RESTORE_SECONDS=" + str(state["elapsed_seconds"]))
        print("TEMPORARY_CONTAINER_RETAINED_FOR_APPLICATION_CHECKS=" + name)
        return state_file
    except Exception:
        state.update(status="failed", elapsed_seconds=round(time.monotonic() - started, 2))
        if container and re.fullmatch(r"[0-9a-f]{64}", container):
            # Only stop the new container owned by this attempt. Preserve its
            # isolated data for diagnosis; never remove a production volume.
            try:
                current = inspect(container)
                if current["Config"].get("Labels", {}).get(LABEL) == token:
                    command(["docker", "stop", "--time", "10", container])
            except Exception:
                state["temporary_container_stop_failed"] = True
        save()
        print("RESTORE_STATE=" + str(state_file), flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--project", type=Path, default=Path("/opt/monipan"))
    args = parser.parse_args()
    require(os.name == "posix", "Run this script on the Linux server")
    require(args.project.is_dir(), "Production project directory is missing")
    restore(args.backup, args.project)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("RESTORE_FAILED " + type(exc).__name__ + ": " + str(exc))
        raise SystemExit(1)
