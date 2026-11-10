#!/usr/bin/env python3
"""Run one SQL recipe on the released Parquet tables and print its rows.

With --expect, compare the rows with a saved JSON result and exit non-zero
when they differ. With --write, save the rows as that JSON result.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import duckdb

TABLES = ("users", "repositories", "portfolios")


def connect(release_dir: Path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect()
    for table in TABLES:
        path = release_dir / f"{table}.parquet"
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        db.read_parquet(str(path)).create_view(f"release_{table}")
    return db


def rows(db: duckdb.DuckDBPyConnection, recipe: Path) -> list[dict]:
    cursor = db.execute(recipe.read_text())
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def same(actual, expected) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(map(same, actual, expected))
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same(actual[k], expected[k]) for k in expected)
    return actual == expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--release-dir", type=Path, default=Path("data"))
    parser.add_argument("--expect", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()

    result = json.loads(json.dumps(rows(connect(args.release_dir), args.recipe), default=str))
    print(json.dumps(result, indent=2))
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(result, indent=2) + "\n")
    if args.expect:
        if not same(result, json.loads(args.expect.read_text())):
            sys.exit(f"FAIL: result differs from {args.expect}")
        print(f"PASS: matches {args.expect}")


if __name__ == "__main__":
    main()
