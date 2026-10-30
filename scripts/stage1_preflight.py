"""Read-only production evidence; never migrates, restarts or sends mail."""

import argparse
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

PROBE = '''
import hashlib,json
from sqlalchemy import MetaData,select
from app.database import engine
from app.schema import current_revisions
protected={'users','simulation_accounts','positions','orders','trades','account_transactions',
           'watchlist_items','agent_runs','agent_evidence','agent_recommendations'}
metadata=MetaData()
metadata.reflect(bind=engine)
result={'tables':{}}
with engine.connect() as connection:
    if connection.dialect.name == 'mysql':
        connection.exec_driver_sql('START TRANSACTION READ ONLY')
    result['schema_versions']=list(current_revisions(connection))
    for name in sorted(protected & set(metadata.tables)):
        table=metadata.tables[name]
        digest=hashlib.sha256()
        count=0
        for row in connection.execute(select(table).order_by(*table.primary_key.columns)).mappings():
            digest.update(json.dumps(dict(row),sort_keys=True,default=str,separators=(',',':')).encode())
            digest.update(b'\\n')
            count+=1
        result['tables'][name]={'rows':count,'sha256':digest.hexdigest()}
print(json.dumps(result))
'''


def command(args, *, cwd=None, source=None, timeout=45):
    process = subprocess.run(args, cwd=cwd, input=source, text=True, encoding="utf-8",
                             capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError("Command failed; diagnostic output suppressed to protect configuration")
    return process.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    if not (project / "compose.yaml").is_file():
        parser.error("Project has no compose.yaml")
    base = ["docker", "compose", "--project-directory", str(project), "-p", "monipan", "-f", str(project / "compose.yaml")]
    report = {"observed_at": datetime.now(UTC).isoformat(), "scope": "read-only server preflight", "services": {}}
    for service in ("mysql", "backend", "frontend"):
        identity = command(base + ["ps", "-q", service], cwd=project)
        if not identity or "\n" in identity:
            raise RuntimeError("Expected exactly one running " + service)
        data = json.loads(command(["docker", "inspect", identity]))[0]
        labels = data["Config"].get("Labels", {})
        if labels.get("com.docker.compose.project") != "monipan" or labels.get("com.docker.compose.service") != service:
            raise RuntimeError("Unexpected production service identity")
        report["services"][service] = {"container": data["Id"], "image": data["Image"],
            "health": data["State"].get("Health", {}).get("Status", "unknown"),
            "volumes": [m["Name"] for m in data["Mounts"] if m["Type"] == "volume"]}
    report["database"] = json.loads(command(base + ["exec", "-T", "backend", "python", "-"], cwd=project, source=PROBE))
    report["timers"] = {}
    for timer in ("monipan-backup.timer", "monipan-monitor.timer"):
        try:
            values = command(["systemctl", "show", timer, "--property=LoadState,ActiveState,UnitFileState,LastTriggerUSec,NextElapseUSecRealtime"])
            report["timers"][timer] = dict(line.split("=", 1) for line in values.splitlines() if "=" in line)
        except (RuntimeError, OSError):
            report["timers"][timer] = {"status": "unavailable"}
    backups = sorted((project / "backups/daily").glob("*.sql.gz"), key=lambda p: p.stat().st_mtime)
    report["backup"] = {"status": "missing"}
    if backups:
        backup = backups[-1]
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        sidecar = backup.with_suffix(backup.suffix + ".sha256")
        with gzip.open(backup, "rb") as source:
            while source.read(1024 * 1024):
                pass
        checksum_parts = sidecar.read_text().split() if sidecar.is_file() else []
        report["backup"] = {"status": "verified" if checksum_parts and checksum_parts[0] == digest else "checksum_missing_or_mismatch",
                            "file": backup.name, "sha256": digest,
                            "age_seconds": round(datetime.now(UTC).timestamp() - backup.stat().st_mtime)}
    report["mail_delivery"] = "requires actual recipient confirmation; this check sends no email"
    report["production_release"] = "not performed"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        args.report.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
