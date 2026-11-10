#!/usr/bin/env python3
"""R4: stream one fixed GH Archive hour and join numeric repository IDs.

No archive or raw event file is saved. Only aggregate evidence is written.
The selected hour is a demonstration parameter, not a known collection end.
Event actors are not assumed to own repositories. WatchEvents do not provide
complete or cumulative repository star histories.
"""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_common import Context, parser, quote  # noqa: E402

ARCHIVE_HOUR_UTC = "2026-01-03T00:00:00Z"
ARCHIVE_URL = "https://data.gharchive.org/2026-01-03-0.json.gz"
MISSING_TYPE = "<missing_or_invalid_type>"
HTTP_HEADERS = ("content-type", "content-length", "content-encoding", "etag", "last-modified",
                "x-goog-generation", "x-goog-metageneration", "x-goog-hash", "date")


class CountingHashReader(io.RawIOBase):
    """Hash compressed transport bytes while gzip consumes bounded buffers."""
    def __init__(self, source):
        self.source = source
        self.byte_count = 0
        self.digest = hashlib.sha256()
        super().__init__()

    def readable(self):
        return True

    def readinto(self, buffer):
        chunk = self.source.read(len(buffer))
        if not chunk:
            return 0
        count = len(chunk)
        buffer[:count] = chunk
        self.byte_count += count
        self.digest.update(chunk)
        return count


class Hour:
    def __init__(self, http=None):
        # Repository IDs exist only as transient in-memory join keys.
        self.repository_events = {}
        self.event_types = Counter()
        self.event_types_missing_repo = Counter()
        self.counters = Counter()
        self.compressed_bytes = 0
        self.compressed_sha256 = ""
        self.plaintext_bytes = 0
        self.plaintext_sha256 = ""
        self.http = http or {}


def positive_integer(value):
    return type(value) is int and value > 0


def parse_hour(source, http=None) -> Hour:
    """Read an entire gzip stream, retaining only numeric repo/type counters.

    Invalid JSON lines and invalid repo IDs are explicitly counted. Gzip
    truncation/CRC errors propagate, so an incomplete archive cannot produce
    a successful result. The event unit is a parsed JSON object record.
    """
    hour = Hour(http=http or {})
    counted = CountingHashReader(source)
    plaintext_hash = hashlib.sha256()
    with io.BufferedReader(counted, buffer_size=65536) as compressed:
        with gzip.GzipFile(fileobj=compressed, mode="rb") as expanded:
            for line in expanded:
                hour.counters["lines"] += 1
                hour.plaintext_bytes += len(line)
                plaintext_hash.update(line)
                if not line.strip():
                    hour.counters["blank_lines"] += 1
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    hour.counters["malformed_json_lines"] += 1
                    continue
                if not isinstance(event, dict):
                    hour.counters["nonobject_json_lines"] += 1
                    continue
                hour.counters["events"] += 1
                kind = event.get("type")
                if not isinstance(kind, str) or not kind.strip():
                    kind = MISSING_TYPE
                    hour.counters["events_missing_or_invalid_type"] += 1
                hour.event_types[kind] += 1
                repo = event.get("repo")
                identifier = repo.get("id") if isinstance(repo, dict) else None
                if not positive_integer(identifier):
                    hour.counters["events_missing_or_invalid_repo_id"] += 1
                    hour.event_types_missing_repo[kind] += 1
                    continue
                hour.counters["events_with_valid_repo_id"] += 1
                if identifier not in hour.repository_events:
                    hour.repository_events[identifier] = Counter()
                hour.repository_events[identifier][kind] += 1
                # No event IDs, actors, owner/name strings or payloads are kept.
    hour.compressed_bytes = counted.byte_count
    if not hour.compressed_bytes:
        raise ValueError("Archive body is empty, expected gzip bytes")
    hour.compressed_sha256 = counted.digest.hexdigest()
    hour.plaintext_sha256 = plaintext_hash.hexdigest()
    declared = hour.http.get("headers", {}).get("content-length")
    if declared is not None and hour.compressed_bytes != int(declared):
        raise ValueError("Compressed byte count differs from HTTP Content-Length")
    return hour


def fetch_hour(timeout=60) -> Hour:
    """One GET of the preregistered hour. No fallback hour or disk cache."""
    request = urllib.request.Request(ARCHIVE_URL, headers={
        "Accept-Encoding": "identity", "User-Agent": "GitHub-Stars-in-Context-research/1.0"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"Archive HTTP status is {response.status}, expected 200")
        http = {"requested_url": ARCHIVE_URL, "final_url": response.url,
                "status": response.status,
                "headers": {name: response.headers[name] for name in HTTP_HEADERS if response.headers.get(name) is not None}}
        hour = parse_hour(response, http)
    hour.http["stream_seconds"] = time.monotonic() - started
    return hour


def fraction(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "share": numerator / denominator if denominator else None}


def join_membership(repository_ids, hour: Hour, progress=None) -> dict:
    """Scan source keys once. Keep counts only for this hour's distinct IDs."""
    matches = Counter()
    source_rows = invalid_source_ids = 0
    for identifier in repository_ids:
        source_rows += 1
        if not positive_integer(identifier):
            invalid_source_ids += 1
        elif identifier in hour.repository_events:
            matches[identifier] += 1
        if progress is not None and source_rows % 5_000_000 == 0:
            progress(source_rows)
    event_count = hour.counters["events"]
    keyed_events = hour.counters["events_with_valid_repo_id"]
    matched_types = Counter()
    unmatched_types = Counter()
    matched_distinct_by_type = Counter()
    unmatched_distinct_by_type = Counter()
    joined_types = Counter()
    for identifier, types in hour.repository_events.items():
        multiplicity = matches[identifier]
        for kind, count in types.items():
            if multiplicity:
                matched_types[kind] += count
                matched_distinct_by_type[kind] += 1
                joined_types[kind] += count * multiplicity
            else:
                unmatched_types[kind] += count
                unmatched_distinct_by_type[kind] += 1
    matched_events = sum(matched_types.values())
    unmatched_events = sum(unmatched_types.values())
    distinct = len(hour.repository_events)
    matched_distinct = len(matches)
    multiplicities = Counter(matches.values())
    result = {
        "source_account_linked_repository_rows_scanned": source_rows,
        "source_rows_with_missing_or_invalid_repository_id": invalid_source_ids,
        "hour_distinct_repository_ids": distinct,
        "matched_distinct_repository_ids": matched_distinct,
        "unmatched_distinct_repository_ids": distinct - matched_distinct,
        "distinct_repository_match_fraction": fraction(matched_distinct, distinct),
        "events_total": event_count,
        "events_with_valid_repo_id": keyed_events,
        "events_missing_or_invalid_repo_id": hour.counters["events_missing_or_invalid_repo_id"],
        "matched_events": matched_events, "unmatched_events": unmatched_events,
        "keyed_event_match_fraction": fraction(matched_events, keyed_events),
        "all_event_match_fraction": fraction(matched_events, event_count),
        "matched_account_linked_repository_rows": sum(matches.values()),
        "hour_repository_ids_matching_multiple_source_rows": sum(n for mult, n in multiplicities.items() if mult > 1),
        "max_source_row_multiplicity_for_matched_id": max(matches.values(), default=0),
        "source_row_multiplicity_histogram": [
            {"source_account_linked_rows_per_repo_id": mult, "hour_distinct_repo_ids": n}
            for mult, n in sorted(multiplicities.items())],
        "naive_event_to_source_row_join_pairs": sum(joined_types.values()),
        "event_type_breakdown": [{
            "type": kind, "all_events": hour.event_types[kind],
            "events_with_valid_repo_id": matched_types[kind] + unmatched_types[kind],
            "events_missing_or_invalid_repo_id": hour.event_types_missing_repo[kind],
            "matched_events": matched_types[kind], "unmatched_events": unmatched_types[kind],
            "matched_distinct_repository_ids": matched_distinct_by_type[kind],
            "unmatched_distinct_repository_ids": unmatched_distinct_by_type[kind],
            "naive_event_to_source_row_join_pairs": joined_types[kind],
        } for kind in sorted(hour.event_types)],
    }
    assert matched_events + unmatched_events == keyed_events
    assert keyed_events + hour.counters["events_missing_or_invalid_repo_id"] == event_count
    return result


def membership_scan_sql(conn):
    """Use an existing nonpartial covering index when one contains the key."""
    candidates = []
    for index in conn.execute('PRAGMA index_list("repositories")'):
        if index["partial"]:
            continue
        columns = [column["name"] for column in conn.execute(f'PRAGMA index_info({quote(index["name"])})')]
        if "repository_id" in columns:
            candidates.append((len(columns), index["name"]))
    selected = min(candidates)[1] if candidates else None
    sql = "SELECT repository_id FROM repositories"
    if selected:
        sql += " INDEXED BY " + quote(selected)
    plan = [row["detail"] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
    return sql, {"existing_index": selected, "query_plan": plan,
                 "covering_index_available": selected is not None,
                 "source_scans": 1, "per_archive_repository_sql_lookups": 0}


def run(ctx: Context, quality_evidence=None, timeout=60) -> dict:
    from reuse_stream import validate_quality
    if not (ctx.source_audit and ctx.collector_policy_active):
        ctx.require_release()
    quality = validate_quality(ctx, quality_evidence or ctx.out_dir / "quality.json")
    hour = fetch_hour(timeout)
    print(f"Streamed {hour.counters['events']:,} JSON event records; joining source keys", flush=True)
    started = time.monotonic()
    conn = ctx.connect()
    try:
        sql, scan = membership_scan_sql(conn)
        joined = join_membership((row[0] for row in conn.execute(sql)), hour,
                                 progress=lambda n: print(f"Scanned {n:,} source repository rows", flush=True))
    finally:
        conn.close()
    scan.update({"sql": sql, "seconds": time.monotonic() - started})
    counters = {key: hour.counters[key] for key in (
        "lines", "blank_lines", "malformed_json_lines", "nonobject_json_lines", "events",
        "events_missing_or_invalid_type", "events_with_valid_repo_id", "events_missing_or_invalid_repo_id")}
    result = {
        "archive": {"hour_utc": ARCHIVE_HOUR_UTC, "url": ARCHIVE_URL, "http": hour.http,
                    "compressed_bytes": hour.compressed_bytes, "compressed_sha256": hour.compressed_sha256,
                    "plaintext_bytes": hour.plaintext_bytes, "plaintext_sha256": hour.plaintext_sha256,
                    "counts": counters, "gzip_consumed_to_eof": True, "raw_archive_saved": False,
                    "documentation_url": "https://www.gharchive.org/"},
        "join": joined, "source_scan": scan, "quality_evidence": quality,
        "policies": {
            "hour_selection": "fixed 2026-01-03 00:00 UTC interoperability demonstration; source collection-end/window alignment is not established",
            "archive_schema": "GH Archive hourly JSONL, GitHub Events API schema used since 2015; positive integer repo.id is the join key",
            "source_population": "all source repositories rows, including rows linked to failed or unknown retrieval classes; no complete-cohort restriction",
            "join_key": "event.repo.id equals repositories.repository_id; local source row IDs are not cross-resource identifiers",
            "event_unit": "parsed JSON object record; event.id is not deduplicated",
            "missing_repo_ids": "counted separately and excluded only from the keyed-event match denominator",
            "multiplicity": "each event counted once for membership; account-linked source multiplicity and naive event-row join pairs reported separately",
            "memory": "one decompressed event line plus counters for distinct hour repository IDs/event types; never the archive or the entire source key corpus",
            "raw_output": "no compressed/expanded archive, event rows, repository IDs/names, actors or account identifiers saved",
            "http_failure": "fail without switching hour; no partial result is published on transport/gzip integrity failure",
        },
        "limitations": [
            "An event actor is not necessarily the repository owner; no actor-to-owner equivalence is inferred",
            "WatchEvents are event observations, not a complete or cumulative star history",
            "Matching repository identifiers does not synchronize event times and stored star observations",
            "A mismatch can reflect selection, forks, organization ownership, timing, deletion or other scope differences; it is not by itself a data error",
        ],
    }
    ctx.write_json("r4-gharchive-join.json", result)
    return result


def main():
    cli = parser(__doc__)
    cli.add_argument("--quality-evidence", type=Path)
    cli.add_argument("--request-timeout", type=float, default=60)
    args = cli.parse_args()
    if args.request_timeout <= 0:
        cli.error("--request-timeout must be positive")
    run(Context(args), args.quality_evidence, args.request_timeout)


if __name__ == "__main__":
    main()
