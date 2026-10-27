"""Exercise the entire candidate probe locally; server rehearsal uses real MySQL."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_stage0_probe_refuses_new_schema_before_adoption(tmp_path):
    # Reuse the existing meaningful trading/AI fixture, before its baseline stamp.
    fixture_tree = ast.parse((ROOT / "backend/tests/test_restore_application.py").read_text(encoding="utf-8"))
    fixture = next(ast.literal_eval(item.value) for item in fixture_tree.body
                   if isinstance(item, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "FIXTURE"
                                                         for name in item.targets))
    fixture = fixture.replace("upgrade_database(engine)\n", "Base.metadata.create_all(engine)\n")
    fixture += '\nwith engine.begin() as c:\n    c.exec_driver_sql("DROP TABLE security_catalog_entries")\n    c.exec_driver_sql("ALTER TABLE stocks DROP COLUMN research_only")\n'
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
    assert result.returncode != 0
    assert "frozen baseline only" in result.stderr
    from sqlalchemy import create_engine, inspect
    database = create_engine(environment["MONIPAN_DATABASE_URL"])
    assert "alembic_version" not in inspect(database).get_table_names()
    assert len(inspect(database).get_table_names()) == 31
    database.dispose()
