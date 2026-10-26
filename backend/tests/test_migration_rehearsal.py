"""Exercise the entire candidate probe locally; server rehearsal uses real MySQL."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_probe_adopts_without_changing_business_data_and_reads_accounts(tmp_path):
    # Reuse the existing meaningful trading/AI fixture, before its baseline stamp.
    fixture_tree = ast.parse((ROOT / "backend/tests/test_restore_application.py").read_text(encoding="utf-8"))
    fixture = next(ast.literal_eval(item.value) for item in fixture_tree.body
                   if isinstance(item, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "FIXTURE"
                                                         for name in item.targets))
    fixture = fixture.replace("adopt_baseline(engine)\n", "")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MONIPAN_")}
    environment.update(MONIPAN_DATABASE_URL="sqlite:///" + (tmp_path / "clone.db").as_posix(),
                       MONIPAN_ENVIRONMENT="test", MONIPAN_COOKIE_SECURE="1", MONIPAN_DISABLE_MARKET_LOOP="1")
    prepare = subprocess.run([sys.executable, "-c", fixture], cwd=ROOT / "backend", env=environment,
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert prepare.returncode == 0, prepare.stderr
    source = ("import json,sys;sys.path.insert(0," + repr(str(ROOT / "scripts")) + ");"
              "from migration_baseline_probe import run_migration_probe;"
              "print('RESULT='+json.dumps(run_migration_probe(allow_sqlite=True)))")
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT / "backend", env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith("RESULT=")))
    assert report["status"] == "passed"
    assert report["dialect"] == "sqlite"
    assert report["business_tables_fingerprinted"] == 31
    assert report["business_rows_unchanged_by_adoption"] is True
    assert report["check_constraints_verified"] == 14
    assert report["fresh_database_upgrade"] == "passed"
    assert report["application"]["restored_accounts_read"] == 2
    assert report["application"]["original_rows_unchanged"] is True
    assert report["production_schema_modified"] is False
