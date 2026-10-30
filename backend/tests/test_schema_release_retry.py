"""Regression checks for permission repair and the retained-clone retry path."""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys

import pytest

from .artifact_support import archived_artifact


ROOT=Path(__file__).resolve().parents[2]


def load_host(*,artifact=False):
    sys.path.insert(0,str(ROOT/"scripts"))
    try:
        path=archived_artifact(ROOT,".docker/monipan-schema-retry-20261018.py") if artifact else ROOT/"scripts/retry_schema_release.py"
        spec=importlib.util.spec_from_file_location("schema_release_retry_host",path)
        host=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        return host
    finally:
        sys.path.pop(0)


def test_retry_artifact_contains_fixed_release_and_no_automatic_main_execution(tmp_path):
    artifact=archived_artifact(ROOT,".docker/monipan-schema-retry-20261018.py")
    ast.parse(artifact.read_text(encoding="utf-8"),feature_version=(3,8))
    scope=runpy.run_path(str(artifact),run_name="artifact_validation")
    release=scope["release"]
    import base64
    payload=base64.b64decode(release.RELEASE_B64,validate=True)
    assert hashlib.sha256(payload).hexdigest()=="3896cd7cb5d61a69b1ea16d662fb8f9e47244f52adecd266e2c6c7e4448db052"
    release.unpack_release(payload,tmp_path/"context")
    assert "RUN chmod -R a+rX" in (tmp_path/"context/Dockerfile").read_text()
    assert '"release_file"] = relative' in release.FINAL_IMAGE_PROBE


def test_file_permission_failure_reports_known_release_path_without_exception_secrets(capsys):
    import pathlib
    sys.path.insert(0,str(ROOT/"scripts"))
    try:
        from build_schema_preparation import final_image_probe
    finally:
        sys.path.pop(0)
    name="vendor/alembic/__init__.py"
    source=final_image_probe({}, {name:"0"*64})
    original=pathlib.Path
    class BlockedPath:
        def __init__(self,*args):pass
        def __truediv__(self,other):return self
        def read_bytes(self):raise PermissionError("synthetic-secret-value-must-not-appear")
    try:
        pathlib.Path=BlockedPath
        with pytest.raises(SystemExit) as result:
            exec(compile(source,"<permission-probe-test>","exec"),{})
    finally:
        pathlib.Path=original
    assert result.value.code==1
    output=capsys.readouterr().out
    report=json.loads(output.split("RESTORE_APPLICATION_RESULT=",1)[1])
    assert report=={"status":"failed","error_type":"PermissionError",
                    "stage":"verify_candidate_files","release_file":name}
    assert "synthetic-secret" not in output


def test_diagnostic_uses_non_root_image_without_network_mounts_or_production_environment(monkeypatch):
    host=load_host()
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if args[1:3]==["image","inspect"]:
            return json.dumps([{"Id":host.FAILED_IMAGE,"Config":{"User":"monipan",
                "Labels":{"com.monipan.schema-baseline":"20261018_01"}}}])
        assert args[1]=="run" and "--rm" in args and "--read-only" in args
        assert args[args.index("--network")+1]=="none"
        assert args[args.index("--entrypoint")+1]=="python"
        assert not any(arg in args for arg in ("--env","--volume","--mount","--privileged","--user"))
        return json.dumps({"uid":999,"gid":999,"paths":{"/opt/monipan-vendor":{"readable":False}}})
    monkeypatch.setattr(host,"command",command)
    assert host.permission_report(host.FAILED_IMAGE)["uid"]==999
    assert len(calls)==2


def prepare_fixture(tmp_path,monkeypatch,*,stale=False,old_readable=False,failed_probe=False,
                    old_file_error="PermissionError",missing_old_file=False,fixed_damage=None,artifact=False):
    host=load_host(artifact=artifact)
    project=tmp_path/"project"
    project.mkdir()
    (project/"compose.yaml").write_text("preserve live compose")
    root=tmp_path/"release"
    root.mkdir()
    (root/"context").mkdir()
    (root/"context/old-image-marker").write_text("keep failed context")
    drill=root/"drill-backup/restore-run-original/restore-state.json"
    drill.parent.mkdir(parents=True)
    drill.write_text(json.dumps({"status":"import_verified_application_pending"}))
    services={name:{"Id":char*64} for name,char in zip(("backend","frontend","mysql"),"abc")}
    state={"status":"preparation_failed","production_deployed":False,"production_schema_modified":False,
           "old_image":host.release.PARENT_IMAGE,"candidate_image":host.FAILED_IMAGE,
           "project":str(project),"production_container_ids":{name:value["Id"] for name,value in services.items()},
           "restore_state":str(drill),"old_image_tag":"monipan-backend:retained","original_file_hashes":{}}
    state_path=root/"release-state.json"
    state_path.write_text(json.dumps(state))
    if stale:services["backend"]={"Id":"9"*64}
    calls=[]
    monkeypatch.setattr(host.release,"production_containers",lambda _:services)
    monkeypatch.setattr(host.release,"verify_unchanged",lambda *args:calls.append("verify_production_unchanged"))
    monkeypatch.setattr(host,"validate_retained_drill",lambda *args:drill)
    monkeypatch.setattr(host.release,"RELEASE_B64","Zml4ZWQ=")
    monkeypatch.setattr(host.release,"FINAL_IMAGE_PROBE","final image probe")
    def unpack(payload,destination):
        assert payload==b"fixed" and destination!=root/"context"
        destination.mkdir()
        calls.append("unpack_fixed_context")
    monkeypatch.setattr(host.release,"unpack_release",unpack)
    fixed_image="sha256:"+"8"*64
    def build(old,old_tag,new_tag,context,log):
        assert old==host.release.PARENT_IMAGE and old_tag==state["old_image_tag"]
        assert "permissions-" in new_tag
        calls.append("build_fixed_image")
        return fixed_image
    monkeypatch.setattr(host.release,"build_candidate",build)
    def permissions(image):
        calls.append("permissions:"+image)
        paths={"/opt/monipan-vendor":{"readable":True},
               "/opt/monipan-vendor/alembic/__init__.py":{"readable":True},
               "/app/app/main.py":{"readable":True}}
        dependency="/opt/monipan-vendor/alembic/__init__.py"
        if image==host.FAILED_IMAGE:
            paths[dependency]={"readable":old_readable}
            if not old_readable:paths[dependency]["error_type"]=old_file_error
            if missing_old_file:del paths[dependency]
        elif fixed_damage=="missing_file":del paths[dependency]
        elif fixed_damage=="unreadable_file":paths[dependency]={"readable":False,"error_type":"PermissionError"}
        return {"uid":999,"paths":paths}
    monkeypatch.setattr(host,"permission_report",permissions)
    def command(args,**kwargs):
        assert args==["docker","image","inspect",state["old_image_tag"]]
        calls.append("verify_old_image_tag")
        return json.dumps([{"Id":host.release.PARENT_IMAGE}])
    monkeypatch.setattr(host,"command",command)
    def check(path,project_arg,source,*,candidate_image):
        assert path==drill and project_arg==project and candidate_image==fixed_image
        assert source=="final image probe"
        calls.append("check_original_clone")
        if failed_probe:raise RuntimeError("Application checks failed; isolated DB retained")
        drill.write_text(json.dumps({"status":"restore_application_verified_and_cleaned",
            "temporary_resources_cleaned":True,"application_report":{"status":"passed",
                "candidate_files_verified":154,"source_overlay_applied":False}}))
    monkeypatch.setattr(host,"check",check)
    return host,state_path,drill,calls


@pytest.mark.parametrize("artifact",[False,True],ids=["source","upload-artifact"])
def test_retry_reuses_the_original_clone_and_records_the_fixed_image(tmp_path,monkeypatch,artifact):
    host,path,drill,calls=prepare_fixture(tmp_path,monkeypatch,artifact=artifact)
    host.retry(path)
    state=json.loads(path.read_text())
    assert state["status"]=="candidate_image_verified_production_not_deployed"
    assert state["production_deployed"] is False and state["production_schema_modified"] is False
    assert state["restore_state"]==str(drill)
    assert state["candidate_image"]=="sha256:"+"8"*64
    assert state["permission_retries"][0]["failed_candidate_image"]==host.FAILED_IMAGE
    original=state["permission_retries"][0]["failed_image_permission_report"]["paths"]
    assert original["/opt/monipan-vendor"]["readable"] is True
    assert original["/opt/monipan-vendor/alembic/__init__.py"]["error_type"]=="PermissionError"
    assert Path(state["candidate_context"]).parent.parent==path.parent
    assert (path.parent/"context/old-image-marker").read_text()=="keep failed context"
    assert Path(state["project"],"compose.yaml").read_text()=="preserve live compose"
    assert calls.count("check_original_clone")==1


@pytest.mark.parametrize("reason",["stale","old_readable"])
@pytest.mark.parametrize("artifact",[False,True],ids=["source","upload-artifact"])
def test_unexpected_production_or_permission_state_blocks_rebuild(tmp_path,monkeypatch,reason,artifact):
    host,path,drill,calls=prepare_fixture(tmp_path,monkeypatch,artifact=artifact,**{reason:True})
    with pytest.raises(RuntimeError):host.retry(path)
    assert "build_fixed_image" not in calls and "check_original_clone" not in calls
    assert json.loads(path.read_text())["status"]=="preparation_failed"


@pytest.mark.parametrize("options",[{"missing_old_file":True},{"old_file_error":"FileNotFoundError"}])
@pytest.mark.parametrize("artifact",[False,True],ids=["source","upload-artifact"])
def test_missing_dependency_or_unrelated_error_does_not_authorize_permission_repair(tmp_path,monkeypatch,options,artifact):
    host,path,drill,calls=prepare_fixture(tmp_path,monkeypatch,artifact=artifact,**options)
    original=path.read_bytes()
    with pytest.raises(RuntimeError,match="permission defect"):host.retry(path)
    assert "build_fixed_image" not in calls and "check_original_clone" not in calls
    assert path.read_bytes()==original
    assert not list(path.parent.glob("permission-retry-*"))


@pytest.mark.parametrize("damage",["missing_file","unreadable_file"])
@pytest.mark.parametrize("artifact",[False,True],ids=["source","upload-artifact"])
def test_fixed_image_requires_all_expected_paths_readable_before_reusing_clone(tmp_path,monkeypatch,damage,artifact):
    host,path,drill,calls=prepare_fixture(tmp_path,monkeypatch,fixed_damage=damage,artifact=artifact)
    with pytest.raises(RuntimeError,match="inaccessible code files"):host.retry(path)
    assert "build_fixed_image" in calls and "check_original_clone" not in calls
    state=json.loads(path.read_text())
    assert state["status"]=="preparation_failed" and state["production_schema_modified"] is False
    assert json.loads(drill.read_text())["status"]=="import_verified_application_pending"


@pytest.mark.parametrize("artifact",[False,True],ids=["source","upload-artifact"])
def test_failed_recheck_retains_recovery_records_and_does_not_adopt_production(tmp_path,monkeypatch,artifact):
    host,path,drill,calls=prepare_fixture(tmp_path,monkeypatch,failed_probe=True,artifact=artifact)
    with pytest.raises(RuntimeError,match="isolated DB retained"):host.retry(path)
    state=json.loads(path.read_text())
    assert state["status"]=="preparation_failed" and state["production_deployed"] is False
    assert state["candidate_image"]==host.FAILED_IMAGE
    assert state["permission_retries"][0]["candidate_image"]=="sha256:"+"8"*64
    assert state["permission_retries"][0]["status"]=="failed"
    assert json.loads(drill.read_text())["status"]=="import_verified_application_pending"


def test_clone_validation_rejects_state_outside_original_drill(tmp_path):
    host=load_host()
    root=tmp_path/"release"
    root.mkdir()
    outside=tmp_path/"other-state.json"
    outside.write_text("{}")
    with pytest.raises(RuntimeError,match="outside this release"):
        host.validate_retained_drill({"restore_state":str(outside)},root,{})
