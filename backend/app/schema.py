"""Checked schema commands: status, check, adopt-baseline and upgrade.

Adoption writes only Alembic metadata after validating the existing schema.
Application startup never adopts an unversioned production database.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, inspect, text

from .migrations.check_expressions import expression_tree
from .migrations.frozen_v04 import metadata as baseline_metadata
from .schema_audit import audit, model_schema


BASELINE = "20261018_01"


class SchemaStateError(RuntimeError):
    pass


def configuration(connection=None, operation=None):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    config.attributes.update(connection=connection, managed_operation=operation)
    return config


def head_revision():
    heads = ScriptDirectory.from_config(configuration()).get_heads()
    if len(heads) != 1:
        raise SchemaStateError("The release must contain exactly one migration head")
    return heads[0]


def current_revisions(connection):
    return MigrationContext.configure(connection).get_current_heads()


def require_current(connection):
    if current_revisions(connection) != (head_revision(),):
        raise SchemaStateError("Database schema is not at the release head; run the checked migration commands")


@contextmanager
def migration_connection(engine):
    with engine.connect() as connection:
        mysql = connection.dialect.name == "mysql"
        if mysql:
            acquired = connection.execute(text("SELECT GET_LOCK('monipan_schema_migration', 10)")).scalar_one()
            connection.commit()
            if acquired != 1:
                raise SchemaStateError("Another schema operation holds the migration lock")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if mysql:
                connection.execute(text("SELECT RELEASE_LOCK('monipan_schema_migration')"))
                connection.commit()


def baseline_check(connection):
    frozen = baseline_metadata()
    report = audit(connection, model_schema(frozen, connection.dialect))
    problems = list(report["differences"])
    problems.extend(report["warnings"])
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    accepted_defaults = []
    checks_count = 0
    for name in sorted(frozen.tables):
        if name not in existing_tables:
            continue
        expected = {item.name: str(item.sqltext) for item in frozen.tables[name].constraints
                    if isinstance(item, CheckConstraint)}
        actual = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(name)}
        checks_count += len(actual)
        if set(expected) != set(actual):
            problems.append({"table": name, "kind": "check_constraint_names"})
        for key in set(expected) & set(actual):
            try:
                equal = expression_tree(expected[key]) == expression_tree(actual[key])
            except ValueError:
                equal = False
            if not equal:
                problems.append({"table": name, "kind": "check_expression", "constraint": key})
        for column in inspector.get_columns(name):
            if column.get("computed") or column.get("identity"):
                problems.append({"table": name, "column": column["name"], "kind": "generated_column"})
            default = column.get("default")
            if default is None or str(default).upper() == "NULL":
                continue
            if (name, column["name"]) == ("users", "status") and str(default) in {"'ACTIVE'", "_utf8mb4'ACTIVE'"}:
                accepted_defaults.append("users.status=ACTIVE")
            else:
                problems.append({"table": name, "column": column["name"], "kind": "unexpected_server_default"})
    views = inspector.get_view_names()
    if views:
        problems.append({"kind": "unmanaged_views", "count": len(views)})
    if connection.dialect.name == "mysql":
        unenforced = connection.execute(text(
            "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK' AND ENFORCED<>'YES'"
        )).all()
        if unenforced:
            problems.append({"kind": "unenforced_checks", "count": len(unenforced)})
        for table in frozen.tables:
            if table not in existing_tables:
                continue
            options = inspector.get_table_options(table)
            if options.get("mysql_collate") != "utf8mb4_0900_ai_ci":
                problems.append({"table": table, "kind": "table_collation"})
        collations = connection.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME<>'alembic_version' "
            "AND CHARACTER_SET_NAME IS NOT NULL AND DATA_TYPE<>'json' "
            "AND (CHARACTER_SET_NAME<>'utf8mb4' OR COLLATION_NAME<>'utf8mb4_0900_ai_ci')"
        )).all()
        if collations:
            problems.append({"kind": "column_collations", "count": len(collations)})
        triggers = connection.execute(text(
            "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
        )).scalar_one()
    else:
        triggers = connection.execute(text("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'")).scalar_one()
    if triggers:
        problems.append({"kind": "unmanaged_triggers", "count": triggers})
    # Alembic additionally validates its dialect-specific index/type matching.
    if not problems:
        differences = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), frozen)
        if differences:
            problems.append({"kind": "alembic_comparison", "count": len(differences),
                             "operations": [str(item[0]) if isinstance(item, tuple) else "column_changes"
                                            for item in differences]})
    report.update(status="baseline_compatible" if not problems else "baseline_review_required",
                  baseline=BASELINE, adoption_problems=problems, check_constraints_inspected=checks_count,
                  accepted_legacy_defaults=accepted_defaults, ready_to_stamp=not problems,
                  not_checked=["column_comments", "index_visibility", "historical_data_backfill_completeness"])
    return report


def adopt_baseline(engine):
    with migration_connection(engine) as connection:
        revisions = current_revisions(connection)
        if revisions and revisions != (BASELINE,):
            raise SchemaStateError("Existing migration version is not this baseline")
        report = baseline_check(connection)
        if not report["ready_to_stamp"]:
            raise SchemaStateError("Baseline check failed; no migration version was written")
        if not revisions:
            command.stamp(configuration(connection, "adopt"), BASELINE)
        if current_revisions(connection) != (BASELINE,):
            raise SchemaStateError("Baseline version write was not confirmed")
        report.update(status="baseline_already_recorded" if revisions else "baseline_adopted",
                      alembic_version_table=True, schema_versions=[BASELINE], schema_modified=not bool(revisions),
                      business_schema_modified=False, version_metadata_written=not bool(revisions),
                      baseline_preflight_passed=True, ready_to_stamp=False)
    return report


def upgrade_database(engine):
    with migration_connection(engine) as connection:
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        if not current_revisions(connection) and tables:
            raise SchemaStateError("Existing database is unversioned; back up and use checked adopt-baseline first")
        command.upgrade(configuration(connection, "upgrade"), "head")
        require_current(connection)


def prepare_database(engine, *, production):
    with engine.connect() as connection:
        empty = not inspect(connection).get_table_names()
    if empty and not production:
        upgrade_database(engine)
    with engine.connect() as connection:
        require_current(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "check", "adopt-baseline", "upgrade"])
    action = parser.parse_args().action
    from .database import engine
    if action == "upgrade":
        upgrade_database(engine)
    elif action == "adopt-baseline":
        report = adopt_baseline(engine)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        with engine.connect() as connection:
            if action == "check":
                report = baseline_check(connection)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                if not report["ready_to_stamp"]:
                    raise SystemExit(1)
            else:
                print(json.dumps({"current": current_revisions(connection), "head": head_revision()}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("SCHEMA_COMMAND_FAILED " + type(exc).__name__)
        if isinstance(exc, SchemaStateError):
            print(str(exc))
        raise SystemExit(1)
