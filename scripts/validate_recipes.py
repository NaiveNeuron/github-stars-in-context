#!/usr/bin/env python3
"""Validate full-export DuckDB recipes against the accepted source aggregates.

Only the three Parquet inputs, export evidence and source aggregate JSON are
read. The source SQLite DB and private collector are never opened. Integer
results must agree exactly. Floating tolerances are explicit, including the
separate bound for different macro-mean reduction orders.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import sys
import time

import duckdb
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "examples/recipes"
SOURCE_NAMES = ("r1-thresholds", "r2-portfolios", "r3-owner-clustering")
QUERY_NAMES = ("01_thresholds.sql", "02_portfolios.sql", "02b_rank_overlap.sql", "03_owner_clustering.sql")
RELATIVE_TOLERANCE = 1e-12
ABSOLUTE_TOLERANCE = 1e-14
MACRO_MEAN_ABSOLUTE_TOLERANCE = 1e-10


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def display_path(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def compare(checks, label, actual, expected, macro=False):
    if expected is None:
        passed, kind, error = actual is None, "null", None
    elif isinstance(expected, int):
        passed, kind, error = actual == expected, "integer_exact", abs(actual - expected)
    else:
        error = abs(actual - expected)
        if macro:
            kind = "macro_mean_absolute_1e-10"
            passed = error <= MACRO_MEAN_ABSOLUTE_TOLERANCE
        else:
            kind = "float_relative_1e-12_absolute_1e-14"
            passed = math.isclose(actual, expected, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)
    checks.append({"field": label, "comparison": kind, "absolute_error": error, "passed": passed})
    if not passed:
        raise ValueError(f"Recipe mismatch at {label}: actual={actual!r}, expected={expected!r}")


def compare_results(outputs, accepted):
    checks = []
    for filename, expected_rows in zip(QUERY_NAMES, (4, 5, 3, 4)):
        compare(checks, filename + "/output_rows", len(outputs[filename]), expected_rows)
    p1, p2, p3 = (accepted[name] for name in SOURCE_NAMES)
    for row, expected in zip(outputs[QUERY_NAMES[0]], p1["thresholds"]):
        threshold = expected["threshold"]
        conditional = expected["conditional_on_retained_owner"]
        baseline = p1["baseline"]
        values = {
            "threshold": threshold,
            "baseline_accounts": baseline["accounts"],
            "baseline_rows": baseline["account_linked_repository_rows"],
            "baseline_stars": baseline["stars"],
            "retained_accounts": expected["retained"]["accounts"]["numerator"],
            "retained_rows": expected["retained"]["account_linked_repository_rows"]["numerator"],
            "retained_stars": expected["retained"]["stars"]["numerator"],
            "retained_owner_baseline_rows": conditional["baseline_account_linked_repository_rows"],
            "retained_owner_baseline_stars": conditional["baseline_stars"],
            "hidden_rows": conditional["hidden_account_linked_repository_rows"]["numerator"],
            "hidden_stars": conditional["hidden_stars"]["numerator"],
            "retained_account_fraction": expected["retained"]["accounts"]["share"],
            "retained_row_fraction": expected["retained"]["account_linked_repository_rows"]["share"],
            "retained_star_fraction": expected["retained"]["stars"]["share"],
            "micro_hidden_row_fraction": conditional["hidden_account_linked_repository_rows"]["share"],
            "micro_hidden_star_fraction": conditional["hidden_stars"]["share"],
            "macro_mean_hidden_row_fraction": conditional["macro"]["mean_hidden_repository_fraction"],
            "macro_mean_hidden_star_fraction": conditional["macro"]["mean_hidden_star_fraction"],
        }
        for key, value in values.items():
            compare(checks, f"R1/t={threshold}/{key}", row[key], value, key.startswith("macro_"))
    for row, expected in zip(outputs[QUERY_NAMES[1]], p2["bands"]):
        label = expected["label"]
        values = {
            "stars_lower_inclusive": expected["stars_lower_inclusive"],
            "stars_upper_exclusive": expected["stars_upper_exclusive"],
            "multi_repository_accounts": expected["n"],
            "singleton_accounts": expected["singleton_n"],
            "dominant_ge_0_9_accounts": expected["dominant_share_ge_0_9"]["n"],
            "dominant_ge_0_9_fraction": expected["dominant_share_ge_0_9"]["share"],
        }
        for key, value in values.items():
            compare(checks, f"R2/{label}/{key}", row[key], value)
        for column, key in (("dominant_share_quantiles", "dominant_share"),
                            ("effective_breadth_quantiles", "effective_breadth")):
            for actual, quantile in zip(row[column], ("min", "p05", "q1", "median", "q3", "p95", "max")):
                compare(checks, f"R2/{label}/{column}/{quantile}", actual, expected[key][quantile])
    for row, expected in zip(outputs[QUERY_NAMES[2]], p2["h_vs_total_stars_top_k"]["comparisons"]):
        k = expected["requested_k"]
        exact, inclusive = expected["exact_k"], expected["tie_inclusive"]
        values = {
            "requested_k": k, "effective_k": expected["effective_k"],
            "exact_overlap_accounts": exact["overlap_n"],
            "exact_overlap_fraction": exact["overlap_share_of_k"],
            "h_boundary_tie_accounts": exact["h_index"]["boundary_tie_n"],
            "star_boundary_tie_accounts": exact["total_stars"]["boundary_tie_n"],
            "h_tie_inclusive_accounts": inclusive["h_index"]["selected_n"],
            "star_tie_inclusive_accounts": inclusive["total_stars"]["selected_n"],
            "tie_inclusive_overlap_accounts": inclusive["overlap_n"],
            "tie_inclusive_h_overlap_fraction": inclusive["overlap_share_of_h_set"],
            "tie_inclusive_star_overlap_fraction": inclusive["overlap_share_of_star_set"],
        }
        for key, value in values.items():
            compare(checks, f"R2/k={k}/{key}", row[key], value)
    for row, expected in zip(outputs[QUERY_NAMES[3]], p3["thresholds"]):
        threshold = expected["threshold"]
        values = {
            "threshold": threshold, "unique_owners": expected["unique_owners"],
            "selected_rows": expected["selected_account_linked_repository_rows"],
            "rows_from_multiple_row_owners": expected["selected_rows_from_owners_with_multiple_selected_rows"]["numerator"],
            "owners_with_multiple_selected_rows": expected["owners_with_multiple_selected_rows"]["numerator"],
            "clustered_row_fraction": expected["selected_rows_from_owners_with_multiple_selected_rows"]["share"],
            "mean_rows_per_retained_owner": expected["selected_repository_rows_per_owner"]["mean"],
        }
        for key, value in values.items():
            compare(checks, f"R3/t={threshold}/{key}", row[key], value)
        for actual, quantile in zip(row["rows_per_owner_quantiles"], ("min", "q1", "median", "q3", "p90", "p95", "p99", "max")):
            compare(checks, f"R3/t={threshold}/quantile/{quantile}", actual,
                    expected["selected_repository_rows_per_owner"]["quantiles"][quantile])
    return checks


def validate(release_dir, source_dir, export_evidence, out_dir):
    started = time.monotonic()
    release_dir, source_dir = Path(release_dir).resolve(), Path(source_dir).resolve()
    export_evidence, out_dir = Path(export_evidence).resolve(), Path(out_dir).resolve()
    export = json.loads(export_evidence.read_text())
    provenance = export["provenance"]
    source_sha = provenance.get("source_sha256", provenance["input_sha256"])
    kind = provenance["data_kind"]
    if kind not in ("research", "synthetic"):
        raise ValueError("Export evidence needs a declared research/synthetic data kind")
    if export["results"]["sampling"]["account_fraction"] != 1:
        raise ValueError("Source aggregate comparison requires the full account export, not a pilot")
    expected_files = {record["path"]: record for record in export["results"]["files"]}
    inputs = []
    for filename in ("users.parquet", "repositories.parquet", "portfolios.parquet"):
        path = release_dir / filename
        actual = digest(path)
        metadata = json.loads(pq.ParquetFile(path).schema_arrow.metadata[b"release_provenance"])
        parquet_source = metadata.get("source_sha256", metadata["input_sha256"])
        if actual != expected_files[filename]["sha256"] or parquet_source != source_sha:
            raise ValueError(f"Parquet input or source-lineage checksum mismatch: {filename}")
        if metadata.get("data_kind") != kind:
            raise ValueError(f"Parquet/export data-kind mismatch: {filename}")
        inputs.append({"path": display_path(path), "bytes": path.stat().st_size,
                       "sha256": actual, "matches_export_manifest": True, "source_lineage_matches": True})
    accepted, oracle_files = {}, []
    for name in SOURCE_NAMES:
        path = source_dir / (name + ".json")
        document = json.loads(path.read_text())
        source_provenance = document["provenance"]
        oracle_source = source_provenance.get("source_sha256", source_provenance["input_sha256"])
        if oracle_source != source_sha or source_provenance.get("data_kind") != kind:
            raise ValueError(f"Source oracle lineage/data-kind mismatch: {path.name}")
        accepted[name] = document["results"]
        oracle_files.append({"path": display_path(path), "sha256": digest(path)})
    recipe_hashes = {path.name: digest(path) for path in sorted(RECIPES.glob("*.sql"))}
    outputs, timings = {}, []
    with duckdb.connect(":memory:", config={"memory_limit": "4GB", "threads": "4", "temp_directory": ""}) as db:
        for table in ("users", "repositories", "portfolios"):
            db.read_parquet(str(release_dir / (table + ".parquet"))).create_view("release_" + table)
        for filename in QUERY_NAMES:
            before = time.monotonic()
            cursor = db.execute((RECIPES / filename).read_text())
            columns = [description[0] for description in cursor.description]
            outputs[filename] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            seconds = time.monotonic() - before
            timings.append({"recipe": filename, "seconds": seconds, "output_rows": len(outputs[filename])})
            print(f"{filename}: {seconds:.3f} seconds", flush=True)
    checks = compare_results(outputs, accepted)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    script = Path(__file__).resolve()
    executable = Path(sys.executable).absolute()
    try:
        executable_name = str(executable.relative_to(ROOT))
    except ValueError:
        executable_name = str(executable)
    command = [executable_name, display_path(script), "--release-dir", display_path(release_dir),
               "--source-dir", display_path(source_dir), "--export-evidence", display_path(export_evidence),
               "--out-dir", display_path(out_dir)]
    report = {
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(), "data_kind": kind,
            "source_sha256": source_sha, "input_role": "full_minimized_parquet_export",
            "actual_inputs": inputs, "input_sha256_by_file": {record["path"]: record["sha256"] for record in inputs},
            "export_evidence_sha256": digest(export_evidence), "accepted_source_evidence": oracle_files,
            "validation_script": display_path(script), "validation_script_sha256": digest(script),
            "validation_command": command, "recipe_sql_sha256": recipe_hashes,
            "view_loading": "Parameterized DuckDB read_parquet views, equivalent to 00_load.sql without requiring its default release path",
            "duckdb_version": duckdb.__version__, "python": platform.python_version(),
            "host": {"system": platform.system(), "machine": platform.machine()},
            "duckdb_configuration": {"memory_limit": "4GB", "threads": 4, "temp_directory": "disabled"},
            "scope": "Software equivalence validation of full exported Parquet tables against accepted source aggregates. No source SQLite access or new scientific interpretation.",
        },
        "results": {
            "status": "passed", "comparison_checks": len(checks), "integer_comparison": "exact",
            "float_relative_tolerance": RELATIVE_TOLERANCE, "float_absolute_tolerance": ABSOLUTE_TOLERANCE,
            "macro_mean_absolute_tolerance": MACRO_MEAN_ABSOLUTE_TOLERANCE,
            "macro_mean_reason": "Python sequential sums and DuckDB AVG use different floating reduction orders across millions of owner fractions",
            "max_macro_mean_absolute_difference": max((item["absolute_error"] for item in checks
                if item["comparison"].startswith("macro")), default=0),
            "recipe_timings": timings, "total_seconds_including_input_hashes": time.monotonic() - started,
            "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
            "timing_scope": "One process, four DuckDB threads. Cache not flushed. Total includes input hashes and query work; per-recipe times exclude initial hashes.",
            "query_outputs": outputs, "comparisons": checks, "raw_source_database_opened": False,
            "input_files_modified": False, "private_collector_required": False,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "duckdb-recipes-validation.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"PASS: {len(checks)} comparisons; {display_path(output)}", flush=True)
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--release-dir", type=Path, required=True)
    cli.add_argument("--source-dir", type=Path, required=True)
    cli.add_argument("--export-evidence", type=Path, required=True)
    cli.add_argument("--out-dir", type=Path, required=True)
    args = cli.parse_args()
    validate(args.release_dir, args.source_dir, args.export_evidence, args.out_dir)


if __name__ == "__main__":
    main()
