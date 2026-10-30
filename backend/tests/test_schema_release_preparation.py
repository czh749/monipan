"""Safety boundaries for building a release without changing production."""

import copy
import ast
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import runpy

import pytest

from .artifact_support import archived_artifact


ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_release_context_matches_reviewed_manifest_and_has_no_production_configuration(tmp_path):
    host = load_script("prepare_schema_release")
    payload = archived_artifact(ROOT, ".docker/monipan-schema-release-20261018.tar.gz").read_bytes()
    manifest = host.unpack_release(payload, tmp_path / "context")
    assert manifest["baseline_revision"] == "20261018_01"
    assert (tmp_path / "context/app/schema.py").read_bytes() == (ROOT / "backend/app/schema.py").read_bytes()
    assert not any(name in manifest["files"] for name in ("compose.yaml", ".env", "frontend/nginx.conf"))
    with pytest.raises(RuntimeError, match="checksum"):
        host.unpack_release(payload + b"corrupt", tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_upload_artifact_embeds_reviewed_release_and_checks_final_image_without_overlay():
    artifact=archived_artifact(ROOT,".docker/monipan-schema-prepare-20261018-v2.py")
    ast.parse(artifact.read_text(encoding="utf-8"),feature_version=(3,8))
    scope=runpy.run_path(str(artifact),run_name="artifact_validation")
    assert hashlib.sha256(base64.b64decode(scope["RELEASE_B64"])).hexdigest()==scope["RELEASE_SHA256"]
    probe=scope["FINAL_IMAGE_PROBE"]
    tree=ast.parse(probe,feature_version=(3,9))
    assignments={node.targets[0].id:ast.literal_eval(node.value) for node in ast.walk(tree)
                 if isinstance(node,ast.Assign) and len(node.targets)==1
                 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in {"expected","scripts"}}
    assert len(assignments["expected"])==154
    assert all(name.startswith(("app/","vendor/")) for name in assignments["expected"])
    # This archived Stage 0 bundle remains byte-for-byte fixed when the live
    # probe gains a Stage 1 refusal guard. Compare its reviewed historical hash.
    assert hashlib.sha256(assignments["scripts"]["migration_baseline_probe.py"].encode()).hexdigest()=="e32ca52ee0fc7a49ace688f48e642fa3626f2c83881bdad2b0f53a426e72bc88"
    assert "shutil.copy" not in probe and '"source_overlay_applied"] = False' in probe


def test_private_umask_cannot_propagate_root_only_permissions_into_image_sources(tmp_path,monkeypatch):
    host=load_script("prepare_schema_release")
    import os
    calls={}
    original=Path.chmod
    def chmod(path,mode,*args,**kwargs):
        calls[path]=mode
        return original(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,"chmod",chmod)
    old_umask=os.umask(0o077)
    try:
        manifest=host.unpack_release(archived_artifact(ROOT,".docker/monipan-schema-release-20261018.tar.gz").read_bytes(),tmp_path/"context")
    finally:
        os.umask(old_umask)
    for name in manifest["files"]:
        path=tmp_path/"context"/name
        assert calls[path]==0o644
        parent=path.parent
        while parent!=tmp_path/"context":
            assert calls[parent]==0o755
            parent=parent.parent
    assert tmp_path/"context" not in calls  # The enclosing build context stays private.
    dockerfile=(tmp_path/"context/Dockerfile").read_text()
    assert "COPY --chown=monipan:monipan vendor/" in dockerfile
    assert dockerfile.index("RUN chmod -R a+rX")<dockerfile.index("USER monipan")


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/root/escape", "file"),
                                     ("app/alias", "symlink"), ("app\\escape", "file")])
def test_unsafe_archive_members_rejected_before_writing(tmp_path,monkeypatch,name,kind):
    host = load_script("prepare_schema_release")
    data = io.BytesIO()
    with tarfile.open(fileobj=data,mode="w:gz") as archive:
        item=tarfile.TarInfo(name)
        if kind=="symlink":
            item.type=tarfile.SYMTYPE;item.linkname="/opt/monipan/.env"
            archive.addfile(item)
        else:
            item.size=1;archive.addfile(item,io.BytesIO(b"x"))
    payload=data.getvalue()
    monkeypatch.setattr(host,"RELEASE_SHA256",hashlib.sha256(payload).hexdigest())
    with pytest.raises(RuntimeError,match="Unsafe"):
        host.unpack_release(payload,tmp_path/"context")
    assert not (tmp_path/"context").exists()


def test_running_backend_drift_blocks_preparation_without_importing_application(monkeypatch):
    host=load_script("prepare_schema_release")
    calls=[]
    # Explicit construction avoids any database or Docker access in this test.
    def changed(args,**kwargs):
        calls.append(args)
        values=dict(host.BASE_FILES);values["app/main.py"]="invalid"
        return json.dumps({"files":values,"pythonpath":None,"version_table":False})
    monkeypatch.setattr(host,"command",changed)
    with pytest.raises(RuntimeError,match="source or database version changed"):
        host.check_running_source({"Id":"a"*64})
    assert len(calls)==1 and calls[0][:2]==["docker","exec"]
    assert "from app.main" not in calls[0][-1]


def test_file_change_during_rehearsal_is_detected(tmp_path,monkeypatch):
    host=load_script("prepare_schema_release")
    (tmp_path/"compose.yaml").write_text("existing live configuration")
    hashes=host.snapshot_files(tmp_path,tmp_path/"private-copy")
    services={"backend":{"Id":"a"*64}}
    monkeypatch.setattr(host,"production_containers",lambda _:services)
    monkeypatch.setattr(host,"check_running_source",lambda _:None)
    (tmp_path/"compose.yaml").write_text("changed concurrently")
    with pytest.raises(RuntimeError,match="file changed"):
        host.verify_unchanged(tmp_path,services,hashes)
    assert (tmp_path/"private-copy/compose.yaml").read_text()=="existing live configuration"


def image_fixture(host):
    parent={"Id":host.PARENT_IMAGE,"Config":{"User":"monipan","Env":["PATH=/usr/bin"],
            "Cmd":["python","-m","uvicorn","app.main:app"],"WorkingDir":"/app"},
            "RootFS":{"Layers":["sha256:"+"1"*64]}}
    candidate=copy.deepcopy(parent)
    candidate["Id"]="sha256:"+"2"*64
    candidate["Config"]["Labels"]={"com.monipan.schema-baseline":"20261018_01"}
    candidate["Config"]["Env"].append("PYTHONPATH=/opt/monipan-vendor")
    candidate["RootFS"]["Layers"].append("sha256:"+"3"*64)
    return parent,candidate


@pytest.mark.parametrize("damage",[None,"cmd","environment","parent_layers"])
def test_build_retains_parent_configuration_and_disallows_network(tmp_path,monkeypatch,damage):
    host=load_script("prepare_schema_release")
    parent,candidate=image_fixture(host)
    if damage=="cmd":candidate["Config"]["Cmd"]=["unsafe"]
    elif damage=="environment":candidate["Config"]["Env"].append("MONIPAN_DISABLE_MARKET_LOOP=1")
    elif damage=="parent_layers":candidate["RootFS"]["Layers"][0]="different"
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if args[1:3]==["image","tag"]:return ""
        assert args[1:3]==["image","inspect"]
        return json.dumps([candidate if args[-1]=="candidate" else parent])
    def run(args,**kwargs):
        calls.append(args)
        assert "--network=none" in args and "--pull=false" in args
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(host,"command",command)
    monkeypatch.setattr(host.subprocess,"run",run)
    if damage:
        with pytest.raises(RuntimeError):
            host.build_candidate(host.PARENT_IMAGE,"retained","candidate",tmp_path,tmp_path/"build.log")
    else:
        assert host.build_candidate(host.PARENT_IMAGE,"retained","candidate",tmp_path,tmp_path/"build.log")==candidate["Id"]
    assert not any(args[1] in ("compose","stop","rm","exec") for args in calls)


def setup_preparation(tmp_path,monkeypatch,*,failed_probe=False,concurrent_change=False):
    host=load_script("prepare_schema_release")
    content=b"base source"
    monkeypatch.setattr(host,"BASE_FILES",{"app/main.py":hashlib.sha256(content).hexdigest()})
    (tmp_path/"backend/app").mkdir(parents=True)
    (tmp_path/"backend/app/main.py").write_bytes(content)
    (tmp_path/"scripts").mkdir()
    (tmp_path/"scripts/backup_mysql.sh").write_text("fixture")
    (tmp_path/"compose.yaml").write_text("keep this compose config")
    services={name:{"Id":char*64} for name,char in zip(("backend","frontend","mysql"),"abc")}
    monkeypatch.setattr(host,"production_containers",lambda _:services)
    monkeypatch.setattr(host,"check_running_source",lambda _:None)
    monkeypatch.setattr(host,"build_candidate",lambda *args:"sha256:"+"2"*64)
    monkeypatch.setattr(host,"unpack_release",lambda *_:None)
    def backup(args,**kwargs):
        assert args==["/bin/sh","scripts/backup_mysql.sh"]
        Path(kwargs["env"]["MONIPAN_BACKUP_DIR"],"monipan-fixture.sql.gz").write_bytes(b"fixture")
        return "BACKUP_VERIFIED"
    monkeypatch.setattr(host,"command",backup)
    def restore(backup,project):
        path=backup.parent/"restore-state.json"
        path.write_text(json.dumps({"status":"import_verified_application_pending"}))
        return path
    monkeypatch.setattr(host,"restore",restore)
    def probe(path,project,source,*,candidate_image):
        assert candidate_image=="sha256:"+"2"*64
        if failed_probe:raise RuntimeError("Application checks failed; isolated DB retained")
        path.write_text(json.dumps({"status":"restore_application_verified_and_cleaned",
            "temporary_resources_cleaned":True,"application_report":{
                "status":"passed","candidate_files_verified":200,"source_overlay_applied":False}}))
        if concurrent_change:(project/"compose.yaml").write_text("external change")
    monkeypatch.setattr(host,"check",probe)
    return host


def test_successful_preparation_records_candidate_and_preserves_live_files(tmp_path,monkeypatch):
    host=setup_preparation(tmp_path,monkeypatch)
    state_path=host.prepare(tmp_path,b"archive","probe")
    state=json.loads(state_path.read_text())
    assert state["status"]=="candidate_image_verified_production_not_deployed"
    assert state["production_deployed"] is False and state["production_schema_modified"] is False
    assert (tmp_path/"compose.yaml").read_text()=="keep this compose config"
    assert state["production_container_ids"]["mysql"]=="c"*64


@pytest.mark.parametrize("reason",["failed_probe","concurrent_change"])
def test_failed_or_stale_rehearsal_never_marks_candidate_ready(tmp_path,monkeypatch,reason):
    host=setup_preparation(tmp_path,monkeypatch,**{reason:True})
    with pytest.raises(RuntimeError):
        host.prepare(tmp_path,b"archive","probe")
    states=list((tmp_path/"backups").glob("schema-release-*/release-state.json"))
    assert len(states)==1
    state=json.loads(states[0].read_text())
    assert state["status"]=="preparation_failed"
    assert state["production_deployed"] is False
    assert state["old_image"]==host.PARENT_IMAGE
    assert Path(state["restore_state"]).is_file()
    assert (states[0].parent/"original-files/compose.yaml").read_text()=="keep this compose config"
