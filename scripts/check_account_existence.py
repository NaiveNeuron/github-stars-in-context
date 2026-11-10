#!/usr/bin/env python3
"""Check whether sampled accounts still resolve on GitHub, by numeric ID.

Tests the collection operator's explanation that accounts with all-default
profile fields no longer existed when profiles were fetched. It measures
present-day resolution, not the state at enrichment time. Only aggregate
counts are saved: no logins, IDs or response bodies.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import subprocess
import urllib.error
import urllib.request

STRATA = {
    "profile_masked_complete": "profile_available = 0 AND retrieval_status = 'complete'",
    "profile_available": "profile_available = 1 AND retrieval_status = 'complete'",
}

def status(github_id: int, token: str) -> tuple[int, bool | None]:
    """Return the HTTP status and, for resolving accounts, whether followers >= 1 today."""
    request = urllib.request.Request(
        f"https://api.github.com/user/{github_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "github-stars-in-context-existence-check"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response).get("followers", 0) >= 1
    except urllib.error.HTTPError as error:
        return error.code, None



def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [centre - half, centre + half]


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", type=Path, required=True)
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--per-stratum", type=int, default=300)
    cli.add_argument("--seed", type=int, default=20261110)
    args = cli.parse_args()
    if args.out.exists():
        cli.error("choose a new output file")
    token = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True).stdout.strip()
    with args.db.open("rb") as stream:
        db_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    conn = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    result = {"provenance": {"input_sha256": db_sha, "seed": args.seed,
                             "started_at_utc": datetime.now(timezone.utc).isoformat(),
                             "endpoint": "GET https://api.github.com/user/{numeric_id}",
                             "saved": "aggregate counts only"},
              "strata": {}}
    for name, where in STRATA.items():
        ids = [row[0] for row in conn.execute(f"SELECT github_id FROM users WHERE {where} ORDER BY github_id")]
        sample = random.Random(f"{args.seed}:{name}").sample(ids, args.per_stratum)
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(lambda i: status(i, token), sample))
        counts = Counter(code for code, _ in answers)
        with_followers = sum(1 for code, has in answers if code == 200 and has)
        other = {str(c): n for c, n in counts.items() if c not in (200, 404)}
        decided = counts[200] + counts[404]
        result["strata"][name] = {
            "population": len(ids), "sampled": len(sample), "http_200": counts[200],
            "http_404": counts[404], "other_status": other,
            "resolving_with_at_least_one_follower_today": with_followers,
            "share_not_resolving": counts[404] / decided if decided else None,
            "wilson_95": wilson(counts[404], decided) if decided else None}
    result["provenance"]["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["strata"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
