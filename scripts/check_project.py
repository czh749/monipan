"""Run repeatable local/CI checks without reading the deployment configuration."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("backend", "frontend", "mysql")
ARCHIVE_SKIP = "Historical release artifact is not present:"


def test_environment(run_dir):
    # Never inherit a deployment DB, Compose selection, or live API credentials.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MONIPAN_", "COMPOSE_", "LANGCHAIN_", "LANGSMITH_"))
           and key not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "TAVILY_API_KEY"}}
    env.update({
        "MONIPAN_ENVIRONMENT": "test",
        "MONIPAN_DATABASE_URL": "sqlite:///" + (run_dir / "isolated.db").as_posix(),
        "MONIPAN_DISABLE_MARKET_LOOP": "1",
        "MONIPAN_COOKIE_SECURE": "0",
        "LANGSMITH_TRACING": "false",
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(ROOT / "backend"),
    })
    # These are explicit local test-tool overrides, never deployment settings.
    for key in ("MONIPAN_NGINX_BINARY", "MONIPAN_REQUIRE_RELEASE_ARTIFACTS"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def junit_result(path, *, strict_skips=False, require_all=False):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    skipped = [{"test": case.get("classname", "") + "." + case.get("name", ""),
                "reason": case.find("skipped").get("message", "")}
               for case in cases if case.find("skipped") is not None]
    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    passed = len(cases) - len(skipped) - failed
    unexpected = [item for item in skipped
                  if require_all or (strict_skips and ARCHIVE_SKIP not in item["reason"])]
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "ok": passed > 0 and failed == 0 and not unexpected}


def backend_lock_matches_source():
    source = ROOT / "backend/requirements.txt"
    lock = ROOT / "backend/requirements.lock"
    digest = hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return ("# requirements.txt sha256: " + digest) in lock.read_text(encoding="utf-8").splitlines()


def installed_dependencies_match_lock():
    from importlib.metadata import PackageNotFoundError, version
    from packaging.requirements import Requirement
    for line in (ROOT / "backend/requirements.lock").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            if not requirement.specifier.contains(version(requirement.name)):
                return False
        except PackageNotFoundError:
            return False
    return True


def run_command(command, *, cwd, env, timeout=600):
    try:
        return subprocess.run(command, cwd=cwd, env=env, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        print("Check exceeded its time budget.", flush=True)
        return 124
    except OSError as exc:
        # Avoid including command arguments or private environment values.
        print("Unable to start check: " + type(exc).__name__, flush=True)
        return 127


def check_pytest(scope, run_dir, env, *, ci=False):
    if not backend_lock_matches_source():
        return {"status": "failed", "reason": "requirements.txt changed; regenerate backend/requirements.lock"}
    if not installed_dependencies_match_lock():
        return {"status": "failed", "reason": "Install backend/requirements.lock with the Python interpreter running this check"}
    if scope == "mysql":
        raw_url = os.environ.get("MONIPAN_STAGE1_MYSQL_URL", "")
        if not raw_url:
            return {"status": "failed", "reason": "MySQL was requested but MONIPAN_STAGE1_MYSQL_URL is not set"}
        # Validate before starting pytest. The test additionally refuses nonempty DBs.
        from sqlalchemy.engine import make_url
        import re
        try:
            url = make_url(raw_url)
            safe = (url.drivername == "mysql+pymysql" and url.host == "127.0.0.1"
                    and re.fullmatch(r"monipan_stage1_test_[a-z0-9_]+", url.database or ""))
        except Exception:
            safe = False
        if not safe:
            return {"status": "failed", "reason": "Refusing MySQL outside the disposable local Stage 1 namespace"}
        env = dict(env, MONIPAN_STAGE1_MYSQL_URL=raw_url)
        targets = ["backend/tests/test_stage1_mysql.py"]
    else:
        # Keep the offline regression independent of any configured MySQL URL.
        targets = ["backend/tests", "--ignore=backend/tests/test_stage1_mysql.py"]
    xml = run_dir / (scope + ".xml")
    command = [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short", "-rs",
               "-p", "no:cacheprovider", "--basetemp=" + str(run_dir / (scope + "-tmp")),
               "--junitxml=" + str(xml)]
    code = run_command(command, cwd=ROOT, env=env)
    try:
        result = junit_result(xml, strict_skips=ci, require_all=scope == "mysql")
    except (OSError, ET.ParseError):
        result = {"ok": False, "reason": "Missing or invalid pytest report"}
    valid_report = result.pop("ok")
    result["status"] = "passed" if code == 0 and valid_report else "failed"
    result["exit_code"] = code
    return result


def check_frontend(env):
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    if not pnpm:
        return {"status": "failed", "reason": "pnpm is not available"}
    frontend = ROOT / "frontend"
    results = {}
    # Reuse package.json so local commands and CI cannot silently diverge.
    # Both batch-file arguments on Windows are fixed strings, never user input.
    for name, script in (("tests", "test"), ("build_and_typecheck", "build")):
        print("Frontend: " + name, flush=True)
        code = run_command([pnpm, script], cwd=frontend, env=env)
        results[name] = "passed" if code == 0 else "failed"
        if code:
            return {"status": "failed", "checks": results, "exit_code": code}
    return {"status": "passed", "checks": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("all", *SCOPES), default="all")
    parser.add_argument("--with-mysql", action="store_true", help="Include explicit isolated MySQL acceptance")
    parser.add_argument("--ci", action="store_true", help="Reject unexpected pytest skips")
    args = parser.parse_args(argv)
    if args.with_mysql and args.scope != "all":
        parser.error("--with-mysql is only used with --scope all")
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = ROOT / ".docker/checks" / stamp
    run_dir.mkdir(parents=True)
    env = test_environment(run_dir)
    selected = ["backend", "frontend"] if args.scope == "all" else [args.scope]
    if args.with_mysql:
        selected.append("mysql")
    report = {"schema_version": 1, "started_at": datetime.now(UTC).isoformat(),
              "checks": {scope: {"status": "not_run"} for scope in SCOPES}}
    for scope in selected:
        print("\nRunning " + scope + " checks", flush=True)
        try:
            report["checks"][scope] = (check_frontend(env) if scope == "frontend"
                                       else check_pytest(scope, run_dir, env, ci=args.ci))
        except Exception as exc:
            report["checks"][scope] = {"status": "failed", "reason": type(exc).__name__}
    success = all(report["checks"][scope]["status"] == "passed" for scope in selected)
    report["status"] = "passed" if success else "failed"
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    path = run_dir / "summary.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print("Report: " + str(path), flush=True)
    if "mysql" not in selected:
        print("MySQL acceptance was NOT RUN; this result does not approve a production release.", flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
