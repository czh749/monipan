"""Validate the restored application and remove only this drill's resources."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

from restore_mysql_drill import check_backup, command, inspect, require, validate_target


APP_LABEL = "com.monipan.restore-app"
PREFIX = "RESTORE_APPLICATION_RESULT="


def validate_application(app, restored, backend, source, token, *, expected_image=None):
    require(app["Id"] not in {restored["Id"], backend["Id"], source["Id"]}, "Application container identity collision")
    expected_image = expected_image or backend["Image"]
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image)), "Expected an immutable application image ID")
    require(app["Image"] == expected_image, "Unexpected application image")
    require(app["Config"].get("Labels", {}).get(APP_LABEL) == token, "Application ownership mismatch")
    require(app["HostConfig"]["NetworkMode"] == "container:" + restored["Id"], "Application network mismatch")
    require(not app["HostConfig"].get("PortBindings") and not app.get("Mounts"), "Application ports or mounts are unsafe")
    require(app["HostConfig"].get("Memory") == 512 * 1024 * 1024, "Application memory limit missing")
    require(not app["HostConfig"].get("Privileged"), "Application must not be privileged")
    values = dict(entry.split("=", 1) for entry in app["Config"]["Env"] if "=" in entry)
    require(values.get("MONIPAN_DISABLE_MARKET_LOOP") == "1", "Application background loop must be disabled")
    require(values.get("MONIPAN_ENVIRONMENT") == "production" and values.get("MONIPAN_COOKIE_SECURE") == "1",
            "Application production configuration missing")
    require(re.fullmatch(r"mysql\+pymysql://root:[0-9a-f]{64}@127\.0\.0\.1:3306/monipan_restore\?charset=utf8mb4",
                         values.get("MONIPAN_DATABASE_URL", "")), "Application database URL is not isolated")


def check(state_path, project, probe_source, *, candidate_image=None):
    started = time.monotonic()
    os.umask(0o077)
    state_path = state_path.resolve(strict=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(state["status"] == "import_verified_application_pending", "This restore is not awaiting application checks")
    require(state["database"] == "monipan_restore", "Unexpected restored database name")
    token = state["token"]
    require(re.fullmatch(r"[0-9a-f]{24}", token), "Unexpected restore token")
    require(check_backup(Path(state["backup"])) == state["backup_sha256"], "Backup changed after import")
    environment = dict(os.environ, COMPOSE_PROJECT_NAME="monipan")
    source_id = command(["docker", "compose", "ps", "-q", "mysql"], cwd=project, env=environment)
    require(bool(re.fullmatch(r"[0-9a-f]{12,64}", source_id)), "Expected one production MySQL container")
    source = inspect(source_id)
    require(source["Id"] == state["source_container"], "Production MySQL changed since the import")
    require(source["Config"].get("Labels", {}).get("com.docker.compose.project") == "monipan"
            and source["Config"]["Labels"].get("com.docker.compose.service") == "mysql", "Unexpected production MySQL")
    restored = inspect(state["container"])
    validate_target(restored, source, token)
    require(restored["State"]["Running"], "Temporary restored MySQL is not running")
    backend_id = command(["docker", "compose", "ps", "-q", "backend"], cwd=project, env=environment)
    require(bool(re.fullmatch(r"[0-9a-f]{12,64}", backend_id)), "Expected one production backend container")
    backend = inspect(backend_id)
    require(backend["State"]["Running"] and backend["Config"].get("Labels", {}).get("com.docker.compose.project") == "monipan"
            and backend["Config"]["Labels"].get("com.docker.compose.service") == "backend", "Unexpected production backend")
    application_image = candidate_image or backend["Image"]
    if candidate_image is not None:
        require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_image)), "Candidate image must be an immutable image ID")
        candidate = json.loads(command(["docker", "image", "inspect", candidate_image]))[0]
        require(candidate["Id"] == candidate_image
                and candidate["Config"].get("Labels", {}).get("com.monipan.schema-baseline") == "20261018_01"
                and candidate["Config"].get("User") == "monipan", "Unexpected candidate image configuration")
        require(candidate_image != backend["Image"], "Candidate image must differ from the running backend")
    values = dict(entry.split("=", 1) for entry in restored["Config"]["Env"] if "=" in entry)
    password = values.get("MYSQL_ROOT_PASSWORD", "")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", password)), "Unexpected temporary password format")
    child_env = dict(os.environ, MONIPAN_DATABASE_URL="mysql+pymysql://root:" + password +
                     "@127.0.0.1:3306/monipan_restore?charset=utf8mb4",
                     MONIPAN_ENVIRONMENT="production", MONIPAN_COOKIE_SECURE="1",
                     MONIPAN_DISABLE_MARKET_LOOP="1", MONIPAN_RESTORE_COUNTS_JSON=json.dumps(state["table_counts"]))
    app_id = None

    def save():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        args = ["docker", "create", "-i", "--pull=never", "--name", "monipan-restore-app-" + token,
                "--label", APP_LABEL + "=" + token, "--network", "container:" + restored["Id"],
                "--memory", "512m", "--memory-swap", "512m", "--cpus", "0.5", "--pids-limit", "128",
                "--log-driver", "json-file", "--log-opt", "max-size=5m", "--log-opt", "max-file=2"]
        for key in ("MONIPAN_DATABASE_URL", "MONIPAN_ENVIRONMENT", "MONIPAN_COOKIE_SECURE",
                    "MONIPAN_DISABLE_MARKET_LOOP", "MONIPAN_RESTORE_COUNTS_JSON"):
            args += ["--env", key]
        app_id = command(args + [application_image, "python", "-"], env=child_env)
        require(bool(re.fullmatch(r"[0-9a-f]{64}", app_id)), "Unexpected application container ID")
        state["application_container"] = app_id
        save()
        validate_application(inspect(app_id), restored, backend, source, token, expected_image=application_image)
        validate_target(inspect(restored["Id"]), source, token)
        result = subprocess.run(["docker", "start", "--attach", "--interactive", app_id],
                                input=probe_source.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=300)
        (state_path.parent / "application-probe.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
        matches = [line[len(PREFIX):] for line in result.stdout.decode("utf-8", errors="replace").splitlines()
                   if line.startswith(PREFIX)]
        require(len(matches) == 1, "Application probe did not return a result; see private application-probe.log")
        report = json.loads(matches[0])
        state["application_report"] = report
        save()
        print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
        require(result.returncode == 0 and report.get("status") == "passed", "Application checks failed; isolated DB retained")
        state["status"] = "restore_application_verified_cleanup_pending"
        state["application_check_seconds"] = round(time.monotonic() - started, 2)
        save()
    finally:
        if app_id and re.fullmatch(r"[0-9a-f]{64}", app_id):
            current = inspect(app_id)
            require(current["Config"].get("Labels", {}).get(APP_LABEL) == token
                    and app_id not in {source["Id"], backend["Id"], restored["Id"]}, "Application cleanup ownership mismatch")
            command(["docker", "rm", "-f", app_id])
    # Destructive operations below concern only the owned disposable clone.
    current = inspect(restored["Id"])
    validate_target(current, source, token)
    volume = current["Mounts"][0].get("Name", "")
    require(bool(re.fullmatch(r"[0-9a-f]{64}", volume)), "Cleanup expects this drill's anonymous volume")
    attached = command(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + volume]).splitlines()
    require(attached == [restored["Id"]], "Another container uses the temporary volume; cleanup stopped")
    require(inspect(source["Id"])["State"]["Running"] and inspect(backend["Id"])["State"]["Running"],
            "Production services are not running; cleanup stopped")
    command(["docker", "stop", "--time", "20", restored["Id"]])
    command(["docker", "rm", "--volumes", restored["Id"]])
    absence = subprocess.run(["docker", "volume", "inspect", volume], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(absence.returncode != 0 and b"no such volume" in absence.stderr.lower(),
            "Temporary data volume deletion was not confirmed")
    state["status"] = "restore_application_verified_and_cleaned"
    state["temporary_resources_cleaned"] = True
    state["automated_check_seconds"] = round(state["elapsed_seconds"] + time.monotonic() - started, 2)
    save()
    print("RESTORE_APPLICATION_OK")
    print("TEMPORARY_RESOURCES_CLEANED")
    print("RESTORE_STATE=" + str(state_path))
    print("AUTOMATED_CHECK_SECONDS=" + str(state["automated_check_seconds"]))


def main(probe_source=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--project", type=Path, default=Path("/opt/monipan"))
    args = parser.parse_args()
    try:
        require(os.name == "posix", "Run this script on the Linux server")
        if probe_source is None:
            probe_source = Path(__file__).with_name("restore_application_probe.py").read_text(encoding="utf-8")
        check(args.state, args.project, probe_source)
    except Exception as exc:
        print("RESTORE_APPLICATION_FAILED " + type(exc).__name__ + ": " + str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
