"""Shared offline analysis of the thesis SQLite schema. No API calls."""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
import hashlib
import itertools
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_COLUMNS = {
    "users": ["id", "github_id", "github_name", "created_at", "last_updated",
              "h_index", "repositories_fetched", "followers_count"],
    "repositories": ["id", "user_id", "repository_id", "name", "created_at", "stars"],
}
CLASSES = ("complete", "partial", "failed", "unprocessed", "unknown")
COLLECTOR_POLICY_ID = "git-h-index-fe2d7196-v1"
COLLECTOR_REVISION = "fe2d7196f4fe305a7a42bb871d267a751f5513f8"
FAILED_FETCH_SENTINEL = "6200-01-01T00:00:00"
PROFILE_COUNT_COLUMNS = ("followers_count", "following_count", "sponsors_count", "organizations_count")
PROFILE_SIGNATURE_COLUMNS = (*PROFILE_COUNT_COLUMNS, "bio", "bio_length", "company", "location")
USER_METADATA_COLUMNS = ("last_updated", "bio", "bio_length", "following_count", "sponsors_count",
                         "organizations_count", "company", "location", "followers_count_raw",
                         "retrieval_status", "profile_available", "bio_was_null",
                         "profile_default_signature", "retrieval_inference_basis", "retrieval_evidence_ref")
COLLECTOR_LIMITATIONS = [
    "Classes are inferred from the pinned collector state machine, not independent proof of the stored repository rowset",
    "An empty successful refresh can retain older rows because deletion runs only when seen_repo_ids is nonempty (database.py:115-120)",
    "last_updated is shared by discovery, profile and link stages and by sentinels; it is not a repository retrieval timestamp",
    "An all-default empty profile can represent either a failed profile request or a genuine empty profile; masking is conservative",
]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--db", type=Path, required=True)
    result.add_argument("--manifest", type=Path, default=ROOT / "release/manifest.json")
    result.add_argument("--out-dir", type=Path, default=ROOT / "evidence/release")
    result.add_argument("--expected-sha256", help="Expected source checksum; actual SHA256 is always computed")
    result.add_argument("--source-audit", action="store_true",
                        help="Audit an unreleased source DB; outputs are not manuscript results")
    return result


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Context:
    def __init__(self, args, check_schema=True):
        self.started = time.monotonic()
        script_path = Path(sys.argv[0]).resolve()
        self.code_provenance = {
            "script": Path(sys.argv[0]).name,
            "script_path": str(script_path) if script_path.is_file() else None,
            "script_sha256": sha256(script_path) if script_path.is_file() else None,
            "common_sha256": sha256(Path(__file__)),
            "code_hashes_captured_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db = args.db.resolve(strict=True)
        self.out_dir = args.out_dir.resolve()
        self.manifest_path = args.manifest.resolve(strict=True)
        self.manifest = json.loads(self.manifest_path.read_text())
        semantics = self.manifest.get("collector_semantics") or {}
        self.collector_policy_active = semantics.get("policy_id") == COLLECTOR_POLICY_ID
        if self.collector_policy_active and semantics.get("revision") != COLLECTOR_REVISION:
            raise ValueError("Collector semantics policy requires its exact pinned revision")
        self.user_columns = set()
        kind = self.manifest.get("data_kind")
        if kind not in {"synthetic", "research"}:
            raise ValueError("manifest.data_kind must be synthetic or research")
        expected = getattr(args, "expected_sha256", None)
        if expected is not None:
            expected = expected.lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("--expected-sha256 must be a 64-character hexadecimal SHA256")
        actual_sha = sha256(self.db)
        if expected is not None and actual_sha != expected:
            raise ValueError(f"Input SHA256 mismatch: expected {expected}, computed {actual_sha}")
        self.source_audit = bool(getattr(args, "source_audit", False))
        self.release_metadata = None
        self.provenance = {
            "input_sha256": actual_sha,
            "input_name": self.db.name,
            "input_bytes": self.db.stat().st_size,
            "manifest_sha256": sha256(self.manifest_path),
            "data_kind": kind,
            "release_version": self.manifest.get("version"),
            "sidecar_sha256": {},
            "expected_input_sha256": expected,
            "checksum_matches_expected": expected is not None,
            "source_audit": self.source_audit,
            "input_role": "unreleased_source",
            "manuscript_results_allowed": False,
            "database_access": {"mode": "ro", "immutable": True, "query_only": True,
                                "temp_store": "MEMORY", "cache_kib": 65536},
        }
        self.provenance["source_sha256"] = self.provenance["input_sha256"]
        self.provenance["collector_semantics"] = {
            "policy_id": semantics.get("policy_id"), "revision": semantics.get("revision"),
            "active": self.collector_policy_active,
            "classification_basis": "explicit sidecar overrides, otherwise pinned collector code inference" if self.collector_policy_active else "sidecar evidence only; otherwise unknown",
            "rowset_completeness_independently_certified": False,
            "limitations": COLLECTOR_LIMITATIONS if self.collector_policy_active else [],
        }
        self.statuses = {}
        self.validation = []
        for key, target in (("retrieval_evidence_csv", self.statuses),
                            ("validation_csv", self.validation)):
            relative = self.manifest.get(key)
            if not relative:
                continue
            path = (self.manifest_path.parent / relative).resolve(strict=True)
            self.provenance["sidecar_sha256"][key] = sha256(path)
            with path.open(newline="") as stream:
                for row in csv.DictReader(stream):
                    user_id = int(row["user_id"])
                    if key == "retrieval_evidence_csv":
                        status = row["status"]
                        ref = row.get("evidence_ref", "").strip()
                        if status not in CLASSES or (status != "unknown" and not ref):
                            raise ValueError("retrieval evidence needs a valid class and evidence_ref")
                        if user_id in target:
                            raise ValueError(f"duplicate retrieval evidence for user {user_id}")
                        target[user_id] = {"status": status, "evidence_ref": ref or None}
                    else:
                        if not row.get("evidence_ref", "").strip():
                            raise ValueError("validation rows need an evidence_ref")
                        target.append(row)
        if self.statuses and not self.manifest.get("retrieval_policy"):
            raise ValueError("document retrieval_policy before supplying status evidence")
        with closing(self.connect()) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "users" in tables:
                self.user_columns = {row[1] for row in conn.execute('PRAGMA table_info("users")')}
            self.provenance["collector_semantics"]["classification_columns_available"] = {"bio", "last_updated"}.issubset(self.user_columns)
            self.provenance["collector_semantics"]["profile_signature_columns_available"] = set(PROFILE_SIGNATURE_COLUMNS).issubset(self.user_columns)
            if "release_metadata" in tables:
                row = conn.execute("SELECT value FROM release_metadata WHERE key='provenance'").fetchone()
                if row:
                    metadata = json.loads(row[0])
                    source_sha = metadata.get("input_sha256") if isinstance(metadata, dict) else None
                    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
                        raise ValueError("release_metadata.provenance lacks a valid source input_sha256")
                    if metadata.get("data_kind") != kind:
                        raise ValueError("release_metadata and manifest data kinds disagree")
                    self.release_metadata = metadata
                    self.provenance["source_sha256"] = source_sha
                    if not self.source_audit:
                        self.provenance["input_role"] = "released_export"
                        self.provenance["manuscript_results_allowed"] = True
            self._validate_release_ledger(conn)
            self.provenance["collector_semantics"]["classification_ledger_used"] = self._has_release_field("retrieval_status")
            self.provenance["collector_semantics"]["profile_availability_ledger_used"] = self._has_release_field("profile_available")
            if self._has_release_field("retrieval_status"):
                self.provenance["collector_semantics"]["classification_basis"] = "persisted released account classification ledger"
            fixture = "_fixture_metadata" in tables
            if fixture != (kind == "synthetic"):
                raise ValueError("synthetic DB marker and manifest.data_kind disagree")
            if kind == "synthetic" and self.out_dir.is_relative_to(ROOT / "evidence/release"):
                raise ValueError("synthetic outputs must not enter evidence/release")
            for table, needed in (CORE_COLUMNS.items() if check_schema else []):
                actual = {r[1] for r in conn.execute(f"PRAGMA table_info({quote(table)})")}
                missing = set(needed) - actual
                if missing:
                    raise ValueError(f"{table}: missing thesis columns {sorted(missing)}")
            for user_id in self.statuses:
                if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
                    raise ValueError(f"retrieval evidence refers to absent user {user_id}")
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def connect(self):
        # Input must be a closed, checkpointed snapshot. immutable=1 prevents
        # journal/lock sidecars; all temporary query work stays in memory.
        conn = sqlite3.connect(self.db.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-65536")
        return conn

    def require_release(self):
        """Reject reuse analysis of an unreleased or explicitly audited source."""
        if self.source_audit or self.release_metadata is None:
            raise ValueError("Reuse examples require a released SQLite export, not an unreleased SOURCE audit")

    @staticmethod
    def _value(row, name, default=None):
        try:
            return row[name]
        except (KeyError, IndexError):
            return default

    @staticmethod
    def _keys(row):
        return set(row.keys())

    @staticmethod
    def _column(alias, name):
        return (quote(alias) + "." if alias else "") + quote(name)

    def _has_release_field(self, name):
        """Trust persisted semantics only on an identified released export."""
        return (getattr(self, "release_metadata", None) is not None
                and not getattr(self, "source_audit", False)
                and getattr(self, "provenance", {}).get("input_role") == "released_export"
                and name in self.user_columns)

    def _validate_release_ledger(self, conn):
        if self._has_release_field("retrieval_status"):
            placeholders = ",".join("?" for _ in CLASSES)
            invalid = conn.execute(
                f"SELECT retrieval_status FROM users WHERE retrieval_status IS NULL "
                f"OR retrieval_status NOT IN ({placeholders}) LIMIT 1", CLASSES).fetchone()
            if invalid is not None:
                raise ValueError("Released retrieval_status must be one of the declared retrieval classes")
        for name in ("profile_available", "bio_was_null", "profile_default_signature"):
            if self._has_release_field(name):
                column = quote(name)
                invalid = conn.execute(
                    f"SELECT {column} FROM users WHERE {column} IS NULL "
                    f"OR typeof({column})!='integer' OR {column} NOT IN (0,1) LIMIT 1").fetchone()
                if invalid is not None:
                    raise ValueError(f"Released {name} must contain nonnull Boolean 0/1 values")

    @staticmethod
    def _ledger_value(row, name):
        if name not in row.keys():
            raise ValueError(f"Released account projection lacks persisted {name}")
        return row[name]

    @staticmethod
    def _ledger_boolean(row, name):
        value = Context._ledger_value(row, name)
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        raise ValueError(f"Released {name} must be a Boolean or integer 0/1")

    def classification_sql(self, alias="u"):
        """Return a collector-inferred class expression with sidecar overrides.

        'complete' means code-inferred fetch-completed. It does not certify
        that the persisted rowset is an independently verified full portfolio.
        """
        if self._has_release_field("retrieval_status"):
            return self._column(alias, "retrieval_status"), []
        flag = self._column(alias, "repositories_fetched")
        base, params = "'unknown'", []
        if self.collector_policy_active:
            if {"bio", "last_updated"}.issubset(self.user_columns):
                updated, bio = self._column(alias, "last_updated"), self._column(alias, "bio")
                base = (f"CASE WHEN {flag}=-1 THEN 'unknown' "
                        f"WHEN {flag}=1 AND {updated}=? AND {bio} IS NULL THEN 'failed' "
                        f"WHEN {flag}=1 THEN 'complete' WHEN {flag}=0 THEN 'unprocessed' ELSE 'unknown' END")
                params = [FAILED_FETCH_SENTINEL]
            else:
                # A minimized table without the discriminator cannot decide
                # whether a raw flag of one means success or failure.
                base = f"CASE WHEN {flag}=0 THEN 'unprocessed' ELSE 'unknown' END"
        if self.statuses:
            user_id = self._column(alias, "id")
            cases, overrides = [], []
            for identifier, evidence in sorted(self.statuses.items()):
                cases.append(f"WHEN {user_id}=? THEN ?")
                overrides.extend([identifier, evidence["status"]])
            base = "CASE " + " ".join(cases) + " ELSE " + base + " END"
            params = overrides + params
        return base, params

    def classify_user(self, row):
        """Python counterpart of classification_sql, preserving evidence basis."""
        if self._has_release_field("retrieval_status"):
            status = self._ledger_value(row, "retrieval_status")
            if status not in CLASSES:
                raise ValueError("Released retrieval_status is outside the declared retrieval classes")
            return {"status": status,
                    "evidence_ref": self._value(row, "retrieval_evidence_ref") or "released users.retrieval_status ledger",
                    "inference_basis": self._value(row, "retrieval_inference_basis") or "persisted_release_ledger"}
        identifier = self._value(row, "user_id", self._value(row, "id"))
        if identifier in self.statuses:
            return {**self.statuses[identifier], "inference_basis": "explicit_sidecar"}
        if not self.collector_policy_active:
            return {"status": "unknown", "evidence_ref": None, "inference_basis": "unavailable"}
        flag = self._value(row, "repositories_fetched")
        if flag == -1:
            status, reference = "unknown", "data_storage/database.py:257-289 (reserved/in-progress)"
        elif flag == 0:
            status, reference = "unprocessed", "data_storage/database.py:264-270 (awaiting repository fetch)"
        elif flag == 1 and {"bio", "last_updated"}.issubset(self._keys(row)):
            if self._value(row, "last_updated") == FAILED_FETCH_SENTINEL and self._value(row, "bio") is None:
                status, reference = "failed", "data_storage/database.py:243-249 (failure sentinel with unprocessed profile)"
            else:
                status, reference = "complete", "data_storage/database.py:198-223;data_fetching/github_parser.py:56-132 (fetch-completed inference)"
        else:
            status, reference = "unknown", "unrecognized flag or missing success/failure discriminator"
        return {"status": status, "evidence_ref": f"collector@{COLLECTOR_REVISION}:{reference}",
                "inference_basis": "collector_code_inference"}

    def profile_default_signature_sql(self, alias="u"):
        """All-default failure-like signature, also possible for genuine profiles."""
        if self._has_release_field("profile_default_signature"):
            return self._column(alias, "profile_default_signature")
        if not self.collector_policy_active or not set(PROFILE_SIGNATURE_COLUMNS).issubset(self.user_columns):
            return "0"
        col = lambda name: self._column(alias, name)
        parts = [f"{col(name)}=0" for name in PROFILE_COUNT_COLUMNS]
        parts += [f"{col('bio')}=''", f"{col('bio_length')}=0",
                  f"{col('company')} IS NULL", f"{col('location')} IS NULL"]
        return "(" + " AND ".join(parts) + ")"

    def profile_default_signature(self, row):
        if self._has_release_field("profile_default_signature"):
            return self._ledger_boolean(row, "profile_default_signature")
        if not self.collector_policy_active or not set(PROFILE_SIGNATURE_COLUMNS).issubset(self._keys(row)):
            return False
        return (all(self._value(row, name) == 0 for name in PROFILE_COUNT_COLUMNS)
                and self._value(row, "bio") == "" and self._value(row, "bio_length") == 0
                and self._value(row, "company") is None and self._value(row, "location") is None)

    def profile_missing_sql(self, alias="u"):
        """Conservative analysis mask for untreated or ambiguous default profiles.

        Sentinel and nonsentinel all-default profiles are both masked. A
        later link failure can overwrite the timestamp after profile failure.
        """
        if self._has_release_field("profile_available"):
            return f"(NOT {self._column(alias, 'profile_available')})"
        if not self.collector_policy_active:
            return "0"
        if "bio" not in self.user_columns:
            return "1"
        return f"({self._column(alias, 'bio')} IS NULL OR {self.profile_default_signature_sql(alias)})"

    def profile_fields_missing(self, row):
        if self._has_release_field("profile_available"):
            return not self._ledger_boolean(row, "profile_available")
        if not self.collector_policy_active:
            return False
        if "bio" not in self._keys(row):
            return True
        return self._value(row, "bio") is None or self.profile_default_signature(row)

    def write_json(self, name, payload):
        provenance = dict(self.provenance)
        provenance.update({"generated_at": datetime.now(timezone.utc).isoformat(),
                           **self.code_provenance,
                           "python": sys.version.split()[0],
                           "elapsed_seconds": time.monotonic()-self.started})
        path = self.out_dir / name
        path.write_text(json.dumps({"provenance": provenance, "results": payload},
                                   indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(path)
        return path

    def iter_portfolios(self, conn):
        """One row per source account, including empty/unfetched/invalid accounts.

        Without a manifest-pinned policy the DB flag is descriptive only.
        Status comes from explicit sidecar evidence or the pinned collector
        state-machine inference, which does not certify rowset completeness.
        Repository rows are streamed in account order, not loaded as a corpus.
        """
        duplicate_ids = {r[0] for r in conn.execute(
            "SELECT id FROM users GROUP BY id HAVING COUNT(*)>1")}
        metadata = ", ".join(f"u.{quote(name)} AS {quote(name)}" for name in USER_METADATA_COLUMNS if name in self.user_columns)
        metadata = (metadata + ",") if metadata else ""
        query = f"""SELECT u.rowid AS account_record, u.id AS user_id, u.github_id, u.github_name,
            u.h_index AS h_stored, u.followers_count, u.repositories_fetched, {metadata}
            r.id AS row_id, r.user_id AS owner_join_marker, r.repository_id, r.stars
            FROM users u LEFT JOIN repositories r ON r.user_id=u.id
            ORDER BY u.rowid, r.stars DESC, r.id"""
        for _, group in itertools.groupby(conn.execute(query), lambda r: r["account_record"]):
            count = total = maximum = breadth = h = squares = 0
            seen = set()
            reasons = set()
            first = None
            for row in group:
                if first is None:
                    first = row
                    user_id = row["user_id"]
                    if user_id is None or user_id in duplicate_ids:
                        reasons.add("ambiguous_account_key")
                if row["owner_join_marker"] is None:
                    continue
                if row["row_id"] is None:
                    reasons.add("missing_repository_row_key")
                count += 1
                repo_id, stars = row["repository_id"], row["stars"]
                if repo_id is None:
                    reasons.add("missing_repository_id")
                elif repo_id in seen:
                    reasons.add("duplicate_repository_key")
                seen.add(repo_id)
                if not is_count(stars):
                    reasons.add("invalid_stars")
                    continue
                total += stars
                squares += stars * stars
                maximum = max(maximum, stars)
                breadth += stars > 0
                if stars >= count:
                    h = count
            status = self.classify_user(first)
            profile_missing = self.profile_fields_missing(first)
            raw_followers = self._ledger_value(first, "followers_count_raw") if self._has_release_field("followers_count_raw") else first["followers_count"]
            profile_unprocessed = (self._ledger_boolean(first, "bio_was_null") if self._has_release_field("bio_was_null")
                                   else self.collector_policy_active and "bio" in self._keys(first) and first["bio"] is None)
            valid = not reasons
            eligible = valid and status["status"] == "complete"
            yield {"user_id": user_id, "github_id": first["github_id"],
                   "github_name": first["github_name"], "h_stored": first["h_stored"],
                   "followers_count": None if profile_missing else first["followers_count"],
                   "raw_followers_count": raw_followers,
                   "profile_fields_missing": profile_missing,
                   "profile_failure_pattern": self.profile_default_signature(first),
                   "profile_unprocessed": profile_unprocessed,
                   "repositories_fetched": first["repositories_fetched"],
                   "retrieval_status": status["status"], "evidence_ref": status["evidence_ref"],
                   "retrieval_inference_basis": status["inference_basis"],
                   "rowset_completeness_independently_certified": False,
                   "n_repositories": count, "h_observed": h if valid else None,
                   "total_stars": total if valid else None,
                   "max_stars": maximum if valid else None,
                   "starred_repositories": breadth if valid else None,
                   "concentration": squares / (total * total) if valid and total else None,
                   "valid_portfolio": valid, "eligible": eligible,
                   "exclusion_reason": ",".join(sorted(reasons)) if reasons else
                       (None if eligible else "retrieval_" + status["status"])}
