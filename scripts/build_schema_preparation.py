"""Make one uploadable artifact for final-image construction and rehearsal."""

import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import runpy
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REHEARSAL_SHA256 = "a6583e9778e29c162bf9466dbec64b27e703644f1d93e7f21d9ac4e20c8ebe33"


def final_image_probe(scripts, file_hashes):
    # Only diagnostics enter the temporary filesystem. App and vendor code must
    # already exist, byte-for-byte, in the immutable candidate image.
    return '''import hashlib, importlib, json, os, sys, tempfile
from pathlib import Path
stage = "verify_candidate_files"
relative = None
try:
    expected = ''' + repr(file_hashes) + '''
    for relative, digest in expected.items():
        root = Path("/app") if relative.startswith("app/") else Path("/opt/monipan-vendor")
        path = root / (relative if relative.startswith("app/") else relative.removeprefix("vendor/"))
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Candidate image file checksum mismatch")
    if os.getenv("PYTHONPATH") != "/opt/monipan-vendor":
        raise RuntimeError("Candidate import path mismatch")
    stage = "import_candidate_dependencies"
    for name in ("alembic", "mako", "markupsafe"):
        if not Path(importlib.import_module(name).__file__).is_relative_to("/opt/monipan-vendor"):
            raise RuntimeError("Candidate dependency imported outside the bundled vendor directory")
    folder = Path(tempfile.mkdtemp(prefix="monipan-final-image-probe-"))
    stage = "prepare_diagnostic_scripts"
    scripts = ''' + repr(scripts) + '''
    for name, source in scripts.items():
        (folder / name).write_text(source, encoding="utf-8")
    sys.path.insert(0, str(folder))
    from migration_baseline_probe import run_migration_probe
    stage = "migration_and_application_checks"
    report = run_migration_probe()
    report["candidate_files_verified"] = len(expected)
    report["source_overlay_applied"] = False
    print("RESTORE_APPLICATION_RESULT=" + json.dumps(report, ensure_ascii=True))
    if report.get("status") != "passed":
        raise SystemExit(1)
except Exception as exc:
    error = {"status":"failed", "error_type":type(exc).__name__, "stage":stage}
    if stage == "verify_candidate_files" and relative in expected:
        error["release_file"] = relative
    print("RESTORE_APPLICATION_RESULT=" + json.dumps(error))
    raise SystemExit(1)
'''


def build():
    rehearsal = ROOT / ".docker/monipan-baseline-rehearsal-20261018.py"
    if hashlib.sha256(rehearsal.read_bytes()).hexdigest() != REHEARSAL_SHA256:
        raise RuntimeError("The passing MySQL rehearsal artifact changed")
    rehearsal_scope = runpy.run_path(str(rehearsal), run_name="packaging_validation")
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(rehearsal_scope["BUNDLE"]))) as bundle:
        scripts = {name: bundle.read("scripts/" + name).decode("utf-8")
                   for name in ("migration_baseline_probe.py", "restore_application_probe.py")}
    for name, source in scripts.items():
        if (ROOT / "scripts" / name).read_text(encoding="utf-8") != source:
            raise RuntimeError("Probe changed after passing MySQL rehearsal: " + name)
    location = str(ROOT / "scripts")
    import sys
    sys.path.insert(0, location)
    try:
        from build_schema_release import build as build_context
        from prepare_schema_release import unpack_release
        context = build_context()
        payload = context.read_bytes()
        import tempfile
        with tempfile.TemporaryDirectory(prefix="monipan-release-package-") as temp:
            manifest = unpack_release(payload, Path(temp) / "context")
    finally:
        sys.path.remove(location)
    file_hashes = {name: digest for name, digest in manifest["files"].items()
                   if name.startswith(("app/", "vendor/"))}
    probe = final_image_probe(scripts, file_hashes)
    ast.parse(probe, feature_version=(3, 9))  # Executed by the Python 3.13 image.
    bootstrap = ["import sys, types\n"]
    for name in ("restore_mysql_drill", "check_restore_application"):
        source = (ROOT / "scripts" / (name + ".py")).read_text(encoding="utf-8")
        bootstrap.append("_module=types.ModuleType(" + repr(name) + ")\n")
        bootstrap.append("_module.__file__=" + repr("<embedded>/" + name + ".py") + "\n")
        bootstrap.append("sys.modules[" + repr(name) + "]=_module\n")
        bootstrap.append("exec(" + repr(source) + ",_module.__dict__)\n")
    source = (ROOT / "scripts/prepare_schema_release.py").read_text(encoding="utf-8")
    source = source.replace("RELEASE_B64 = None", "RELEASE_B64 = " + repr(base64.b64encode(payload).decode("ascii")), 1)
    source = source.replace("FINAL_IMAGE_PROBE = None", "FINAL_IMAGE_PROBE = " + repr(probe), 1)
    prefix_end = source.index('"""', 3) + 3
    source = source[:prefix_end] + "\n" + "".join(bootstrap) + source[prefix_end:]
    ast.parse(source, feature_version=(3, 8))
    output = ROOT / ".docker/monipan-schema-prepare-20261018-v2.py"
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    print("ARTIFACT=" + str(output))
    print("SHA256=" + hashlib.sha256(output.read_bytes()).hexdigest())
    print("BYTES=" + str(output.stat().st_size))
    print("FINAL_IMAGE_FILES_CHECKED=" + str(len(file_hashes)))
    return output


if __name__ == "__main__":
    build()
