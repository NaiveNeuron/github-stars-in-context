#!/usr/bin/env python3
"""Draw the paper's two figures from the released Parquet tables.

The plotted values come from the SQL recipes 01_thresholds.sql and
02_portfolios.sql. With --expected, they are first compared with the saved
results, and the script stops if they differ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42  # embed TrueType, not Type 3
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

RECIPES = Path(__file__).resolve().parent / "recipes"
TABLES = ("users", "repositories", "portfolios")
BLUE, VERMILLION, GREEN = "#0072B2", "#D55E00", "#009E73"
QUANTILES = ("min", "p05", "q1", "median", "q3", "p95", "max")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def query(db: duckdb.DuckDBPyConnection, recipe: str) -> list[dict]:
    cursor = db.execute((RECIPES / recipe).read_text())
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def compute(release_dir: Path) -> dict:
    db = duckdb.connect()
    for table in TABLES:
        db.read_parquet(str(release_dir / f"{table}.parquet")).create_view(f"release_{table}")
    thresholds = [{
        "threshold": row["threshold"],
        "kept": {"repository_rows": row["retained_row_fraction"],
                 "stars": row["retained_star_fraction"],
                 "accounts": row["retained_account_fraction"]},
        "omitted_among_kept_owners": {"repository_rows": row["micro_hidden_row_fraction"],
                                      "stars": row["micro_hidden_star_fraction"]},
    } for row in query(db, "01_thresholds.sql")]
    bands = [{
        "label": (f"\u2265{row['stars_lower_inclusive']:,}" if row["stars_upper_exclusive"] is None
                  else f"{row['stars_lower_inclusive']:,}\u2013{row['stars_upper_exclusive'] - 1:,}"),
        "accounts": row["multi_repository_accounts"],
        "dominant_share": dict(zip(QUANTILES, row["dominant_share_quantiles"])),
        "effective_breadth": dict(zip(QUANTILES, row["effective_breadth_quantiles"])),
    } for row in query(db, "02_portfolios.sql")]
    return {"thresholds": thresholds, "bands": bands}


def check(values: dict, expected_dir: Path) -> int:
    """Compare every plotted value with the saved results. Return the count."""
    saved_thresholds = json.loads((expected_dir / "r1-thresholds.json").read_text())["results"]["thresholds"]
    saved_bands = json.loads((expected_dir / "r2-portfolios.json").read_text())["results"]["bands"]
    pairs = []
    for mine, saved in zip(values["thresholds"], saved_thresholds, strict=True):
        pairs.append((mine["threshold"], saved["threshold"]))
        kept, omitted = saved["retained"], saved["conditional_on_retained_owner"]
        pairs += [(mine["kept"]["repository_rows"], kept["account_linked_repository_rows"]["share"]),
                  (mine["kept"]["stars"], kept["stars"]["share"]),
                  (mine["kept"]["accounts"], kept["accounts"]["share"]),
                  (mine["omitted_among_kept_owners"]["repository_rows"],
                   omitted["hidden_account_linked_repository_rows"]["share"]),
                  (mine["omitted_among_kept_owners"]["stars"], omitted["hidden_stars"]["share"])]
    for mine, saved in zip(values["bands"], saved_bands, strict=True):
        pairs.append((mine["accounts"], saved["n"]))
        for field in ("dominant_share", "effective_breadth"):
            pairs += [(mine[field][name], saved[field][name]) for name in QUANTILES]
    for actual, wanted in pairs:
        if not math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12):
            sys.exit(f"FAIL: plotted value {actual!r} differs from saved value {wanted!r}")
    return len(pairs)


def save(fig, out_dir: Path, stem: str) -> list[dict]:
    files = []
    for extension, options in (("pdf", {"metadata": {"CreationDate": None}}), ("png", {"dpi": 220})):
        path = out_dir / f"{stem}.{extension}"
        fig.savefig(path, **options)
        files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)})
    plt.close(fig)
    return files


def plot_thresholds(records: list[dict], out_dir: Path) -> list[dict]:
    x = [row["threshold"] for row in records]
    fig, axes = plt.subplots(2, 1, figsize=(3.5, 3.0), sharex=True)
    series = (("repository_rows", "Repository rows", "o", BLUE),
              ("stars", "Stars", "s", VERMILLION),
              ("accounts", "Accounts", "^", GREEN))
    for axis, field, names in ((axes[0], "kept", ("repository_rows", "stars", "accounts")),
                               (axes[1], "omitted_among_kept_owners", ("repository_rows", "stars"))):
        for key, label, marker, color in series:
            if key in names:
                axis.plot(x, [row[field][key] for row in records], marker=marker, color=color,
                          linewidth=1.3, markersize=3.5, label=label)
        axis.set_xscale("log")
        axis.set_xticks(x, labels=[f"{value:,}" for value in x])
        axis.minorticks_off()
        axis.set_ylim(-0.04, 1.04)
        axis.set_yticks([0, 0.5, 1])
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.tick_params(labelsize=7, length=2)
        axis.grid(alpha=0.25, linewidth=0.5)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(fontsize=7, frameon=False, ncol=len(names), loc="lower center",
                    bbox_to_anchor=(0.5, 1.0), handlelength=1.6, columnspacing=1.2,
                    borderaxespad=0.2)
    axes[0].set_ylabel("Share kept", fontsize=7)
    axes[1].set_ylabel("Share omitted", fontsize=7)
    axes[1].set_xlabel("Minimum stars per repository", fontsize=7)
    fig.subplots_adjust(left=0.16, right=0.98, top=0.93, bottom=0.13, hspace=0.42)
    return save(fig, out_dir, "r1-thresholds")


def plot_portfolios(bands: list[dict], out_dir: Path) -> list[dict]:
    fig, axes = plt.subplots(1, 2, figsize=(3.5, 2.5), sharey=True)
    labels = [f"{band['label']}\nn={band['accounts']:,}" for band in bands]
    for axis, field, title in zip(axes, ("dominant_share", "effective_breadth"),
                                  ("Dominant share $D$", "Effective breadth")):
        boxes = [{"med": band[field]["median"], "q1": band[field]["q1"], "q3": band[field]["q3"],
                  "whislo": band[field]["p05"], "whishi": band[field]["p95"]} for band in bands]
        axis.bxp(boxes, positions=range(len(bands)), orientation="horizontal", showfliers=False,
                 widths=0.5, patch_artist=True,
                 boxprops={"facecolor": "#cfe3f1", "edgecolor": BLUE, "linewidth": 0.7},
                 medianprops={"color": VERMILLION, "linewidth": 1.1},
                 whiskerprops={"color": BLUE, "linewidth": 0.7},
                 capprops={"color": BLUE, "linewidth": 0.7})
        axis.set_title(title, fontsize=8)
        axis.set_yticks(range(len(labels)), labels)
        axis.tick_params(labelsize=6.5, length=2)
        axis.grid(axis="x", alpha=0.25, linewidth=0.5)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].set_xlim(0, 1)
    axes[0].set_xticks([0, 0.5, 1])
    axes[0].set_ylabel("Total stars of the account", fontsize=7)
    axes[1].set_xscale("log")
    axes[1].tick_params(axis="y", labelleft=False)
    axes[0].set_ylim(-0.6, len(labels) - 0.4)
    axes[0].invert_yaxis()
    fig.subplots_adjust(left=0.27, right=0.98, bottom=0.1, top=0.91, wspace=0.2)
    return save(fig, out_dir, "r2-portfolios")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("results/figures"))
    parser.add_argument("--expected", type=Path, help="directory with r1-thresholds.json and r2-portfolios.json")
    args = parser.parse_args()

    values = compute(args.release_dir)
    compared = check(values, args.expected) if args.expected else 0
    args.out_dir.mkdir(parents=True, exist_ok=True)
    files = plot_thresholds(values["thresholds"], args.out_dir) + plot_portfolios(values["bands"], args.out_dir)
    record = {
        "inputs": {f"{table}.parquet": sha256(args.release_dir / f"{table}.parquet")
                   for table in ("repositories", "portfolios")},
        "script_sha256": sha256(Path(__file__)),
        "recipes": {name: sha256(RECIPES / name) for name in ("01_thresholds.sql", "02_portfolios.sql")},
        "versions": {"duckdb": duckdb.__version__, "matplotlib": matplotlib.__version__},
        "values_compared_with_saved_results": compared,
        "plotted_values": values,
        "files": files,
    }
    (args.out_dir / "figure-values.json").write_text(json.dumps(record, indent=2) + "\n")
    if compared:
        print(f"PASS: {compared} plotted values match the saved results")
    print(f"Wrote {', '.join(item['path'] for item in files)} to {args.out_dir}")


if __name__ == "__main__":
    main()
