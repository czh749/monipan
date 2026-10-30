"""Build a self-contained schema audit, using this checkout's model manifest."""

import ast
import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy.dialects import mysql
from app.database import Base
from app import models  # Register the complete current metadata; no DB connection.
from app.schema_audit import model_schema


def build():
    wrapper = (ROOT / "scripts/audit_production_schema.py").read_text(encoding="utf-8")
    source = (ROOT / "backend/app/schema_audit.py").read_text(encoding="utf-8")
    manifest = model_schema(Base.metadata, mysql.dialect())
    assert len(manifest) == 31, "Review baseline table changes before rebuilding this delivery"
    payload = wrapper.replace("EMBEDDED_AUDIT = None", "EMBEDDED_AUDIT = " + repr(source), 1)
    payload = payload.replace("EXPECTED_SCHEMA = None", "EXPECTED_SCHEMA = " + repr(manifest), 1)
    ast.parse(payload)
    artifact = ROOT / ".docker/monipan-schema-audit-20261018.py"
    artifact.parent.mkdir(exist_ok=True)
    with artifact.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    print("SCHEMA_AUDIT_FILE=" + str(artifact))
    print("SHA256=" + checksum)
    return artifact


if __name__ == "__main__":
    build()
