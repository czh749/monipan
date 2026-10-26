"""Exercise the application probe with a disposable SQLite fixture, not production."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts/restore_application_probe.py"
FIXTURE = '''
from datetime import datetime, timedelta
from decimal import Decimal
import os
from sqlalchemy import text
from app.database import Base, engine, SessionLocal
from app.schema import adopt_baseline
from app.models import User, SimulationAccount, Stock, Position, Order, Trade, AccountTransaction, RateLimitCounter
from app.models import AgentRun, AgentEvidence, AgentRecommendation
Base.metadata.create_all(engine)
adopt_baseline(engine)
with SessionLocal() as db:
    first=User(username="restored_first", status="ACTIVE")
    second=User(username="restored_second", status="ACTIVE")
    db.add_all([first,second]); db.flush()
    account=SimulationAccount(user_id=first.id,initial_cash=Decimal("1000000"),available_cash=Decimal("998995"))
    other=SimulationAccount(user_id=second.id,initial_cash=Decimal("2000000"),available_cash=Decimal("2000000"))
    stock=Stock(symbol="600519",name="fixture",exchange="SH",industry="fixture",prev_close=10,price=10,
                open_price=10,high_price=10,low_price=10,volume=100)
    db.add_all([account,other,stock]);db.flush()
    order=Order(order_no="restore_fixture_order",account_id=account.id,stock_id=stock.id,side="BUY",
                order_type="MARKET",quantity=100,filled_quantity=100,price=10,fee=5,status="FILLED")
    db.add(order);db.flush()
    db.add(Trade(trade_no="restore_fixture_trade",order_id=order.id,account_id=account.id,stock_id=stock.id,
                 side="BUY",quantity=100,price=10,amount=1000,fee=5))
    db.add(Position(account_id=account.id,stock_id=stock.id,quantity=100,average_cost=Decimal("10.05")))
    db.add(AccountTransaction(account_id=account.id,order_id=order.id,transaction_type="BUY",amount=-1005,
                             balance_after=998995))
    run=AgentRun(user_id=first.id,account_id=account.id,stock_id=stock.id,status="COMPLETED",
                 model_provider="fixture",model_name="fixture")
    db.add(run);db.flush()
    db.add(AgentEvidence(run_id=run.id,evidence_key="E1",tool_name="get_stock_quote",
                         evidence_type="quote",excerpt="fixture evidence"))
    db.add(AgentRecommendation(run_id=run.id,risk_level="LOW",confidence=Decimal("0.5"),action="WATCH",
                               summary="fixture",reasoning="fixture",output_hash="e"*64,evidence_keys=["E1"]))
    db.add(RateLimitCounter(scope="expired_fixture",identity_hash="f"*64,
                            window_start=datetime.now()-timedelta(days=3),expires_at=datetime.now()-timedelta(days=2),count=1))
    db.commit()
    if os.environ.get("FIXTURE_DAMAGE")=="cash":
        account.available_cash=998994;db.commit()
    if os.environ.get("FIXTURE_DAMAGE")=="position":
        db.query(Position).update({Position.quantity:99});db.commit()
'''


def execute_probe(tmp_path, damage=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MONIPAN_")}
    environment.update(MONIPAN_DATABASE_URL="sqlite:///" + (tmp_path / "restored.db").as_posix(),
                       MONIPAN_ENVIRONMENT="test", MONIPAN_DISABLE_MARKET_LOOP="1", MONIPAN_COOKIE_SECURE="1")
    if damage:
        environment["FIXTURE_DAMAGE"] = damage
    preparation = subprocess.run([sys.executable, "-c", FIXTURE], cwd=ROOT / "backend", env=environment,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert preparation.returncode == 0, preparation.stderr
    source = PROBE.read_text(encoding="utf-8").split('\nif __name__ == "__main__":')[0]
    source += '''
try:
    result=run_probe(allow_sqlite=True)
except ProbeFailure as exc:
    result={"status":"failed","check":str(exc)}
print("RESULT="+json.dumps(result))
'''
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT / "backend", env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT=")))


def test_probe_exercises_registration_login_restored_reads_and_isolation(tmp_path):
    report = execute_probe(tmp_path)
    assert report["status"] == "passed", report
    assert report["restored_accounts_read"] == 2
    assert report["cash_ledger_reconciled_accounts"] == 2
    assert report["foreign_keys_checked"] > 10
    assert report["original_rows_unchanged"] is True
    assert report["synthetic_register_login_logout"] == "passed"
    assert report["agent_runs_read_and_isolated"] == 1
    assert report["original_user_passwords_tested"] is False


@pytest.mark.parametrize("damage,check", [("cash", "account_cash_ledger"),
                                         ("position", "position_quantity_replay")])
def test_probe_rejects_broken_cash_or_position_reconciliation(tmp_path, damage, check):
    assert execute_probe(tmp_path, damage) == {"status": "failed", "check": check}


def load_host():
    # Load the standalone script the same way the Linux scripts directory does.
    location = str(ROOT / "scripts")
    sys.path.insert(0, location)
    try:
        spec = importlib.util.spec_from_file_location("restore_app_host", ROOT / "scripts/check_restore_application.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(location)


def test_application_container_must_share_only_the_isolated_network():
    host = load_host()
    source, backend, restored = ({"Id": char*64, "Image": "sha256:"+char*64} for char in "abc")
    token = "d"*24
    app = {"Id":"e"*64,"Image":backend["Image"],"Mounts":[],
           "Config":{"Labels":{host.APP_LABEL:token},"Env":[
               "MONIPAN_DATABASE_URL=mysql+pymysql://root:"+"f"*64+"@127.0.0.1:3306/monipan_restore?charset=utf8mb4",
               "MONIPAN_DISABLE_MARKET_LOOP=1","MONIPAN_ENVIRONMENT=production","MONIPAN_COOKIE_SECURE=1"]},
           "HostConfig":{"NetworkMode":"container:"+restored["Id"],"PortBindings":{},
                         "Memory":512*1024*1024,"Privileged":False}}
    host.validate_application(app,restored,backend,source,token)
    app["HostConfig"]["NetworkMode"]="monipan_default"
    with pytest.raises(RuntimeError,match="network mismatch"):
        host.validate_application(app,restored,backend,source,token)


def test_application_container_cannot_use_production_database_hostname():
    host = load_host()
    source, backend, restored = ({"Id": char*64, "Image": "sha256:"+char*64} for char in "abc")
    token="d"*24
    app={"Id":"e"*64,"Image":backend["Image"],"Mounts":[],
         "Config":{"Labels":{host.APP_LABEL:token},"Env":[
             "MONIPAN_DATABASE_URL=mysql+pymysql://root:"+"f"*64+"@mysql:3306/monipan?charset=utf8mb4",
             "MONIPAN_DISABLE_MARKET_LOOP=1","MONIPAN_ENVIRONMENT=production","MONIPAN_COOKIE_SECURE=1"]},
         "HostConfig":{"NetworkMode":"container:"+restored["Id"],"PortBindings":{},
                       "Memory":512*1024*1024,"Privileged":False}}
    with pytest.raises(RuntimeError,match="database URL"):
        host.validate_application(app,restored,backend,source,token)


class HostDocker:
    def __init__(self, host, *, shared_volume=False, failed_probe=False):
        self.host = host
        self.shared_volume = shared_volume
        self.failed_probe = failed_probe
        self.candidate = {"Id":"sha256:"+"7"*64,"Config":{
            "Labels":{"com.monipan.schema-baseline":"20261018_01"},"User":"monipan"}}
        self.calls = []
        self.token = "d" * 24
        self.source = {"Id":"a"*64,"Image":"sha256:"+"a"*64,"State":{"Running":True},
                       "Config":{"Labels":{"com.docker.compose.project":"monipan","com.docker.compose.service":"mysql"}},
                       "Mounts":[{"Source":"/production/mysql"}]}
        self.backend = {"Id":"b"*64,"Image":"sha256:"+"b"*64,"State":{"Running":True},
                        "Config":{"Labels":{"com.docker.compose.project":"monipan","com.docker.compose.service":"backend"}}}
        self.restored = {"Id":"c"*64,"Image":self.source["Image"],"State":{"Running":True},
                         "Config":{"Labels":{"com.monipan.restore-drill":self.token},
                                   "Env":["MYSQL_ROOT_PASSWORD="+"f"*64]},
                         "HostConfig":{"NetworkMode":"none","Memory":768*1024*1024,
                                       "MemorySwap":768*1024*1024,"NanoCpus":750_000_000,
                                       "PortBindings":{},"Privileged":False},
                         "Mounts":[{"Type":"volume","Source":"/temporary/mysql",
                                    "Destination":"/var/lib/mysql","Name":"9"*64}]}
        self.app = {"Id":"e"*64,"Image":self.backend["Image"],"Mounts":[],
                    "Config":{"Labels":{host.APP_LABEL:self.token},"Env":[]},
                    "HostConfig":{"NetworkMode":"container:"+self.restored["Id"],"PortBindings":{},
                                  "Memory":512*1024*1024,"Privileged":False}}

    def inspect(self, container):
        return {item["Id"]:item for item in (self.source,self.backend,self.restored,self.app)}[container]

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["compose","ps"]:
            return (self.source if args[-1] == "mysql" else self.backend)["Id"]
        if args[1:3] == ["image","inspect"]:
            return json.dumps([self.candidate])
        if args[1] == "create":
            self.app["Image"] = args[-3]
            self.app["Config"]["Env"] = [key+"="+value for key,value in kwargs["env"].items() if key.startswith("MONIPAN_")]
            assert kwargs["env"]["MONIPAN_DATABASE_URL"] not in args
            return self.app["Id"]
        if args[1] == "ps":
            return self.restored["Id"] + ("\n"+"8"*64 if self.shared_volume else "")
        assert args[1] in ("stop","rm")
        assert args[-1] in (self.app["Id"],self.restored["Id"])
        return args[-1]

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[1] == "start":
            assert args[-1] == self.app["Id"]
            report={"status":"failed" if self.failed_probe else "passed"}
            return subprocess.CompletedProcess(args,1 if self.failed_probe else 0,
                (self.host.PREFIX+json.dumps(report)+"\n").encode(),b"")
        assert args[1:3] == ["volume","inspect"]
        return subprocess.CompletedProcess(args,1,b"",b"Error response from daemon: no such volume")


def prepare_host(tmp_path, monkeypatch, **options):
    host=load_host()
    fake=HostDocker(host,**options)
    state={"status":"import_verified_application_pending","database":"monipan_restore","token":fake.token,
           "backup":str(tmp_path/"backup.sql.gz"),"backup_sha256":"digest",
           "source_container":fake.source["Id"],"container":fake.restored["Id"],"table_counts":{},"elapsed_seconds":16.37}
    path=tmp_path/"restore-state.json"
    path.write_text(json.dumps(state),encoding="utf-8")
    monkeypatch.setattr(host,"check_backup",lambda _:"digest")
    monkeypatch.setattr(host,"command",fake.command)
    monkeypatch.setattr(host,"inspect",fake.inspect)
    monkeypatch.setattr(host.subprocess,"run",fake.run)
    return host,fake,path


def test_successful_probe_cleans_only_owned_temporary_resources(tmp_path,monkeypatch):
    host,fake,path=prepare_host(tmp_path,monkeypatch)
    host.check(path,tmp_path,"print('fixture')")
    state=json.loads(path.read_text())
    assert state["status"]=="restore_application_verified_and_cleaned"
    assert state["temporary_resources_cleaned"] is True
    removed=[args[-1] for args in fake.calls if args[1]=="rm"]
    assert removed==[fake.app["Id"],fake.restored["Id"]]
    assert all(fake.source["Id"]!=args[-1] for args in fake.calls if args[1] in ("stop","rm"))


def test_failed_probe_retains_restored_database_for_diagnosis(tmp_path,monkeypatch):
    host,fake,path=prepare_host(tmp_path,monkeypatch,failed_probe=True)
    with pytest.raises(RuntimeError,match="Application checks failed"):
        host.check(path,tmp_path,"print('fixture')")
    assert not any(args[1] in ("stop","rm") and args[-1]==fake.restored["Id"] for args in fake.calls)
    assert [args[-1] for args in fake.calls if args[1]=="rm"]==[fake.app["Id"]]


def test_shared_volume_is_never_deleted(tmp_path,monkeypatch):
    host,fake,path=prepare_host(tmp_path,monkeypatch,shared_volume=True)
    with pytest.raises(RuntimeError,match="Another container"):
        host.check(path,tmp_path,"print('fixture')")
    assert not any(args[1] in ("stop","rm") and args[-1]==fake.restored["Id"] for args in fake.calls)


def test_production_container_in_state_is_rejected_before_launch(tmp_path,monkeypatch):
    host,fake,path=prepare_host(tmp_path,monkeypatch)
    state=json.loads(path.read_text());state["container"]=fake.source["Id"]
    path.write_text(json.dumps(state))
    with pytest.raises(RuntimeError,match="production container"):
        host.check(path,tmp_path,"print('fixture')")
    assert not any(args[1] in ("create","start","rm","stop") for args in fake.calls)


def test_final_candidate_is_used_only_on_the_isolated_restore_network(tmp_path,monkeypatch):
    host,fake,path=prepare_host(tmp_path,monkeypatch)
    host.check(path,tmp_path,"print('fixture')",candidate_image=fake.candidate["Id"])
    creates=[args for args in fake.calls if args[1]=="create"]
    assert len(creates)==1
    assert creates[0][-3]==fake.candidate["Id"]
    assert creates[0][creates[0].index("--network")+1]=="container:"+fake.restored["Id"]
    assert not any(args[1] in ("stop","rm") and args[-1]==fake.backend["Id"] for args in fake.calls)


@pytest.mark.parametrize("reason",["mutable_tag","wrong_label","wrong_user","wrong_image"])
def test_candidate_identity_is_checked_before_any_container_mutation(tmp_path,monkeypatch,reason):
    host,fake,path=prepare_host(tmp_path,monkeypatch)
    candidate=fake.candidate["Id"]
    if reason=="mutable_tag":
        candidate="monipan-backend:latest"
    elif reason=="wrong_label":
        fake.candidate["Config"]["Labels"].clear()
    elif reason=="wrong_user":
        fake.candidate["Config"]["User"]="root"
    else:
        fake.candidate["Id"]="sha256:"+"8"*64
    with pytest.raises(RuntimeError):
        host.check(path,tmp_path,"print('fixture')",candidate_image=candidate)
    assert not any(args[1] in ("create","start","stop","rm") for args in fake.calls)
