#!/usr/bin/env python3
"""R2: concentration, effective breadth and h-index versus total-star ranking.

One read-only stream supplies the complete code-inferred retrieval cohort.
The fixed total-star bands and top-k cutoffs are specified before running
this example on research data. No account names or selected IDs are exported.
"""
from __future__ import annotations
from array import array
from collections import Counter
import hashlib
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_common import Context, parser, sha256  # noqa: E402

BANDS = ((1, 10, "1–9"), (10, 100, "10–99"), (100, 1000, "100–999"),
         (1000, 10000, "1,000–9,999"), (10000, None, "≥10,000"))
TOP_K = (100, 1000, 10000)
QUANTILES = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
QUANTILE_KEYS = ("min", "p05", "q1", "median", "q3", "p95", "max")
LIMITATIONS = [
    "The complete class is inferred from the pinned collector state machine; it is not independent certification of the stored portfolio rowset",
    "A successful empty refresh can retain stale rows in the collector, and all analyses use the stored rows",
    "Results describe the follower-selected account population, not all GitHub accounts",
    "Popularity means repository stars; account ownership is not authorship or individual contribution",
]


def distribution(values) -> dict:
    values = np.asarray(values)
    if not len(values):
        return {"n": 0, **dict.fromkeys(QUANTILE_KEYS), "undefined_reason": "empty_population"}
    quantiles = np.quantile(values, QUANTILES, method="linear")
    return {"n": len(values), **{key: float(value) for key, value in zip(QUANTILE_KEYS, quantiles)},
            "undefined_reason": None}


def selected_set_sha256(ids) -> str:
    """Hash a set as ascending signed little-endian int64 GitHub account IDs."""
    ordered = np.sort(np.asarray(ids, dtype=np.int64)).astype("<i8", copy=False)
    return hashlib.sha256(memoryview(ordered)).hexdigest()


def overlap_count(a, b) -> int:
    return int(np.intersect1d(a, b, assume_unique=True).size)


def ranking_overlap(github_ids, h_index, total_stars, cutoffs=TOP_K) -> dict:
    """Report exact-k and cutoff-tie-inclusive results on the same accounts.

    `cutoffs` is exposed for fixed small unit-test oracles. The command-line
    experiment uses only the preregistered TOP_K constants.
    """
    cutoffs = tuple(cutoffs)
    ids = np.asarray(github_ids, dtype=np.int64)
    h = np.asarray(h_index, dtype=np.int64)
    stars = np.asarray(total_stars, dtype=np.int64)
    if not (len(ids) == len(h) == len(stars)):
        raise ValueError("Ranking arrays must describe the same account population")
    if len(np.unique(ids)) != len(ids):
        raise ValueError("Top-k ranking requires unique GitHub account IDs")
    orders = {"h_index": np.lexsort((ids, -h)),
              "total_stars": np.lexsort((ids, -stars))}
    metrics = {"h_index": h, "total_stars": stars}
    results = []
    for requested_k in cutoffs:
        if type(requested_k) is not int or requested_k < 1:
            raise ValueError("Top-k cutoffs must be positive")
        effective_k = min(int(requested_k), len(ids))
        exact, inclusive, exact_ids, inclusive_ids = {}, {}, {}, {}
        for name, values in metrics.items():
            order = orders[name]
            selected = ids[order[:effective_k]]
            cutoff = int(values[order[effective_k - 1]]) if effective_k else None
            n_above = int(np.count_nonzero(values > cutoff)) if cutoff is not None else 0
            n_tied = int(np.count_nonzero(values == cutoff)) if cutoff is not None else 0
            tied_selected = effective_k - n_above
            exact[name] = {
                "selected_n": effective_k, "cutoff": cutoff,
                "strictly_above_cutoff_n": n_above,
                "boundary_tie_n": n_tied,
                "boundary_ties_selected_n": tied_selected,
                "boundary_ties_excluded_n": n_tied - tied_selected,
                "selected_set_sha256": selected_set_sha256(selected),
            }
            at_or_above = ids[values >= cutoff] if cutoff is not None else ids[:0]
            inclusive[name] = {"selected_n": len(at_or_above), "cutoff": cutoff,
                               "boundary_tie_n": n_tied,
                               "selected_set_sha256": selected_set_sha256(at_or_above)}
            exact_ids[name] = selected
            inclusive_ids[name] = at_or_above
        intersection = overlap_count(exact_ids["h_index"], exact_ids["total_stars"])
        inclusive_intersection = overlap_count(inclusive_ids["h_index"], inclusive_ids["total_stars"])
        h_n = inclusive["h_index"]["selected_n"]
        s_n = inclusive["total_stars"]["selected_n"]
        results.append({
            "requested_k": int(requested_k), "effective_k": effective_k,
            "exact_k": {
                "h_index": exact["h_index"], "total_stars": exact["total_stars"],
                "overlap_n": intersection,
                "overlap_share_of_k": intersection / effective_k if effective_k else None,
                "jaccard": intersection / (2 * effective_k - intersection) if effective_k else None,
                "undefined_reason": None if effective_k else "empty_population",
            },
            "tie_inclusive": {
                "h_index": inclusive["h_index"], "total_stars": inclusive["total_stars"],
                "overlap_n": inclusive_intersection,
                "overlap_share_of_h_set": inclusive_intersection / h_n if h_n else None,
                "overlap_share_of_star_set": inclusive_intersection / s_n if s_n else None,
                "jaccard": inclusive_intersection / (h_n + s_n - inclusive_intersection)
                    if h_n + s_n else None,
                "undefined_reason": None if h_n + s_n else "empty_population",
            },
        })
    return {
        "n": len(ids), "unit": "GitHub account ID", "cutoffs": list(cutoffs),
        "comparisons": results,
        "exact_k_policy": "descending metric, ascending numeric GitHub account ID; select min(k,n)",
        "tie_inclusive_policy": "include every account with metric at least the metric-specific exact-k cutoff",
        "set_hash_encoding": "ascending signed little-endian int64 GitHub account IDs, no header; SHA256 of empty bytes for the empty set",
        "interpretation": "exact-k overlap and cutoff-tie-inclusive overlap have different denominators; inspect boundary ties before attributing differences to the metric",
    }


class Accumulator:
    """Keep account-level numeric arrays, never a repository corpus in memory."""
    def __init__(self):
        self.statuses = Counter()
        self.complete_n = self.empty_n = self.nonempty_zero_star_n = self.star_positive_n = 0
        self.github_ids, self.h, self.stars = array("q"), array("q"), array("q")
        self.data = [{"n": 0, "singletons": 0, "dominant_n": 0,
                      "D": array("d"), "effective_breadth": array("d")} for _ in BANDS]

    def consume(self, row: dict) -> None:
        status = row["retrieval_status"]
        self.statuses[status] += 1
        if status != "complete":
            return
        keys = ("github_id", "n_repositories", "total_stars", "max_stars",
                "sum_squared_stars", "h_observed")
        if any(not isinstance(row[key], int) or isinstance(row[key], bool) or row[key] < 0 for key in keys):
            raise ValueError("Complete-cohort summaries need nonnegative integer IDs/counts/measures")
        n, stars, maximum, squares = (row[key] for key in (
            "n_repositories", "total_stars", "max_stars", "sum_squared_stars"))
        if maximum > stars or (stars > 0 and (n == 0 or squares <= 0)):
            raise ValueError("Inconsistent portfolio summary")
        self.complete_n += 1
        self.github_ids.append(row["github_id"])
        self.h.append(row["h_observed"])
        self.stars.append(stars)
        if n == 0:
            self.empty_n += 1
        if not stars:
            self.nonempty_zero_star_n += n > 0
            return
        self.star_positive_n += 1
        index = next(i for i, (low, high, _) in enumerate(BANDS)
                     if stars >= low and (high is None or stars < high))
        band = self.data[index]
        if n == 1:
            band["singletons"] += 1
            return
        band["n"] += 1
        # Evaluate >=0.9 exactly: floating division can round a value just below it.
        band["dominant_n"] += 10 * maximum >= 9 * stars
        band["D"].append(maximum / stars)
        band["effective_breadth"].append(stars * stars / squares)

    def finalize(self) -> dict:
        bands = []
        for (low, high, label), data in zip(BANDS, self.data):
            bands.append({
                "label": label, "stars_lower_inclusive": low, "stars_upper_exclusive": high,
                "n": data["n"], "singleton_n": data["singletons"],
                "dominant_share": distribution(np.frombuffer(data["D"], dtype=np.float64)),
                "effective_breadth": distribution(np.frombuffer(data["effective_breadth"], dtype=np.float64)),
                "dominant_share_ge_0_9": {"n": int(data["dominant_n"]), "denominator": data["n"],
                    "share": data["dominant_n"] / data["n"] if data["n"] else None},
            })
        multi_n = sum(b["n"] for b in bands)
        singleton_n = sum(b["singleton_n"] for b in bands)
        h = np.frombuffer(self.h, dtype=np.int64)
        result = {
            "cohort": {
                "retrieval_status_counts": dict(sorted(self.statuses.items())),
                "complete_code_inferred_accounts": self.complete_n,
                "empty_accounts": self.empty_n,
                "nonempty_zero_star_accounts": self.nonempty_zero_star_n,
                "star_positive_accounts": self.star_positive_n,
                "multi_repository_star_positive_accounts": multi_n,
                "singleton_star_positive_accounts": singleton_n,
            },
            "bands": bands,
            "h_summary_complete_cohort": distribution(h),
            "h_vs_total_stars_top_k": ranking_overlap(
                np.frombuffer(self.github_ids, dtype=np.int64), h,
                np.frombuffer(self.stars, dtype=np.int64)),
            "policies": {
                "population": "retrieval_status=complete under the explicitly configured pinned collector policy or supplied evidence; code inference is not rowset certification",
                "R2_distribution_population": "total_stars>0 and n_repositories>=2, including zero-star repositories in n_repositories",
                "singletons": "total_stars>0 and n_repositories=1, counted separately per band and excluded from distribution boxes",
                "dominant_share": "D=max_stars/total_stars; D>=0.9 evaluated exactly as 10*max_stars>=9*total_stars",
                "effective_breadth": "total_stars**2/sum_squared_stars, using every observed repository including zero-star rows",
                "bands": "fixed total-star intervals [1,10),[10,100),[100,1000),[1000,10000),[10000,infinity)",
                "quantiles": "NumPy linear interpolation (Hyndman-Fan type 7); p05/p95 whiskers and q1/q3 boxes",
                "ranking_population": "all complete-code-inferred accounts, including zero-star and empty portfolios; unique numeric GitHub account IDs",
                "preregistered_top_k": list(TOP_K),
                "named_account_output": "none; selected sets are represented only by SHA256 hashes",
            },
            "limitations": LIMITATIONS,
            "analysis_code_sha256": sha256(Path(__file__)),
            "figure": {"files": ["r2-portfolios.pdf", "r2-portfolios.png"],
                       "width_inches": 3.5, "panels": ["dominant_share", "effective_breadth"],
                       "effective_breadth_axis": "logarithmic"},
        }
        assert multi_n + singleton_n == self.star_positive_n
        return result


def plot(ctx: Context, result: dict) -> dict:
    fig, axes = plt.subplots(1, 2, figsize=(3.5, 2.7), sharey=True)
    labels = [f"{b['label']}\nn={b['n']:,}" for b in result["bands"]]
    for axis, field, title in zip(axes, ("dominant_share", "effective_breadth"),
                                  ("Dominant share D", "Effective breadth")):
        boxes, positions = [], []
        for i, band in enumerate(result["bands"]):
            d = band[field]
            if d["n"]:
                boxes.append({"med": d["median"], "q1": d["q1"], "q3": d["q3"],
                              "whislo": d["p05"], "whishi": d["p95"]})
                positions.append(i)
        if boxes:
            axis.bxp(boxes, positions=positions, orientation="horizontal", showfliers=False,
                     widths=0.5, patch_artist=True,
                     boxprops={"facecolor": "#bfdbef", "edgecolor": "#315a75", "linewidth": 0.7},
                     medianprops={"color": "#8b2424", "linewidth": 0.9},
                     whiskerprops={"color": "#315a75", "linewidth": 0.7},
                     capprops={"color": "#315a75", "linewidth": 0.7})
        else:
            axis.text(0.5, 0.5, "No eligible\nportfolios", ha="center", va="center",
                      transform=axis.transAxes, fontsize=7)
        axis.set_title(title, fontsize=8)
        axis.set_yticks(range(len(labels)), labels)
        axis.tick_params(labelsize=6.5)
        axis.grid(axis="x", alpha=0.2)
    axes[0].set_xlim(0, 1)
    axes[0].set_xticks([0, 0.5, 1])
    axes[0].set_ylabel("Total stars / account count", fontsize=7)
    axes[1].set_xscale("log")
    axes[1].tick_params(axis="y", labelleft=False)
    axes[0].set_ylim(-0.6, len(labels) - 0.4)
    axes[0].invert_yaxis()
    fig.subplots_adjust(left=0.27, right=0.98, bottom=0.14, top=0.88, wspace=0.25)
    if ctx.provenance.get("data_kind") == "synthetic":
        fig.text(0.5, 0.5, "SYNTHETIC TEST DATA", color="crimson", alpha=0.3,
                 ha="center", va="center", rotation=25, fontsize=13)
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for extension in ("pdf", "png"):
        path = ctx.out_dir / f"r2-portfolios.{extension}"
        fig.savefig(path, dpi=220)
        files.append({"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size})
    plt.close(fig)
    result["figure"]["files"] = files
    return result["figure"]


def run(ctx: Context, quality_evidence=None) -> dict:
    if ctx.source_audit:
        if not ctx.collector_policy_active:
            raise ValueError("Source experiments require the explicit pinned collector policy")
    else:
        ctx.require_release()
    from reuse_stream import iter_summaries, validate_quality
    audit = validate_quality(ctx, quality_evidence or ctx.out_dir / "quality.json")
    accumulator = Accumulator()
    for row in iter_summaries(ctx, thresholds=()):
        accumulator.consume(row)
    result = accumulator.finalize()
    result["stream_code_sha256"] = sha256(Path(__file__).resolve().parents[1] / "scripts/reuse_stream.py")
    result["quality_evidence"] = audit
    result["collector_semantics"] = ctx.manifest.get("collector_semantics")
    plot(ctx, result)
    ctx.write_json("r2-portfolios.json", result)
    return result


def main() -> None:
    cli = parser(__doc__)
    cli.add_argument("--quality-evidence", type=Path)
    args = cli.parse_args()
    result = run(Context(args), args.quality_evidence)
    print("r2-portfolios.json:", result["cohort"]["multi_repository_star_positive_accounts"],
          "multi-repository star-positive accounts")


if __name__ == "__main__":
    main()
