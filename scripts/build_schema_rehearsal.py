"""Build the isolated rehearsal artifact with pure Python Alembic dependencies."""

import ast
import base64
import hashlib
import importlib.metadata
import io
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def build():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for project, package in (("alembic", "alembic"), ("Mako", "mako"), ("MarkupSafe", "markupsafe")):
            distribution = importlib.metadata.distribution(project)
            for relative in sorted(distribution.files or [], key=str):
                parts = relative.parts
                if not parts or ".." in parts or "__pycache__" in parts:
                    continue
                if parts[0] != package and not (parts[0].endswith(".dist-info") and
                                                ("licenses" in parts or relative.name in {"METADATA", "WHEEL"})):
                    continue
                if relative.suffix.lower() in {".pyc", ".pyd", ".so", ".dll", ".exe"}:
                    continue
                file = Path(distribution.locate_file(relative))
                if file.is_file():
                    archive.writestr("vendor/" + relative.as_posix(), file.read_bytes())
        for name in ("schema.py", "schema_audit.py", "main.py", "invites.py"):
            archive.writestr("overlay/app/" + name, (ROOT / "backend/app" / name).read_bytes())
        for file in sorted((ROOT / "backend/app/migrations").rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
                archive.writestr("overlay/app/migrations/" + file.relative_to(ROOT / "backend/app/migrations").as_posix(),
                                 file.read_bytes())
        for name in ("migration_baseline_probe.py", "restore_application_probe.py"):
            archive.writestr("scripts/" + name, (ROOT / "scripts" / name).read_bytes())
    bootstrap = ["import sys, types\n"]
    for name in ("restore_mysql_drill", "check_restore_application"):
        source = (ROOT / "scripts" / (name + ".py")).read_text(encoding="utf-8")
        bootstrap.append("_module=types.ModuleType(" + repr(name) + ")\n")
        bootstrap.append("_module.__file__=" + repr("<embedded>/" + name + ".py") + "\n")
        bootstrap.append("sys.modules[" + repr(name) + "]=_module\n")
        bootstrap.append("exec(" + repr(source) + ",_module.__dict__)\n")
    source = (ROOT / "scripts/rehearse_schema_baseline.py").read_text(encoding="utf-8")
    source = source.replace("BUNDLE = None", "BUNDLE = " + repr(base64.b64encode(payload.getvalue()).decode("ascii")), 1)
    # Keep the module docstring before bootstrap imports.
    prefix_end = source.index('"""', 3) + 3
    source = source[:prefix_end] + "\n" + "".join(bootstrap) + source[prefix_end:]
    ast.parse(source, feature_version=(3, 8))
    artifact = ROOT / ".docker/monipan-baseline-rehearsal-20261018.py"
    artifact.parent.mkdir(exist_ok=True)
    with artifact.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    print("ARTIFACT=" + str(artifact))
    print("SHA256=" + hashlib.sha256(artifact.read_bytes()).hexdigest())
    print("BYTES=" + str(artifact.stat().st_size))
    return artifact


if __name__ == "__main__":
    build()
