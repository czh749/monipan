"""Upgrade only an owned restore drill and verify a compatible recovery image.

Run beside restore_mysql_drill.py. Does not deploy or migrate production,
delete volumes, publish ports, or enable background collection.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from restore_mysql_drill import check_backup, command, inspect, require, validate_target

PREFIX = "STAGE1_RESTORE_RESULT="
PROBE = r'''
import hashlib,json,os
from sqlalchemy import MetaData,select
from app.database import engine,SessionLocal,get_db
from app.schema import current_revisions,head_revision,upgrade_database,require_current
from app.models import SimulationAccount
from app.auth import get_current_account,get_current_user
from fastapi.testclient import TestClient
from app.main import app
from app.schema_audit import audit,model_schema
from app.database import Base

require_database='monipan_restore'
assert engine.url.database==require_database
assert head_revision()=='20261026_02'
metadata=MetaData();metadata.reflect(bind=engine)
shape={n:[c.name for c in t.columns] for n,t in metadata.tables.items() if n!='alembic_version'}
def snapshot():
    tables=MetaData();tables.reflect(bind=engine)
    result={}
    with engine.connect() as connection:
        for name,columns in shape.items():
            table=tables.tables[name];digest=hashlib.sha256();count=0
            for row in connection.execute(select(*(table.c[c] for c in columns)).order_by(*table.primary_key.columns)):
                digest.update(json.dumps(list(row),default=str,separators=(',',':')).encode());digest.update(b'\n');count+=1
            result[name]={'rows':count,'sha256':digest.hexdigest()}
    return result
before=snapshot()
expected=json.loads(os.environ['MONIPAN_RESTORE_COUNTS_JSON'])
assert all(before[n]['rows']==expected[n] for n in before if n in expected)
with engine.connect() as connection:
    previous=list(current_revisions(connection))
assert previous in (['20261018_01'],['20261026_02'])
if os.environ['MONIPAN_STAGE1_PROBE_MODE']=='upgrade':
    upgrade_database(engine)
with engine.connect() as connection:
    require_current(connection)
    assert not audit(connection,model_schema(Base.metadata,connection.dialect))['differences']
assert before==snapshot(), 'Migration changed original business values'
with SessionLocal() as db:
    account=db.scalar(select(SimulationAccount).order_by(SimulationAccount.id))
    assert account is not None
    user=account.user
    account_id=account.id
def database():
    with SessionLocal() as db:
        yield db
def current_account():
    with SessionLocal() as db:
        account=db.get(SimulationAccount,account_id)
        account.user
        return account
app.dependency_overrides[get_db]=database
app.dependency_overrides[get_current_account]=current_account
app.dependency_overrides[get_current_user]=lambda:user
with TestClient(app,base_url='https://isolated.test') as client:
    assert client.get('/readyz').status_code==200
    assert client.get('/api/account').status_code==200
    assert client.get('/api/stocks').status_code==200
app.dependency_overrides.clear()
assert before==snapshot(), 'Application checks changed original business values'
print('STAGE1_RESTORE_RESULT='+json.dumps({'status':'passed','previous_head':previous,
      'head':'20261026_02','original_rows_unchanged':True,'http_checks':['readyz','account','stocks'],
      'table_counts':{n:v['rows'] for n,v in before.items()},
      'scope':'Existing-account API through test-only dependency overrides; password hashes compared, no real login or production TLS test'}))
'''


def verify_image(tag, expected):
    data = json.loads(command(["docker", "image", "inspect", tag]))[0]
    require(data["Id"] in {expected["image_id"], expected["config_sha256"]}, "Image identity differs from delivery manifest")
    require(data["RootFS"]["Layers"] == expected["layers"], "Image content differs from tested layers")
    return data


def check(state_path, manifest_path, project):
    os.umask(0o077)
    state_path = state_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(state["status"] == "import_verified_application_pending", "Restore is not awaiting Stage 1 checks")
    require(state["database"] == "monipan_restore", "Unexpected database")
    token = state["token"]
    require(bool(re.fullmatch(r"[0-9a-f]{24}", token)), "Invalid drill token")
    require(check_backup(Path(state["backup"])) == state["backup_sha256"], "Backup changed after import")
    environment = {k:v for k,v in os.environ.items() if not k.startswith(("MONIPAN_","COMPOSE_"))}
    environment["COMPOSE_PROJECT_NAME"] = "monipan"
    source_id = command(["docker","compose","ps","-q","mysql"],cwd=project,env=environment)
    source = inspect(source_id)
    require(source["Id"] == state["source_container"] and source["State"]["Running"], "Source identity or state changed")
    restored = inspect(state["container"])
    validate_target(restored,source,token)
    require(restored["State"]["Running"], "Restored MySQL is stopped")
    password = dict(item.split("=",1) for item in restored["Config"]["Env"] if "=" in item)["MYSQL_ROOT_PASSWORD"]
    require(bool(re.fullmatch(r"[0-9a-f]{64}",password)), "Unexpected isolated password format")
    results = {}
    for mode,name in (("upgrade","candidate"),("verify","recovery")):
        image = verify_image(manifest["images"][name]["tag"],manifest["images"][name])
        require(image["Config"].get("User")=="monipan", "Backend must run as monipan")
        env = dict(environment,MONIPAN_DATABASE_URL="mysql+pymysql://root:"+password+"@127.0.0.1:3306/monipan_restore?charset=utf8mb4",
                   MONIPAN_ENVIRONMENT="production",MONIPAN_COOKIE_SECURE="1",MONIPAN_DISABLE_MARKET_LOOP="1",
                   MONIPAN_RESTORE_COUNTS_JSON=json.dumps(state["table_counts"]),MONIPAN_STAGE1_PROBE_MODE=mode)
        label = "com.monipan.stage1-restore-app"
        args=["docker","create","-i","--pull=never","--name","monipan-stage1-"+mode+"-"+token,
              "--label",label+"="+token,"--network","container:"+restored["Id"],
              "--memory","512m","--memory-swap","512m","--cpus","0.5","--pids-limit","128"]
        for key in env:
            if key.startswith("MONIPAN_"):
                args += ["--env",key]
        identity=command(args+[image["Id"],"python","-"],env=env)
        try:
            app=inspect(identity)
            require(app["Id"] not in {source["Id"],restored["Id"]}, "Application identity collision")
            require(app["Config"].get("Labels",{}).get(label)==token, "Application ownership mismatch")
            require(app["HostConfig"]["NetworkMode"]=="container:"+restored["Id"], "Application network mismatch")
            require(not app.get("Mounts") and not app["HostConfig"].get("PortBindings"), "Application has unsafe mounts or ports")
            validate_target(inspect(restored["Id"]),source,token)
            result=subprocess.run(["docker","start","--attach","--interactive",identity],input=PROBE.encode(),
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
            (state_path.parent/("stage1-"+mode+".log")).write_bytes(result.stdout+b"\nSTDERR\n"+result.stderr)
            matches=[line[len(PREFIX):] for line in result.stdout.decode(errors="replace").splitlines() if line.startswith(PREFIX)]
            require(result.returncode==0 and len(matches)==1,"Stage 1 probe failed; isolated DB and private logs retained")
            results[name]=json.loads(matches[0])
            require(results[name]["status"]=="passed","Stage 1 probe did not pass")
            print(name.upper()+"_RESTORE_CHECK_PASSED",flush=True)
        finally:
            owned=inspect(identity)
            require(owned["Config"].get("Labels",{}).get(label)==token,"Cleanup ownership mismatch")
            command(["docker","rm","-f",identity])
    require(inspect(source["Id"])["State"]["Running"],"Source database stopped")
    state.update(status="stage1_upgrade_and_compatibility_verified",stage1_report=results,
                 stage1_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("STAGE1_RESTORED_UPGRADE_AND_RECOVERY_VERIFIED",flush=True)
    print("RESTORE_STATE="+str(state_path),flush=True)
    print(json.dumps(results,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state",type=Path)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--project",type=Path,default=Path("/opt/monipan"))
    args=parser.parse_args()
    try:
        check(args.state,args.manifest,args.project)
    except Exception as error:
        print("STAGE1_RESTORE_CHECK_FAILED "+type(error).__name__+": "+str(error),flush=True)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
