"""Bounded unauthenticated ecosyste.ms contract probe; never a coverage estimate.

Run from the processing archive root. Only aggregate fields, numeric account
IDs, URL hashes and public publisher source code are saved, not API bodies.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from release_common import sha256

REVISION = "4e51f92e656dac574b5e0d45b21dabca1bc9dce7"
BASE = "https://repos.ecosyste.ms/api/v1/hosts/GitHub/owners/"
SOURCES = (
    "openapi/api/v1/openapi.yaml",
    "app/controllers/api/v1/owners_controller.rb",
    "app/views/api/v1/owners/_owner.json.jbuilder",
    "app/views/api/v1/repositories/_repository.json.jbuilder",
    "app/models/owner.rb",
    "app/models/hosts/github.rb",
)
HEADERS = {"Accept": "application/json", "Accept-Encoding": "identity",
           "User-Agent": "GitHub-Stars-in-Context-capability-comparison/1.0"}
BLOCKED = {401, 402, 403, 429, 451}
MAX_BYTES = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url, endpoint, timeout):
    """Exactly one GET, no credentials, retry, redirect, cookie or body persistence."""
    record = {"endpoint": endpoint, "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
              "started_at_utc": utc_now(), "method": "GET", "status": None,
              "response_sha256": None, "response_bytes": 0, "transport_error_type": None}
    started = time.monotonic()
    body = b""
    try:
        opener = urllib.request.build_opener(NoRedirect)
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            record["status"] = response.code
            record["headers"] = {key: response.headers[key] for key in
                                 ("Content-Type", "Content-Length", "Date", "Retry-After")
                                 if response.headers.get(key) is not None}
            record["pagination_link_present"] = response.headers.get("Link") is not None
            body = response.read(MAX_BYTES + 1)
            record["body_truncated"] = len(body) > MAX_BYTES
            record["response_bytes"] = len(body)
            record["response_sha256"] = hashlib.sha256(body).hexdigest()
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        # Error text can contain a login-bearing URL; retain only its class.
        record["transport_error_type"] = type(error).__name__
    record["elapsed_seconds"] = time.monotonic() - started
    record["ended_at_utc"] = utc_now()
    return record, body


def response_fields(record, body, kind):
    """Presence is not a semantic guarantee; no account text or field values saved."""
    if record["status"] != 200 or record.get("body_truncated") or record["transport_error_type"]:
        return {"assessment": "unavailable", "expected_upstream_repository_total": None,
                "owner_enumeration_completion_flag": None, "observation_timestamp": None,
                "portable_identifiers": None}
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return {"assessment": "non_json_response"}
    fields = ("uuid", "owner", "full_name", "stargazers_count", "fork", "last_synced_at")
    if kind == "repositories" and isinstance(value, list):
        rows = [row for row in value if isinstance(row, dict)]
        return {"assessment": "first_page_only_not_coverage", "returned_rows": len(value),
                "object_rows": len(rows), "nonnull_field_counts": {
                    field: sum(row.get(field) is not None for row in rows) for field in fields},
                "expected_upstream_repository_total": "not_established_by_array_response",
                "owner_enumeration_completion_flag": "not_established_by_array_response",
                "observation_timestamp": "last_synced_at_presence_only_not_common_observation_time",
                "portable_identifiers": "uuid_presence_only_mapping_requires_source_contract"}
    if kind == "owner" and isinstance(value, dict):
        return {"assessment": "owner_fields_not_completeness_proof", "nonnull_fields": {
                    field: value.get(field) is not None for field in
                    ("uuid", "repositories_count", "last_synced_at")},
                "expected_upstream_repository_total": "repositories_count_is_local_index_count",
                "owner_enumeration_completion_flag": "not_in_inspected_serializer",
                "observation_timestamp": "last_synced_at_presence_only",
                "portable_identifiers": "uuid_presence_only"}
    return {"assessment": "unexpected_json_shape"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("evidence/comparison"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output = args.out_dir / "ecosystems-capability.json"
    if output.exists():
        parser.error("choose a new --out-dir to preserve the existing run")
    db = (args.release_dir / "portfolios.sqlite").resolve()
    manifest_path = args.release_dir / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    actual_hash = sha256(db)
    expected_hash = next(item["sha256"] for item in manifest["files"]
                         if item["path"] == "portfolios.sqlite")
    if actual_hash != expected_hash:
        raise ValueError("Export checksum does not match release manifest")
    result = {"started_at_utc": utc_now(), "provenance": {
        "input_sha256": actual_hash, "source_sha256": manifest["source_provenance"]["input_sha256"],
        "release_manifest_sha256": sha256(manifest_path), "script_sha256": sha256(__file__),
        "helper_sha256": sha256(Path(__file__).with_name("release_common.py")),
        "python": platform.python_version(), "database_access": "mode=ro&immutable=1; query_only=ON",
        "analysis_manifest_sha256": sha256(args.release_dir / "analysis-manifest.json")},
        "request_policy": {"authentication": "none", "headers": HEADERS,
                           "max_account_requests": 20, "redirects": False, "retries": 0,
                           "per_response_byte_limit": MAX_BYTES,
                           "timeout_seconds": args.timeout,
                           "stop": "first access-denial/rate-limit, transport failure, redirect or truncated body",
                           "pagination": "first page only, never follow links"},
        "accounts": [], "requests": [], "primary_sources": [],
        "empirical_coverage_comparison": False,
        "limitations": ["Deterministic capability sample, not a population estimate",
                        "Complete is code-inferred, not independent rowset certification",
                        "Stored logins can be stale; 404 is not proof of missing owner data",
                        "Current API responses cannot validate unrecorded historical observation dates",
                        "Access failure is not evidence of competitor data absence"]}
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        count = conn.execute("SELECT COUNT(*) FROM users WHERE retrieval_status='complete'").fetchone()[0]
        if count < 10:
            raise ValueError("At least ten complete-class accounts are required")
        ranks = [(2 * i + 1) * count // 20 for i in range(10)]
        selected = set(ranks)
        samples = []
        cursor = conn.execute("SELECT id, github_id, github_name FROM users "
                              "WHERE retrieval_status='complete' ORDER BY github_id, id")
        for rank, (local_id, github_id, login) in enumerate(cursor):
            if rank in selected:
                samples.append((github_id, login))
                result["accounts"].append({"github_owner_id": github_id, "zero_based_rank": rank})
        result["sampling"] = {"cohort": "users.retrieval_status='complete'", "cohort_accounts": count,
                              "selected_accounts": 10, "ordering": "github_id ASC, id ASC",
                              "zero_based_rank_formula": "floor((2*i+1)*N/20), i=0..9",
                              "selection": "ten equal-rank-stratum midpoints; no random sampling"}
    result["stop_reason"] = "twenty_request_cap_reached"
    stop = False
    for github_id, login in samples:
        for kind in ("repositories", "owner"):
            suffix = "/repositories?fork=false&per_page=100&page=1" if kind == "repositories" else ""
            url = BASE + urllib.parse.quote(login, safe="") + suffix
            record, body = fetch(url, BASE + "{stored_login}" + suffix, args.timeout)
            record["github_owner_id"] = github_id
            record["kind"] = kind
            record["capabilities"] = response_fields(record, body, kind)
            result["requests"].append(record)
            status = record["status"]
            if (status in BLOCKED or record["transport_error_type"] or record.get("body_truncated")
                    or (status is not None and 300 <= status < 400)):
                result["stop_reason"] = "access_block_or_transport_limit; remaining sample not requested"
                stop = True
                break
        if stop:
            break
    # Archive the exact public contract used for interpretation, independently of API access.
    for path in SOURCES:
        url = f"https://raw.githubusercontent.com/ecosyste-ms/repos/{REVISION}/{path}"
        record, body = fetch(url, url, args.timeout)
        record["source_path"] = path
        if record["status"] == 200 and not record.get("body_truncated") and not record["transport_error_type"]:
            target = args.out_dir / "sources" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            record["saved_path"] = str(target.relative_to(args.out_dir))
        result["primary_sources"].append(record)
    result["contract_revision"] = REVISION
    result["summary"] = {"actual_account_requests": len(result["requests"]),
                         "accounts_attempted": len({r["github_owner_id"] for r in result["requests"]}),
                         "http_status_counts": dict(Counter(str(r["status"]) for r in result["requests"])),
                         "primary_source_requests": len(result["primary_sources"])}
    result["ended_at_utc"] = utc_now()
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
