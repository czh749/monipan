"""Build and rehearse the exact backend release without deploying production.

Run the generated single-file artifact on the existing Linux server. Production
files and running services are read only; backups, local image tags and owned
disposable drill resources are the only new resources.
"""

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile

from restore_mysql_drill import command, inspect, require, restore
from check_restore_application import check


RELEASE_B64 = None
RELEASE_SHA256 = "3896cd7cb5d61a69b1ea16d662fb8f9e47244f52adecd266e2c6c7e4448db052"
FINAL_IMAGE_PROBE = None
PARENT_IMAGE = "sha256:2bc78105792958fe58f54438a4d892067f28cb9f62d9ee247515d09136a2b2e2"
BASE_FILES = {
    "app/main.py": "6d0ca602f0f51b6a23b91f747422a2512f36148619e27298c8d10abcb3fddaf0",
    "app/models.py": "4ee0be53f22c9eb4d1a86055513022d0e58a4d6f358dcbcda22d56b298d6eeaf",
    "app/invites.py": "5e0e34aafb8166cc09e76c4454348a687ff85ba99c1c59e77baee9dde2c178b4",
    "requirements.txt": "14217401da8c1ce0867d7c7d0d13d5f9bc40c92f506eaa8609ac07ede1e3a58d",
}
SNAPSHOT_FILES = ("compose.yaml", "compose.override.yaml", "compose.override.yml", ".env",
                  "frontend/nginx.conf", "frontend/security-headers.conf", "backend/Dockerfile",
                  *("backend/" + name for name in BASE_FILES))


def unpack_release(payload, destination):
    require(hashlib.sha256(payload).hexdigest() == RELEASE_SHA256, "Release archive checksum mismatch")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        require(len(members) <= 2000, "Release archive has too many files")
        for item in members:
            path = PurePosixPath(item.name)
            require(item.isfile() and not path.is_absolute() and ".." not in path.parts
                    and "\\" not in item.name and path.as_posix() == item.name
                    and item.name not in files and 0 <= item.size <= 8 * 1024 * 1024,
                    "Unsafe release archive entry")
            files[item.name] = archive.extractfile(item).read()
        require(sum(map(len, files.values())) <= 16 * 1024 * 1024, "Release archive is too large")
    manifest = json.loads(files["release-manifest.json"])
    require(manifest["baseline_revision"] == "20261018_01"
            and manifest["mysql_rehearsal_passed"] is True, "Unexpected release manifest")
    require(set(files) == set(manifest["files"]) | {"release-manifest.json", "README.txt"},
            "Release file list differs from manifest")
    for name, digest in manifest["files"].items():
        require(hashlib.sha256(files[name]).hexdigest() == digest, "Release file checksum mismatch")
    destination.mkdir(mode=0o700)
    for name, content in files.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        # These are public application/dependency sources, never credentials.
        # The enclosing release folder remains private. Do not let the host's
        # 077 umask turn copied image dependencies into root-only files.
        target.chmod(0o644)
        directory = target.parent
        while directory != destination:
            directory.chmod(0o755)
            directory = directory.parent
    return manifest


def production_containers(project):
    environment = dict(os.environ, COMPOSE_PROJECT_NAME="monipan")
    result = {}
    for service in ("backend", "frontend", "mysql"):
        container_id = command(["docker", "compose", "ps", "-q", service], cwd=project, env=environment)
        require(bool(re.fullmatch(r"[0-9a-f]{12,64}", container_id)), "Expected one running " + service)
        item = inspect(container_id)
        labels = item["Config"].get("Labels", {})
        require(item["State"]["Running"] and labels.get("com.docker.compose.project") == "monipan"
                and labels.get("com.docker.compose.service") == service,
                "Unexpected production service identity: " + service)
        require(item["State"].get("Health", {}).get("Status") == "healthy",
                "Production service is not healthy: " + service)
        result[service] = item
    backend = result["backend"]
    require(backend["Image"] == PARENT_IMAGE and backend["Config"].get("User") == "monipan",
            "Production backend changed; refresh release preflight")
    require(backend["Config"]["Labels"].get("com.docker.compose.project.config_files") == str(project / "compose.yaml"),
            "Production Compose file list changed; refresh release preflight")
    return result


def check_running_source(backend):
    # Never import app.main: its old lifespan performs legacy migration work.
    source = '''import hashlib,json,os
from pathlib import Path
from sqlalchemy import inspect
from app.database import engine
names = ''' + repr(tuple(BASE_FILES)) + '''
print(json.dumps({"files":{p:hashlib.sha256((Path("/app")/p).read_bytes()).hexdigest() for p in names},
"pythonpath":os.getenv("PYTHONPATH"),"version_table":inspect(engine).has_table("alembic_version")}))
'''
    report = json.loads(command(["docker", "exec", backend["Id"], "python", "-c", source]))
    require(report["files"] == BASE_FILES and report["pythonpath"] is None and report["version_table"] is False,
            "Production source or database version changed; refresh release preflight")


def snapshot_files(project, destination):
    hashes = {}
    for name in SNAPSHOT_FILES:
        original = project / name
        require(not original.is_symlink(), "Snapshot source must not be a symbolic link: " + name)
        if not original.exists():
            hashes[name] = None
            continue
        require(original.is_file(), "Snapshot source is not a regular file: " + name)
        content = original.read_bytes()
        hashes[name] = hashlib.sha256(content).hexdigest()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o600)
    return hashes


def verify_unchanged(project, original_containers, original_files):
    current = production_containers(project)
    require({name: item["Id"] for name, item in current.items()}
            == {name: item["Id"] for name, item in original_containers.items()},
            "Production container identity changed during preparation")
    for name, digest in original_files.items():
        path = project / name
        require(not path.is_symlink(), "Production file became a symbolic link: " + name)
        current_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        require(current_digest == digest, "Production file changed during preparation: " + name)
    check_running_source(current["backend"])


def build_candidate(parent_image, parent_tag, candidate_tag, context, log):
    command(["docker", "image", "tag", parent_image, parent_tag])
    args = ["docker", "build", "--pull=false", "--network=none", "--build-arg",
            "MONIPAN_PARENT_IMAGE=" + parent_tag, "--tag", candidate_tag, str(context)]
    with log.open("wb") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, timeout=600)
    require(result.returncode == 0, "Candidate build failed; see private image-build.log. Production is unchanged")
    image = json.loads(command(["docker", "image", "inspect", candidate_tag]))[0]
    parent = json.loads(command(["docker", "image", "inspect", parent_image]))[0]
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"])) and image["Id"] != parent_image,
            "Candidate image identity is invalid")
    require(image["Config"].get("Labels", {}).get("com.monipan.schema-baseline") == "20261018_01"
            and image["Config"].get("User") == "monipan", "Candidate image configuration mismatch")
    for key in ("Cmd", "Entrypoint", "WorkingDir", "ExposedPorts", "Volumes", "Healthcheck", "StopSignal"):
        require(image["Config"].get(key) == parent["Config"].get(key), "Candidate changed inherited image config: " + key)
    parent_environment = dict(value.split("=", 1) for value in parent["Config"].get("Env", []))
    candidate_environment = dict(value.split("=", 1) for value in image["Config"].get("Env", []))
    require(candidate_environment == dict(parent_environment, PYTHONPATH="/opt/monipan-vendor"),
            "Candidate environment differs beyond the vendor import path")
    layers = parent["RootFS"]["Layers"]
    require(image["RootFS"]["Layers"][:len(layers)] == layers, "Candidate does not retain the production image layers")
    require(json.loads(command(["docker", "image", "inspect", parent_tag]))[0]["Id"] == parent_image,
            "Retained rollback image changed")
    return image["Id"]


def prepare(project, payload, probe):
    project = project.resolve(strict=True)
    require((project / "scripts/backup_mysql.sh").is_file(), "Existing backup script is missing")
    require(shutil.disk_usage(project).free >= 1024 ** 3, "Preparation requires at least 1 GiB free disk")
    services = production_containers(project)
    check_running_source(services["backend"])
    os.umask(0o077)
    (project / "backups").mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="schema-release-20261018-", dir=project / "backups"))
    state_path = output / "release-state.json"
    token = secrets.token_hex(12)
    state = {"status": "preparing", "started_at_utc": datetime.now(timezone.utc).isoformat(),
             "baseline_revision": "20261018_01", "project": str(project),
             "production_container_ids": {name: item["Id"] for name, item in services.items()},
             "old_image": PARENT_IMAGE, "old_image_tag": "monipan-backend:before-schema-20261018-" + token,
             "candidate_image_tag": "monipan-backend:schema-20261018_01-" + token,
             "production_deployed": False, "production_schema_modified": False}

    def save():
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    save()
    print("RELEASE_STATE=" + str(state_path), flush=True)
    try:
        files = snapshot_files(project, output / "original-files")
        state["original_file_hashes"] = files
        # Source updates will be performed later; detect existing backend drift now.
        require(all(files["backend/" + name] == digest for name, digest in BASE_FILES.items()),
                "Backend checkout differs from the inspected running source")
        unpack_release(payload, output / "context")
        save()
        print("CANDIDATE_IMAGE_BUILD_STARTING", flush=True)
        state["candidate_image"] = build_candidate(PARENT_IMAGE, state["old_image_tag"],
                                                   state["candidate_image_tag"], output / "context",
                                                   output / "image-build.log")
        state["status"] = "candidate_built_rehearsal_pending"
        save()
        print("CANDIDATE_IMAGE=" + state["candidate_image"], flush=True)
        verify_unchanged(project, services, files)
        backup_dir = output / "drill-backup"
        backup_dir.mkdir(mode=0o700)
        environment = dict(os.environ, COMPOSE_PROJECT_NAME="monipan", MONIPAN_BACKUP_DIR=str(backup_dir))
        print(command(["/bin/sh", "scripts/backup_mysql.sh"], cwd=project, env=environment, timeout=600), flush=True)
        backups = list(backup_dir.glob("monipan-*.sql.gz"))
        require(len(backups) == 1, "Expected exactly one new release rehearsal backup")
        drill_state = restore(backups[0], project)
        state["restore_state"] = str(drill_state)
        save()
        check(drill_state, project, probe, candidate_image=state["candidate_image"])
        report = json.loads(drill_state.read_text(encoding="utf-8"))
        require(report["status"] == "restore_application_verified_and_cleaned"
                and report.get("temporary_resources_cleaned") is True, "Release rehearsal cleanup is incomplete")
        state["candidate_report"] = report["application_report"]
        require(state["candidate_report"].get("candidate_files_verified", 0) > 0
                and state["candidate_report"].get("source_overlay_applied") is False,
                "Expected rehearsal of the final image without source overlays")
        verify_unchanged(project, services, files)
        state["status"] = "candidate_image_verified_production_not_deployed"
        save()
        print("FINAL_IMAGE_REHEARSAL_OK", flush=True)
        print("PRODUCTION_CONTAINERS_UNCHANGED", flush=True)
        print("PRODUCTION_SCHEMA_NOT_ADOPTED", flush=True)
        print("RELEASE_STATE=" + str(state_path), flush=True)
        print("CANDIDATE_READY_PRODUCTION_NOT_DEPLOYED", flush=True)
        return state_path
    except Exception as exc:
        state["status"] = "preparation_failed"
        state["error_type"] = type(exc).__name__
        save()
        # No automatic image/source rollback is needed: this stage never deploys.
        raise


def main():
    require(os.name == "posix" and os.geteuid() == 0, "Run this artifact as root on the Linux server")
    require(RELEASE_B64 is not None and FINAL_IMAGE_PROBE is not None, "Run the generated release-preparation artifact")
    require(Path("/opt/monipan").resolve(strict=True) == Path("/opt/monipan"), "Project directory must not be a symbolic link")
    import fcntl
    with Path("/run/lock/monipan-schema-prepare.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another release preparation is running") from None
        prepare(Path("/opt/monipan"), base64.b64decode(RELEASE_B64, validate=True), FINAL_IMAGE_PROBE)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Subprocess output and arbitrary exception values can contain secrets.
        detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        print("SCHEMA_RELEASE_PREPARATION_FAILED " + detail)
        raise SystemExit(1)
