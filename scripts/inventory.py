"""Inventory a frozen SQLite input and compare it with the documented thesis DDL."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3

from release_common import Context, ROOT, parser, quote, sha256


def indexes_for(conn, table):
    indexes = []
    for entry in conn.execute(f"PRAGMA index_list({quote(table)})"):
        index = dict(entry)
        name = index["name"]
        index["columns"] = [dict(row) for row in conn.execute(f"PRAGMA index_xinfo({quote(name)})")]
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        index["ddl"] = ddl[0] if ddl else None
        indexes.append(index)
    return sorted(indexes, key=lambda item: item["name"])


def describe_table(conn, name, ddl):
    return {"columns": [dict(row) for row in conn.execute(f"PRAGMA table_info({quote(name)})")],
            "ddl": ddl,
            "foreign_keys": [dict(row) for row in conn.execute(f"PRAGMA foreign_key_list({quote(name)})")],
            "indexes": indexes_for(conn, name)}


def normalized_ddl(sql):
    # This compares text only. Column/key comparisons below establish the
    # structural differences independently of SQL spelling and formatting.
    return re.sub(r"\s+", " ", (sql or "").strip()).casefold()


def column_definition(column):
    return {"type": column["type"].upper(), "notnull": bool(column["notnull"]),
            "default": column["dflt_value"], "pk": column["pk"]}


def unique_keys(table):
    keys = []
    for index in table["indexes"]:
        if index["unique"]:
            keys.append({"columns": [column["name"] for column in index["columns"] if column["key"]],
                         "partial": bool(index["partial"])})
    return sorted(keys, key=lambda item: json.dumps(item, sort_keys=True))


def foreign_keys(table):
    # SQLite numbers constraints by declaration order. Preserve composite-key
    # member sequence, but ignore the constraint's arbitrary numeric id.
    return sorted([{key: value for key, value in row.items() if key != "id"}
                   for row in table["foreign_keys"]], key=lambda row: json.dumps(row, sort_keys=True))


def index_definition(index):
    # cid is the table column's physical position, and seqno is metadata's
    # ordinal. Neither changes an index's meaning when columns move in DDL.
    # Keep list order and the named column, collation, direction and key role.
    columns = [{key: column[key] for key in ("name", "coll", "desc", "key")}
               for column in index["columns"]]
    return {"unique": bool(index["unique"]), "origin": index["origin"],
            "partial": bool(index["partial"]), "columns": columns}


def compare_schema(actual, reference_path):
    # Only the public reference DDL is executed, in a separate in-memory DB.
    # The supplied input connection remains immutable and query-only.
    with closing(sqlite3.connect(":memory:")) as reference:
        reference.row_factory = sqlite3.Row
        reference.executescript(reference_path.read_text())
        expected = {row["name"]: describe_table(reference, row["name"], row["sql"])
                    for row in reference.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")}
    tables = {}
    for name in sorted(set(actual) & set(expected)):
        observed, declared = actual[name], expected[name]
        observed_columns = {column["name"]: column for column in observed["columns"]}
        declared_columns = {column["name"]: column for column in declared["columns"]}
        changed = {}
        for column in sorted(set(observed_columns) & set(declared_columns)):
            found, wanted = column_definition(observed_columns[column]), column_definition(declared_columns[column])
            if found != wanted:
                changed[column] = {"documented": wanted, "actual": found}
        observed_indexes = {index["name"]: index for index in observed["indexes"]}
        declared_indexes = {index["name"]: index for index in declared["indexes"]}
        changed_indexes = {}
        for index in sorted(set(observed_indexes) & set(declared_indexes)):
            found, wanted = index_definition(observed_indexes[index]), index_definition(declared_indexes[index])
            if found != wanted:
                changed_indexes[index] = {"documented": wanted, "actual": found}
        tables[name] = {
            "missing_columns": sorted(set(declared_columns) - set(observed_columns)),
            "extra_columns": sorted(set(observed_columns) - set(declared_columns)),
            "changed_column_definitions": changed,
            "column_order_matches": list(observed_columns) == list(declared_columns),
            "foreign_keys_match": foreign_keys(observed) == foreign_keys(declared),
            "unique_keys_match": unique_keys(observed) == unique_keys(declared),
            "missing_indexes": sorted(set(declared_indexes) - set(observed_indexes)),
            "extra_indexes": sorted(set(observed_indexes) - set(declared_indexes)),
            "changed_index_definitions": changed_indexes,
            "normalized_ddl_text_matches": normalized_ddl(observed["ddl"]) == normalized_ddl(declared["ddl"]),
        }
    return {
        "reference": str(reference_path.relative_to(ROOT)) if reference_path.is_relative_to(ROOT) else str(reference_path),
        "reference_sha256": sha256(reference_path),
        "reference_kind": "Documented thesis DDL; not collector execution evidence",
        "missing_tables": sorted(set(expected) - set(actual)),
        "extra_tables": sorted(set(actual) - set(expected)),
        "tables": tables,
        "ddl_text_note": "DDL text differences can reflect formatting or constraint spelling; inspect the saved SQL and structural comparisons",
    }


def main():
    cli = parser(__doc__)
    cli.add_argument("--reference-schema", type=Path, default=ROOT / "tests/fixtures/schema.sql")
    args = cli.parse_args()
    ctx = Context(args, check_schema=False)
    reference_path = args.reference_schema.resolve(strict=True)
    with closing(ctx.connect()) as conn:
        tables = {}
        objects = [dict(row) for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        for row in objects:
            if row["type"] != "table":
                continue
            name = row["name"]
            table = describe_table(conn, name, row["sql"])
            table["rows"] = conn.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]
            tables[name] = table
        sequences = []
        if "sqlite_sequence" in tables:
            for name, highwater in conn.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name"):
                rows = tables.get(name, {}).get("rows")
                sequences.append({"table": name, "sequence_highwater": highwater,
                                  "observed_rows": rows,
                                  "highwater_minus_rows": highwater - rows if isinstance(highwater, int) and rows is not None else None})
        result = {
            "tables": tables, "schema_objects": objects, "sqlite_version": sqlite3.sqlite_version,
            "schema_comparison": compare_schema(tables, reference_path),
            "autoincrement_sequences": sequences,
            "sequence_interpretation": "Sequence highwater minus row count is descriptive, not a deleted-row count. Gaps can result from failed inserts, explicit IDs, replacement or deletion, and do not establish which occurred",
            "connection_settings": {"query_only": conn.execute("PRAGMA query_only").fetchone()[0],
                                    "temp_store": conn.execute("PRAGMA temp_store").fetchone()[0],
                                    "cache_size": conn.execute("PRAGMA cache_size").fetchone()[0]},
            "audit_only": not ctx.provenance["manuscript_results_allowed"],
            "input_role": ctx.provenance["input_role"],
        }
        ctx.write_json("inventory.json", result)


if __name__ == "__main__":
    main()
