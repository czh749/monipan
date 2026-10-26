"""Repair the release packaging permissions and reuse the retained drill DB.

The failed image and build context remain intact. Only a new candidate is built;
the existing production services and their files must remain unchanged.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets

import prepare_schema_release as release
from restore_mysql_drill import check_backup, command, inspect, require, validate_target
from check_restore_application import check


STATE_PATH = Path("/opt/monipan/backups/schema-release-20261018-kn9o5neq/release-state.json")
FAILED_IMAGE = "sha256:eda0ed340117ae73b7bd511a76406dba2691b43a481be07f8d1074904e5bc04a"
VENDOR_FILE = "/opt/monipan-vendor/alembic/__init__.py"
PERMISSION_PATHS = ("/opt/monipan-vendor", VENDOR_FILE, "/app/app/main.py")


def permission_report(image):
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image)), "Expected an immutable diagnostic image ID")
    metadata = json.loads(command(["docker", "image", "inspect", image]))[0]
    require(metadata["Id"] == image and metadata["Config"].get("User") == "monipan"
            and metadata["Config"].get("Labels", {}).get("com.monipan.schema-baseline") == "20261018_01",
            "Unexpected diagnostic image configuration")
    source = '''import json, os, stat
from pathlib import Path
result = {"uid":os.getuid(),"gid":os.getgid(),"paths":{}}
for name in ("/opt/monipan-vendor", "/opt/monipan-vendor/alembic/__init__.py", "/app/app/main.py"):
    path=Path(name)
    item={"readable":False}
    try:
        value=path.stat()
        item.update(mode=oct(stat.S_IMODE(value.st_mode)),uid=value.st_uid,gid=value.st_gid)
        if path.is_dir():
            item["readable"]=os.access(path,os.R_OK|os.X_OK)
        else:
            with path.open("rb") as stream:stream.read(1)
            item["readable"]=True
    except OSError as exc:
        item["error_type"]=type(exc).__name__
    result["paths"][name]=item
print(json.dumps(result))
'''
    token = secrets.token_hex(12)
    report = json.loads(command([
        "docker", "run", "--rm", "--pull=never", "--name", "monipan-permission-check-" + token,
        "--label", "com.monipan.permission-check=" + token,
        "--network", "none", "--read-only", "--memory", "128m", "--memory-swap", "128m",
        "--cpus", "0.25", "--pids-limit", "32", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--entrypoint", "python", image, "-c", source]))
    require(report.get("uid") != 0 and isinstance(report.get("paths"), dict), "Invalid non-root permission report")
    return report


def validate_retained_drill(state, root, services):
    path = Path(state["restore_state"]).resolve(strict=True)
    try:
        path.relative_to(root / "drill-backup")
    except ValueError:
        raise RuntimeError("Retained drill state is outside this release") from None
    drill = json.loads(path.read_text(encoding="utf-8"))
    require(drill["status"] == "import_verified_application_pending"
            and drill.get("application_report", {}).get("error_type") == "PermissionError"
            and drill.get("application_report", {}).get("status") == "failed",
            "Retained drill is not the expected failed permission check")
    require(drill["database"] == "monipan_restore" and re.fullmatch(r"[0-9a-f]{24}", drill["token"]),
            "Unexpected retained drill identity")
    require(drill["source_container"] == services["mysql"]["Id"], "Production MySQL changed since restore")
    require(check_backup(Path(drill["backup"])) == drill["backup_sha256"], "Retained backup changed")
    restored = inspect(drill["container"])
    validate_target(restored, services["mysql"], drill["token"])
    require(restored["State"]["Running"], "Retained isolated MySQL is not running")
    return path


def retry(state_path):
    state_path = state_path.resolve(strict=True)
    root = state_path.parent
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(state["status"] == "preparation_failed" and state.get("production_deployed") is False
            and state.get("production_schema_modified") is False and state["old_image"] == release.PARENT_IMAGE
            and state.get("candidate_image") == FAILED_IMAGE, "Release state is not the expected failed preparation")
    project = Path(state["project"]).resolve(strict=True)
    services = release.production_containers(project)
    require({name: value["Id"] for name, value in services.items()} == state["production_container_ids"],
            "Production services changed after the failed preparation")
    release.verify_unchanged(project, services, state["original_file_hashes"])
    drill_state = validate_retained_drill(state, root, services)
    old_report = permission_report(FAILED_IMAGE)
    print("FAILED_IMAGE_PERMISSIONS=" + json.dumps(old_report, sort_keys=True), flush=True)
    # A searchable top-level directory does not imply its files are readable.
    # Reproduce the actual file-open failure before authorizing this repair.
    old_vendor_file = old_report["paths"].get(VENDOR_FILE, {})
    require(old_vendor_file.get("readable") is False
            and old_vendor_file.get("error_type") == "PermissionError",
            "Expected vendor permission defect was not reproduced; inspect the diagnostic report")
    require(json.loads(command(["docker", "image", "inspect", state["old_image_tag"]]))[0]["Id"] == state["old_image"],
            "Retained production image tag changed")
    token = secrets.token_hex(12)
    output = root / ("permission-retry-" + token)
    output.mkdir(mode=0o700)
    attempt = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "failed_candidate_image": FAILED_IMAGE,
               "failed_image_permission_report": old_report,
               "candidate_image_tag": "monipan-backend:schema-20261018_01-permissions-" + token,
               "release_context_sha256": release.RELEASE_SHA256, "context": str(output / "context")}
    state.setdefault("permission_retries", []).append(attempt)
    state["status"] = "retrying_candidate_permissions"

    def save():
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    save()
    print("REUSING_RETAINED_RESTORE=" + str(drill_state), flush=True)
    try:
        import base64
        release.unpack_release(base64.b64decode(release.RELEASE_B64, validate=True), output / "context")
        print("FIXED_CANDIDATE_IMAGE_BUILD_STARTING", flush=True)
        image = release.build_candidate(state["old_image"], state["old_image_tag"], attempt["candidate_image_tag"],
                                        output / "context", output / "image-build.log")
        attempt["candidate_image"] = image
        fixed_report = permission_report(image)
        attempt["fixed_image_permission_report"] = fixed_report
        save()
        print("FIXED_IMAGE_PERMISSIONS=" + json.dumps(fixed_report, sort_keys=True), flush=True)
        require(all(fixed_report["paths"].get(path, {}).get("readable") is True
                    for path in PERMISSION_PATHS),
                "Fixed candidate still has inaccessible code files")
        release.verify_unchanged(project, services, state["original_file_hashes"])
        # The unchanged restored DB is reused, not imported into again.
        check(drill_state, project, release.FINAL_IMAGE_PROBE, candidate_image=image)
        drill = json.loads(drill_state.read_text(encoding="utf-8"))
        report = drill["application_report"]
        require(drill["status"] == "restore_application_verified_and_cleaned"
                and drill.get("temporary_resources_cleaned") is True
                and report.get("status") == "passed" and report.get("candidate_files_verified") == 154
                and report.get("source_overlay_applied") is False, "Final-image rehearsal is incomplete")
        release.verify_unchanged(project, services, state["original_file_hashes"])
        state.update(status="candidate_image_verified_production_not_deployed", candidate_image=image,
                     candidate_image_tag=attempt["candidate_image_tag"], candidate_report=report,
                     candidate_context=attempt["context"], release_context_sha256=release.RELEASE_SHA256)
        attempt["status"] = "passed"
        save()
        print("FINAL_IMAGE_REHEARSAL_OK", flush=True)
        print("TEMPORARY_RESOURCES_CLEANED", flush=True)
        print("PRODUCTION_CONTAINERS_UNCHANGED", flush=True)
        print("PRODUCTION_SCHEMA_NOT_ADOPTED", flush=True)
        print("CANDIDATE_IMAGE=" + image, flush=True)
        print("RELEASE_STATE=" + str(state_path), flush=True)
        print("CANDIDATE_READY_PRODUCTION_NOT_DEPLOYED", flush=True)
        return state_path
    except Exception as exc:
        state["status"] = "preparation_failed"
        attempt["status"] = "failed"
        attempt["error_type"] = type(exc).__name__
        save()
        raise


def main():
    require(os.name == "posix" and os.geteuid() == 0, "Run as root on the existing Linux server")
    require(release.RELEASE_B64 is not None and release.FINAL_IMAGE_PROBE is not None, "Use the generated retry artifact")
    require(STATE_PATH.resolve(strict=True) == STATE_PATH, "Release state path must not contain symbolic links")
    import fcntl
    os.umask(0o077)
    with Path("/run/lock/monipan-schema-prepare.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another schema preparation is running") from None
        retry(STATE_PATH)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        print("SCHEMA_RELEASE_RETRY_FAILED " + detail)
        raise SystemExit(1)
