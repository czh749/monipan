"""Clone a local site's database and rehearse migration and image recovery.

The source MySQL is read only. Every mutation targets a newly named Compose
project and volume. Secrets and SQL stay in .docker; no volumes are deleted.
"""

import argparse
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = '''
import hashlib,json,sys
from sqlalchemy import MetaData,select
from app.database import engine
metadata=MetaData();metadata.reflect(bind=engine)
shape=json.loads(sys.argv[1]) if len(sys.argv)>1 else {n:[c.name for c in t.columns] for n,t in metadata.tables.items() if n!='alembic_version'}
result={}
with engine.connect() as connection:
    connection.exec_driver_sql('START TRANSACTION READ ONLY')
    for name,columns in shape.items():
        table=metadata.tables[name];digest=hashlib.sha256();count=0
        query=select(*(table.c[c] for c in columns)).order_by(*table.primary_key.columns)
        for row in connection.execute(query):
            digest.update(json.dumps(list(row),default=str,separators=(',',':')).encode());digest.update(b'\\n');count+=1
        result[name]={'rows':count,'sha256':digest.hexdigest()}
print(json.dumps({'shape':shape,'tables':result}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-mysql", required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--fallback-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--port", type=int, default=18082)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Expected an unprivileged loopback port")
    token = uuid4().hex[:12]
    project = "monipan-stage1-drill-" + token
    folder = ROOT / ".docker" / project
    folder.mkdir(parents=True)
    report = {"observed_at": datetime.now(UTC).isoformat(), "project": project,
              "scope": "local database clone and real Docker image recovery", "checks": {}}
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "MONIPAN_"))}
    counter = 0

    def run(command, *, source=None, check=True, timeout=240):
        nonlocal counter
        counter += 1
        result = subprocess.run(command, input=source, capture_output=True, env=environment, timeout=timeout)
        (folder / f"command-{counter:02}.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
        if check and result.returncode:
            raise RuntimeError("Drill command failed; private log command-" + f"{counter:02}.log")
        return result

    def inspect(identity):
        return json.loads(run(["docker", "inspect", identity]).stdout)[0]

    source = inspect(args.source_mysql)
    labels = source["Config"].get("Labels", {})
    if labels.get("com.docker.compose.project") != "monipan" or labels.get("com.docker.compose.service") != "mysql":
        raise RuntimeError("Source must be the existing monipan MySQL service")
    if not source["State"]["Running"]:
        raise RuntimeError("Source is not running")
    report["source_container"] = source["Id"]
    report["source_volumes"] = [m["Name"] for m in source["Mounts"] if m["Type"] == "volume"]
    images = {name: inspect(value)["Id"] for name, value in (
        ("candidate", args.candidate_image), ("fallback", args.fallback_image),
        ("frontend", args.frontend_image), ("mysql", "mysql:8.4"))}
    if images["candidate"] == images["fallback"] or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in images.values()):
        raise RuntimeError("Require distinct immutable candidate and compatible fallback images")
    report["images"] = images
    print("Creating a consistent source backup; all writes target " + project, flush=True)
    dump = run(["docker", "exec", source["Id"], "sh", "-c",
                'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump --single-transaction --quick --no-tablespaces --set-gtid-purged=OFF -u"$MYSQL_USER" "$MYSQL_DATABASE"']).stdout
    if not dump or re.search(rb"(?im)^\s*(?:USE\s|CREATE\s+DATABASE\s)", dump):
        raise RuntimeError("Refusing an empty or cross-database dump")
    backup = folder / "source.sql.gz"
    backup.write_bytes(gzip.compress(dump))
    report["source_backup_sha256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
    password = secrets.token_hex(24)
    base = {"services": {
        "mysql": {"image": images["mysql"], "environment": {
            "MYSQL_DATABASE": "monipan_stage1_drill", "MYSQL_USER": "monipan_drill",
            "MYSQL_PASSWORD": password, "MYSQL_ROOT_PASSWORD": secrets.token_hex(24)},
            "command": ["--character-set-server=utf8mb4", "--collation-server=utf8mb4_0900_ai_ci"],
            "volumes": ["drill-data:/var/lib/mysql"], "restart": "no",
            "healthcheck": {"test": ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"], "interval": "3s", "timeout": "3s", "retries": 40}},
        "backend": {"image": images["candidate"], "environment": {
            "MONIPAN_DATABASE_URL": f"mysql+pymysql://monipan_drill:{password}@mysql:3306/monipan_stage1_drill",
            "MONIPAN_ENVIRONMENT": "development", "MONIPAN_COOKIE_SECURE": "0",
            "MONIPAN_DISABLE_MARKET_LOOP": "1", "MONIPAN_RESEARCH_ENABLED": "1"},
            "depends_on": {"mysql": {"condition": "service_healthy"}}, "restart": "no",
            "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=2)"], "interval": "3s", "timeout": "3s", "retries": 12}},
        "frontend": {"image": images["frontend"], "ports": [f"127.0.0.1:{args.port}:80"],
            "depends_on": {"backend": {"condition": "service_healthy"}}, "restart": "no"}},
        "volumes": {"drill-data": {"labels": {"monipan.drill": token}}}}
    config = folder / "compose.json"
    config.write_text(json.dumps(base), encoding="utf-8")
    compose = ["docker", "compose", "-p", project, "-f", str(config)]

    def python(code, *arguments):
        return run(compose + ["run", "--rm", "-T", "--no-deps", "backend", "python", "-c", code, *arguments]).stdout.decode()

    def snapshot(shape=None):
        return json.loads(python(SNAPSHOT, *([json.dumps(shape)] if shape else [])))

    try:
        run(compose + ["up", "-d", "--pull", "never", "--wait", "mysql"])
        clone = inspect(json.loads(run(compose + ["ps", "--format", "json", "mysql"]).stdout)["ID"])
        clone_volumes = [m["Name"] for m in clone["Mounts"] if m["Type"] == "volume"]
        if set(clone_volumes) & set(report["source_volumes"]):
            raise RuntimeError("Clone shares a source data volume")
        report["clone_volumes"] = clone_volumes
        run(compose + ["exec", "-T", "mysql", "sh", "-c",
            'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql -u"$MYSQL_USER" "$MYSQL_DATABASE"'], source=dump)
        before = snapshot()
        print("Checking the restored schema, migrating the clone, and comparing every original column", flush=True)
        python("from app.database import engine; from app.schema import adopt_baseline,current_revisions,upgrade_database; "
               "c=engine.connect(); versions=current_revisions(c); c.close(); "
               "adopt_baseline(engine) if not versions else None; upgrade_database(engine)")
        assert snapshot(before["shape"])["tables"] == before["tables"], "Migration changed restored business rows"
        report["checks"]["all_original_rows_preserved"] = True
        report["original_table_counts"] = {n: t["rows"] for n, t in before["tables"].items()}
        run(compose + ["up", "-d", "--pull", "never", "--wait", "backend", "frontend"])
        assert snapshot(before["shape"])["tables"] == before["tables"], "Startup changed restored rows"
        url = f"http://127.0.0.1:{args.port}"
        assert requests.get(url + "/readyz", timeout=10).status_code == 200
        # Add one clearly named drill account only in the clone. Original
        # accounts and password hashes were compared before this registration.
        invite = json.loads(python("import json; from app.invites import create_invitations; print(json.dumps(create_invitations(count=1,expires_days=1,label='isolated recovery drill')))"))[0]
        access = {"username": "drill_" + token, "password": "Drill9" + secrets.token_hex(12)}
        session = requests.Session()
        response = session.post(url + "/api/auth/register", json=dict(access, invite_code=invite), timeout=15)
        assert response.status_code == 201, "Drill registration failed"
        (folder / "demo-access.json").write_text(json.dumps(dict(access, url=url)), encoding="utf-8")
        python("from app.database import SessionLocal; from app.services.security_catalog import CatalogRecord,replace_directory,SZSE_SOURCE,SSE_SOURCE; "
               "db=SessionLocal(); replace_directory(db,[CatalogRecord('302132','SZSE','中航成飞',None,'创业板','制造业',None,SZSE_SOURCE),"
               "CatalogRecord('600519','SSE','贵州茅台',None,'主板','食品饮料',None,SSE_SOURCE)]); db.close()")
        assert session.post(url + "/api/watchlist/302132", timeout=10).status_code == 201
        payload = {"symbol": "302132", "side": "BUY", "quantity": 100}
        for endpoint in ("/api/orders/preview", "/api/orders"):
            response = session.post(url + endpoint, json=payload, headers={"Idempotency-Key": uuid4().hex}, timeout=10)
            assert response.status_code == 403, endpoint + " returned " + str(response.status_code)
        after_add = snapshot()
        report["checks"]["candidate_http_and_trading_guards"] = True
        bad = folder / "fault.json"
        bad.write_text(json.dumps({"services": {"backend": {"command": ["python", "-c", "raise RuntimeError('controlled drill failure')"]}}}), encoding="utf-8")
        failed = run(compose + ["-f", str(bad), "up", "-d", "--pull", "never", "--wait", "--wait-timeout", "30", "backend"], check=False)
        assert failed.returncode != 0, "Controlled startup failure was not detected"
        report["checks"]["startup_failure_detected"] = True
        recovery = folder / "recovery.json"
        recovery.write_text(json.dumps({"services": {"backend": {"image": images["fallback"]}}}), encoding="utf-8")
        print("Recovering with the compatible prior image while keeping the upgraded database", flush=True)
        run(compose + ["-f", str(recovery), "up", "-d", "--force-recreate", "--pull", "never", "--wait", "backend", "frontend"])
        assert requests.get(url + "/readyz", timeout=10).status_code == 200
        for endpoint in ("/api/orders/preview", "/api/orders"):
            response = session.post(url + endpoint, json=payload, headers={"Idempotency-Key": uuid4().hex}, timeout=10)
            assert response.status_code == 403, endpoint + " returned " + str(response.status_code)
        assert snapshot(after_add["shape"])["tables"] == after_add["tables"], "Recovery changed business rows"
        report["checks"]["compatible_image_recovery_and_guards"] = True
        # Restore the latest candidate so this independent URL remains useful.
        run(compose + ["up", "-d", "--force-recreate", "--pull", "never", "--wait", "backend", "frontend"])
        assert requests.get(url + "/readyz", timeout=10).status_code == 200
        assert inspect(source["Id"])["State"]["Running"]
        report["checks"]["source_service_preserved"] = True
        report.update(status="passed", url=url, limitations=["Local source database clone, not a production snapshot", "Local HTTP development settings, not production TLS"])
        (folder / "demo-access.json").write_text(json.dumps(dict(access, url=url)), encoding="utf-8")
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Drill report: " + str(folder / "report.json"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
