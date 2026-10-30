"""Install the checked daily backup timer on the existing Linux production host.

Only creates new monipan-backup units. Does not change Compose or restart apps.
The single-file delivery artifact supplies the exact same unit text as the repo.
"""

import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


NAMES = ("monipan-backup.service", "monipan-backup.timer")
PROJECT = Path("/opt/monipan")
UNIT_DIR = Path("/etc/systemd/system")
EMBEDDED_UNITS = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args):
    environment = os.environ.copy()
    environment["COMPOSE_PROJECT_NAME"] = "monipan"
    result = subprocess.run(args, capture_output=True, text=True, env=environment, timeout=2100)
    require(result.returncode == 0,
            "Command failed: " + args[0] + " " + args[1] + " (exit " + str(result.returncode) + ")")
    return result.stdout.strip()


def verified_backup(directory, started):
    files = sorted(directory.glob("monipan-*.sql.gz"), key=lambda p: p.stat().st_mtime)
    require(bool(files), "Service returned without a backup file")
    backup = files[-1]
    require(not backup.is_symlink() and backup.stat().st_mtime >= started - 2,
            "No new regular backup from this run")
    checksum = backup.with_name(backup.name + ".sha256")
    require(checksum.is_file() and not checksum.is_symlink(), "Missing regular checksum sidecar")
    fields = checksum.read_text(encoding="ascii").split()
    require(len(fields) == 2 and re.fullmatch(r"[0-9a-f]{64}", fields[0])
            and fields[1] == backup.name, "Invalid backup checksum sidecar")
    digest = hashlib.sha256()
    with backup.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    require(digest.hexdigest() == fields[0], "New backup checksum mismatch")
    with gzip.open(backup, "rb") as stream:
        require(bool(stream.read(1)), "New backup decompressed to empty data")
        while stream.read(1024 * 1024):
            pass
    require(all(path.stat().st_mode & 0o077 == 0 for path in (backup, checksum)),
            "Backup permissions must exclude group and other users")
    return backup


def install(units, project=PROJECT, unit_dir=UNIT_DIR):
    os.umask(0o077)
    require(set(units) == set(NAMES), "Unexpected unit payload")
    require(project.is_dir() and not project.is_symlink(), "Project directory is missing or symlinked")
    script = project / "scripts/backup_mysql.sh"
    require(script.is_file() and not script.is_symlink(), "Existing backup script is missing or symlinked")
    require((project / "compose.yaml").is_file(), "Existing Compose file is missing")
    require(unit_dir.is_dir() and not unit_dir.is_symlink(), "Unsafe system unit directory")
    for name in NAMES:
        target = unit_dir / name
        require(not target.exists() and not target.is_symlink(), "Existing unit must be reviewed: " + name)
    backup_parent = project / "backups"
    require(not backup_parent.is_symlink(), "Backup parent must not be symlinked")
    daily = backup_parent / "daily"
    require(not daily.is_symlink(), "Daily backup directory must not be symlinked")
    if daily.exists():
        require(daily.is_dir() and daily.stat().st_uid == 0 and daily.stat().st_mode & 0o077 == 0,
                "Existing daily backup directory must be private and root-owned")
    for name in NAMES:
        # Missing-unit show has different exit statuses across systemd versions.
        result = subprocess.run(["systemctl", "show", name, "-p", "LoadState", "--value"],
                                capture_output=True, text=True, timeout=30)
        require(result.stdout.strip() == "not-found", "Loaded unit must be reviewed: " + name)
    run(["/bin/sh", "-n", str(script)])
    run(["docker", "compose", "--project-directory", str(project), "config", "--quiet"])
    container = run(["docker", "compose", "--project-directory", str(project), "ps", "-q", "mysql"])
    require(bool(re.fullmatch(r"[0-9a-f]{64}", container)), "Expected one existing production MySQL container")
    running = run(["docker", "inspect", "--format", "{{.State.Running}}", container])
    require(running == "true", "Production MySQL is not running")
    with tempfile.TemporaryDirectory(prefix="monipan-backup-units-") as staging:
        paths = []
        for name, source in units.items():
            path = Path(staging) / name
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
            paths.append(str(path))
        run(["systemd-analyze", "verify", *paths])
        daily.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in NAMES:
            with (unit_dir / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(units[name])
            (unit_dir / name).chmod(0o644)
    run(["systemctl", "daemon-reload"])
    started = time.time()
    print("FIRST_BACKUP_STARTING", flush=True)
    run(["systemctl", "start", NAMES[0]])
    properties = dict(line.split("=", 1) for line in run(
        ["systemctl", "show", NAMES[0], "-p", "Result", "-p", "ExecMainStatus"]
    ).splitlines() if "=" in line)
    require(properties.get("Result") == "success" and properties.get("ExecMainStatus") == "0",
            "First backup service did not finish successfully; timer is not enabled")
    backup = verified_backup(daily, started)
    run(["systemctl", "enable", "--now", NAMES[1]])
    require(run(["systemctl", "is-enabled", NAMES[1]]) == "enabled", "Timer enablement was not confirmed")
    require(run(["systemctl", "is-active", NAMES[1]]) == "active", "Timer activation was not confirmed")
    print("DAILY_BACKUP_INSTALLED")
    print("FIRST_BACKUP_VERIFIED=" + str(backup))
    print("SCHEDULE=03:00 Asia/Shanghai; retention threshold=14 days")
    print(run(["systemctl", "list-timers", "--all", "--no-pager", NAMES[1]]))


def main():
    require(os.name == "posix" and os.geteuid() == 0, "Run as root on the Linux production host")
    units = EMBEDDED_UNITS
    if units is None:
        folder = Path(__file__).resolve().parents[1] / "deploy/systemd"
        units = {name: (folder / name).read_text(encoding="utf-8") for name in NAMES}
    install(units)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("DAILY_BACKUP_SETUP_FAILED " + type(exc).__name__ + ": " + str(exc))
        print("Inspect monipan-backup units and service journal before retrying; existing units are never overwritten.")
        raise SystemExit(1)
