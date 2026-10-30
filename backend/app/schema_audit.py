"""Read-only schema inventory for planning the initial migration baseline.

This is a preliminary comparison, not authorization to stamp a version. Defaults,
collations, generated expressions and CHECK SQL equivalence need separate review.
"""

import re

from sqlalchemy import UniqueConstraint, inspect, text


def type_name(value, dialect):
    name = str(value.compile(dialect=dialect)).upper()
    name = re.sub(r"\bINTEGER\b", "INT", name)
    name = re.sub(r"\bNUMERIC\b", "DECIMAL", name)
    if dialect.name == "mysql":
        if name in {"BOOL", "BOOLEAN", "TINYINT(1)"}:
            return "BOOLEAN"
    return name


def foreign_key(columns, table, referred, options):
    return {
        "columns": list(columns), "table": table, "referred": list(referred),
        "ondelete": (options.get("ondelete") or "RESTRICT").upper(),
        "onupdate": (options.get("onupdate") or "RESTRICT").upper(),
    }


def sorted_keys(values):
    return sorted(values, key=lambda item: repr(sorted(item.items())))


def model_schema(metadata, dialect):
    tables = {}
    for table in metadata.sorted_tables:
        indexes = {index.name: {"columns": [column.name for column in index.columns],
                               "unique": bool(index.unique)} for index in table.indexes}
        unique = {tuple(column.name for column in item.columns) for item in table.constraints
                  if isinstance(item, UniqueConstraint)}
        unique.update(tuple(item["columns"]) for item in indexes.values() if item["unique"])
        tables[table.name] = {
            "columns": {column.name: {"type": type_name(column.type, dialect),
                                      "nullable": column.nullable} for column in table.columns},
            "primary_key": [column.name for column in table.primary_key.columns],
            "unique": sorted([list(columns) for columns in unique]), "indexes": indexes,
            "constraint_indexes": {item.name: [column.name for column in item.columns]
                                   for item in table.constraints
                                   if isinstance(item, UniqueConstraint) and item.name},
            "foreign_keys": sorted_keys([
                foreign_key([item.parent.name for item in constraint.elements],
                            constraint.referred_table.name,
                            [item.column.name for item in constraint.elements],
                            {"ondelete": constraint.ondelete, "onupdate": constraint.onupdate})
                for constraint in table.foreign_key_constraints]),
        }
    return tables


def reject_writes(_connection, _cursor, statement, _parameters, _context, _many):
    normalized = statement.strip().upper()
    if normalized.startswith(("SELECT ", "SHOW ", "DESCRIBE ")):
        return
    if normalized == "SET SESSION TRANSACTION READ ONLY":
        return
    if (normalized.startswith("PRAGMA ") and "=" not in normalized
            and re.match(r"PRAGMA (?:MAIN\.|TEMP\.)?(?:TABLE_X?INFO|INDEX_LIST|INDEX_X?INFO|FOREIGN_KEY_LIST)\(",
                         normalized)):
        return  # Offline SQLite fixtures only; production requires MySQL.
    raise RuntimeError("Schema audit rejected a statement outside its read-only allowlist")


def audit(connection, expected):
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    differences = []
    warnings = []
    missing = sorted(set(expected) - names)
    extra = sorted(names - set(expected) - {"alembic_version"})
    if missing or extra:
        differences.append({"kind": "tables", "missing": missing, "extra": extra})
    column_count = 0
    foreign_key_count = 0
    supporting_indexes = 0
    for name in sorted(set(expected) & names):
        model = expected[name]
        columns = inspector.get_columns(name)
        column_count += len(columns)
        actual_columns = {column["name"]: {"type": type_name(column["type"], connection.dialect),
                                           "nullable": bool(column["nullable"])} for column in columns}
        if actual_columns != model["columns"]:
            differences.append({"table": name, "kind": "columns",
                                "missing": sorted(set(model["columns"]) - set(actual_columns)),
                                "extra": sorted(set(actual_columns) - set(model["columns"])),
                                "changed": [
                                    {"column": column, "model": model["columns"][column],
                                     "actual": actual_columns[column]}
                                    for column in sorted(set(model["columns"]) & set(actual_columns))
                                    if model["columns"][column] != actual_columns[column]]})
        primary = inspector.get_pk_constraint(name).get("constrained_columns", [])
        if primary != model["primary_key"]:
            differences.append({"table": name, "kind": "primary_key", "actual": primary})
        indexes = {index["name"]: {"columns": index["column_names"], "unique": bool(index["unique"])}
                   for index in inspector.get_indexes(name)}
        changed = [index for index in model["indexes"] if indexes.get(index) != model["indexes"][index]]
        if changed:
            differences.append({"table": name, "kind": "missing_or_changed_indexes", "indexes": sorted(changed)})
        extra_indexes = sorted(set(indexes) - set(model["indexes"]))
        unknown_indexes = []
        for index_name in extra_indexes:
            index = indexes[index_name]
            columns = index["columns"]
            named_unique = (index["unique"] and
                            model.get("constraint_indexes", {}).get(index_name) == columns)
            unnamed_unique = (index["unique"] and len(columns) == 1 and index_name == columns[0]
                              and columns in model["unique"])
            foreign_support = (not index["unique"] and columns and index_name == columns[0]
                               and any(key["columns"] == columns for key in model["foreign_keys"]))
            if named_unique or unnamed_unique or foreign_support:
                supporting_indexes += 1
            else:
                unknown_indexes.append(index_name)
        if unknown_indexes:
            warnings.append({"table": name, "kind": "additional_indexes", "indexes": unknown_indexes})
        unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        unique.update(tuple(item["columns"]) for item in indexes.values() if item["unique"])
        if sorted([list(columns) for columns in unique]) != model["unique"]:
            differences.append({"table": name, "kind": "unique_keys", "actual": sorted([list(c) for c in unique])})
        keys = inspector.get_foreign_keys(name)
        foreign_key_count += len(keys)
        actual_keys = sorted_keys([foreign_key(item["constrained_columns"], item["referred_table"],
                                              item["referred_columns"], item.get("options", {})) for item in keys])
        if actual_keys != model["foreign_keys"]:
            differences.append({"table": name, "kind": "foreign_keys", "actual": actual_keys})
        if connection.dialect.name == "mysql":
            options = inspector.get_table_options(name)
            engine = options.get("mysql_engine", "unknown")
            if engine.upper() != "INNODB":
                warnings.append({"table": name, "kind": "non_innodb_engine", "engine": engine})
    versions = []
    if "alembic_version" in names:
        for value in connection.execute(text("SELECT version_num FROM alembic_version LIMIT 10")).scalars():
            versions.append(value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                            else "unrecognized_version_value")
    return {"status": "differences_found" if differences else "matches_model_precheck",
            "dialect": connection.dialect.name, "model_tables": len(expected),
            "actual_business_tables": len(names - {"alembic_version"}), "columns_inspected": column_count,
            "foreign_keys_inspected": foreign_key_count, "alembic_version_table": "alembic_version" in names,
            "supporting_indexes_classified": supporting_indexes,
            "schema_versions": versions, "differences": differences, "warnings": warnings,
            "business_rows_read": False, "schema_modified": False, "ready_to_stamp": False,
            "not_checked": ["server_defaults", "collations", "check_expressions", "triggers", "views", "data_backfills"]}
