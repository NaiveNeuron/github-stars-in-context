#!/usr/bin/env python3
"""Build the minimized release or a deterministic account-sampled size pilot.

The supplied SQLite snapshot is opened only through immutable Context. The
three Parquet tables and a separate SQLite file preserve every selected
account and its stored repository rows, including failed/stale portfolios.
Profile text and social-link URLs never enter the destination.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import sqlite3
import sys
import tempfile
import time

import pyarrow as pa
import pyarrow.parquet as pq

from release_common import Context, parser, quote, sha256
from reuse_stream import validate_quality

BATCH_SIZE = 10_000
RESERVE_BYTES = 5 * 1024**3
ESTIMATE_MARGIN = 1.25
DEFAULT_SEED = 20261106

# Explicit nullable schemas avoid inference from all-null/empty first batches.
USER_FIELDS = {
    'id': ('integer', 'Local account surrogate key, not the GitHub owner ID'),
    'github_id': ('integer', 'GitHub numeric owner identifier'),
    'github_name': ('string', 'Stored owner login, potentially different from its current login'),
    'created_at': ('string', 'Collector account-row insertion timestamp, not GitHub registration time'),
    'last_updated': ('string', 'Shared collector/profile/link stage timestamp or failure sentinel, not repository observation time'),
    'h_index': ('integer', 'Stored source h-index, independently recomputed in portfolios'),
    'repositories_fetched': ('integer', 'Unchanged raw collector flag, including negative values'),
    'followers_count': ('integer', 'Analysis value, NULL where the conservative profile-availability rule masks raw defaults'),
    'followers_count_raw': ('integer', 'Unchanged raw source follower count, including collection defaults'),
    'retrieval_status': ('string', 'Persisted collector-inferred class or explicit sidecar override; not independent rowset certification'),
    'profile_available': ('boolean', 'Conservative analysis availability flag, not proof that a profile request succeeded'),
    'bio_was_null': ('boolean', 'Whether the source bio was NULL; the bio text is excluded'),
    'profile_default_signature': ('boolean', 'Whether the source matched the conservative all-default profile signature'),
    'retrieval_inference_basis': ('string', 'Classification basis recorded by the pinned collector helper'),
    'retrieval_evidence_ref': ('string', 'Collector source-code reference or explicit record-level evidence reference'),
    'rowset_completeness_independently_certified': ('boolean', 'False: code-inferred completion does not independently certify stored rows'),
}
REPOSITORY_FIELDS = {
    'id': ('integer', 'Local repository-row surrogate key'),
    'user_id': ('integer', 'Local owner key joining users.id'),
    'repository_id': ('integer', 'GitHub numeric repository identifier; may occur under multiple owners'),
    'name': ('string', 'Stored repository name; retained without rewriting'),
    'created_at': ('string', 'GitHub repository createdAt on insertion, not retrieval time'),
    'stars': ('integer', 'Stored nonnegative integer star count'),
}
PORTFOLIO_FIELDS = {
    'user_id': ('integer', 'Local owner key joining users.id'),
    'github_id': ('integer', 'GitHub numeric owner identifier'),
    'n_repositories': ('integer', 'All stored account-linked repository rows, including zero-star rows'),
    'total_stars': ('integer', 'Sum of stars over stored account-linked repository rows'),
    'max_stars': ('integer', 'Largest stored repository star count; zero for an empty portfolio'),
    'sum_squared_stars': ('integer', 'Exact integer sum of squared repository star counts'),
    'h_observed': ('integer', 'h-index recomputed from descending stored star counts'),
    'h_stored': ('integer', 'Stored users.h_index value'),
    'starred_repositories': ('integer', 'Stored rows with at least one star'),
    'concentration': ('float', 'Herfindahl concentration sum((stars/total_stars)^2), NULL at zero total'),
    'effective_breadth': ('float', 'Reciprocal Herfindahl concentration, NULL at zero total'),
    'retrieval_status': ('string', 'Persisted account retrieval class'),
    'profile_available': ('boolean', 'Conservative profile-availability analysis flag'),
    'valid_portfolio': ('boolean', 'Source passed prerequisite physical checks; not a completeness certificate'),
    'eligible': ('boolean', 'Complete class under the declared code-inferred policy'),
    'exclusion_reason': ('string', 'Class-based analysis exclusion, if any; rows remain in the release'),
}
FIELDS = {'users': USER_FIELDS, 'repositories': REPOSITORY_FIELDS, 'portfolios': PORTFOLIO_FIELDS}
ARROW_TYPES = {'integer': pa.int64(), 'string': pa.string(), 'boolean': pa.bool_(), 'float': pa.float64()}
SQL_TYPES = {'integer': 'INTEGER', 'string': 'TEXT', 'boolean': 'INTEGER', 'float': 'REAL'}

CROSSWALK_SQL = '''CREATE VIEW identifier_crosswalk AS
SELECT r.id AS local_repository_row_id, u.id AS local_owner_id,
       u.github_id AS github_owner_id, r.repository_id AS github_repository_id,
       u.github_name AS owner_login, r.name AS repository_name_stored,
       CASE WHEN instr(r.name, '/') > 0 THEN substr(r.name, instr(r.name, '/') + 1)
            ELSE r.name END AS repository_name,
       u.github_name || '/' || CASE WHEN instr(r.name, '/') > 0
            THEN substr(r.name, instr(r.name, '/') + 1) ELSE r.name END AS owner_name,
       'https://github.com/' || u.github_name || '/' || CASE WHEN instr(r.name, '/') > 0
            THEN substr(r.name, instr(r.name, '/') + 1) ELSE r.name END AS repository_url
FROM repositories r JOIN users u ON u.id=r.user_id'''


def json_write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def memory_usage():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    darwin = platform.system() == 'Darwin'
    return {'peak_rss_bytes': raw if darwin else raw * 1024,
            'resource_ru_maxrss': raw, 'resource_ru_maxrss_unit': 'bytes' if darwin else 'KiB',
            'scope': 'Whole export process, including Context checksum work and dependencies'}


def check_headroom(directory, required_additional=0, reserve=RESERVE_BYTES):
    free = shutil.disk_usage(directory).free
    required = reserve + math.ceil(required_additional)
    if free < required:
        raise OSError(f'Insufficient export headroom: {free:,} free bytes; '
                      f'{required:,} required including the {reserve:,}-byte reserve')
    return {'free_bytes': free, 'reserve_bytes': reserve,
            'estimated_additional_bytes_with_margin': math.ceil(required_additional)}


def full_estimate(ctx, evidence):
    if evidence is None:
        raise ValueError('A full export requires --pilot-evidence from an account-sampled export')
    document = json.loads(Path(evidence).read_text())
    if document['provenance']['input_sha256'] != ctx.provenance['input_sha256']:
        raise ValueError('Pilot and full export input checksums differ')
    if document['provenance']['manifest_sha256'] != ctx.provenance['manifest_sha256']:
        raise ValueError('Pilot and full export classification manifests differ')
    pilot = document['results']
    fraction = pilot['sampling']['account_fraction']
    if not 0 < fraction < 1 or pilot['sampling']['selected_accounts'] < 1:
        raise ValueError('Pilot evidence must contain a nonempty proper account sample')
    estimate = pilot['artifact_bytes'] / fraction
    return {'pilot_evidence_sha256': sha256(evidence), 'pilot_artifact_bytes': pilot['artifact_bytes'],
            'scaled_estimate_bytes': estimate, 'margin_multiplier': ESTIMATE_MARGIN,
            'required_additional_bytes': math.ceil(estimate * ESTIMATE_MARGIN),
            'caveat': 'Account portfolios have heavy tails. This is an extrapolation from the pilot, not a guaranteed upper bound'}


def select_accounts(conn, accounts, fraction, seed):
    if fraction == 1:
        return None
    n = math.floor(accounts * fraction)
    if n < 1:
        raise ValueError('The requested sample selects zero accounts; increase its fraction')
    positions = set(random.Random(seed).sample(range(accounts), n))
    return [row[0] for position, row in enumerate(conn.execute('SELECT id FROM users ORDER BY id'))
            if position in positions]


def input_stream(conn, table, selected_ids):
    fields = '*' if table == 'users' else ','.join(map(quote, REPOSITORY_FIELDS))
    key = 'id' if table == 'users' else 'user_id'
    if selected_ids is None:
        yield from conn.execute(f'SELECT {fields} FROM {quote(table)} ORDER BY {quote(key)}')
    else:
        # Bounded IN lists use existing owner indexes, avoiding a full repository
        # scan for a 1% account pilot. Selected IDs are sorted from source order.
        for offset in range(0, len(selected_ids), 500):
            chunk = selected_ids[offset:offset+500]
            placeholders = ','.join('?' for _ in chunk)
            yield from conn.execute(f'SELECT {fields} FROM {quote(table)} '
                                    f'WHERE {quote(key)} IN ({placeholders}) ORDER BY {quote(key)}', chunk)


def validate_values(table, row):
    for name, (kind, _) in FIELDS[table].items():
        value = row[name]
        if value is None:
            continue
        if kind == 'integer':
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif kind == 'boolean':
            valid = type(value) is bool
        elif kind == 'string':
            valid = type(value) is str
        else:
            valid = type(value) in (int, float) and math.isfinite(value)
        if not valid:
            raise TypeError(f'{table}.{name}: expected nullable {kind}, got '
                            f'{type(value).__name__}; refusing lossy export conversion')


class TableWriter:
    def __init__(self, table, destination, directory, provenance):
        self.table, self.destination, self.directory = table, destination, directory
        self.rows, self.buffer = 0, []
        self.columns = list(FIELDS[table])
        self.schema = pa.schema([pa.field(name, ARROW_TYPES[kind], nullable=True)
                                 for name, (kind, _) in FIELDS[table].items()],
                                metadata={b'release_provenance': json.dumps(provenance, sort_keys=True).encode()})
        self.writer = pq.ParquetWriter(directory / (table + '.parquet'), self.schema, compression='zstd')
        declarations = []
        primary = 'user_id' if table == 'portfolios' else 'id'
        for name, (kind, _) in FIELDS[table].items():
            declarations.append(f'{quote(name)} {SQL_TYPES[kind]}' + (' PRIMARY KEY' if name == primary else ''))
        destination.execute(f'CREATE TABLE {quote(table)} ({", ".join(declarations)})')
        self.insert = f'INSERT INTO {quote(table)} VALUES ({",".join("?" for _ in self.columns)})'

    def append(self, row):
        validate_values(self.table, row)
        self.buffer.append(row)
        if len(self.buffer) >= BATCH_SIZE:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        check_headroom(self.directory)
        self.writer.write_table(pa.Table.from_pylist(self.buffer, schema=self.schema))
        self.destination.executemany(self.insert, [tuple(row[name] for name in self.columns) for row in self.buffer])
        self.rows += len(self.buffer)
        self.buffer.clear()
        if self.table == 'repositories' and self.rows % 1_000_000 == 0:
            print(f'Exported {self.rows:,} account-linked repository rows', flush=True)

    def close(self):
        self.flush()
        self.writer.close()


def canonical_user(ctx, source):
    missing = ctx.profile_fields_missing(source)
    status = ctx.classify_user(source)
    row = {key: source[key] for key in ('id', 'github_id', 'github_name', 'created_at',
                                      'last_updated', 'h_index', 'repositories_fetched')}
    row.update({'followers_count': None if missing else source['followers_count'],
                'followers_count_raw': source['followers_count'],
                'retrieval_status': status['status'], 'profile_available': not missing,
                'bio_was_null': 'bio' not in source.keys() or source['bio'] is None,
                'profile_default_signature': ctx.profile_default_signature(source),
                'retrieval_inference_basis': status['inference_basis'],
                'retrieval_evidence_ref': status['evidence_ref'],
                'rowset_completeness_independently_certified': False})
    return row


def canonical_portfolio(user, stars):
    ordered = sorted(stars, reverse=True)
    h = 0
    for position, value in enumerate(ordered, 1):
        if value < position:
            break
        h = position
    total, squares = sum(stars), sum(value * value for value in stars)
    complete = user['retrieval_status'] == 'complete'
    return {'user_id': user['id'], 'github_id': user['github_id'],
            'n_repositories': len(stars), 'total_stars': total, 'max_stars': max(stars, default=0),
            'sum_squared_stars': squares, 'h_observed': h, 'h_stored': user['h_index'],
            'starred_repositories': sum(value > 0 for value in stars),
            'concentration': squares / (total * total) if total else None,
            'effective_breadth': total * total / squares if squares else None,
            'retrieval_status': user['retrieval_status'], 'profile_available': user['profile_available'],
            'valid_portfolio': True, 'eligible': complete,
            'exclusion_reason': None if complete else 'retrieval_' + user['retrieval_status']}


def analysis_manifest(ctx, sample):
    # Only public, reusable configuration travels with the artifact. Runtime
    # audit paths/flags/notes are retained in evidence, not as reader dependencies.
    keys = ('data_kind', 'version', 'account_frame', 'observation_window', 'timestamp_semantics',
            'collector_semantics', 'data_license', 'code_license', 'reviewer_url', 'doi')
    result = {key: ctx.manifest.get(key) for key in keys}
    result.update({'retrieval_evidence_csv': None, 'validation_csv': None,
                   'retrieval_policy': 'Use persisted users.retrieval_status and profile_available from the source classification ledger',
                   'release_scope': 'All selected account records and their stored repository rows; profile text and user_links excluded',
                   'sampling': sample,
                   'classification_ledger': {'table': 'users', 'status': 'retrieval_status',
                                             'profile_availability': 'profile_available',
                                             'basis': 'Source sidecar overrides or revision-pinned code inference, persisted before private fields are removed'},
                   'author_decisions': {'data_license': ctx.manifest.get('data_license') or 'pending',
                                        'code_license': ctx.manifest.get('code_license') or 'pending',
                                        'restricted_profile_addon': 'pending; default artifact excludes profile text and social-link URLs'}})
    return result


def write_dictionary(ctx, directory, source_columns, counts):
    excluded = {table: sorted(set(columns)-set(FIELDS.get(table, {}))) for table, columns in source_columns.items()}
    dictionary = {'source_sha256': ctx.provenance['input_sha256'],
                  'tables': {table: {'rows': counts[table], 'fields': [
                      {'name': name, 'type': kind, 'nullable': True, 'description': description}
                      for name, (kind, description) in fields.items()]} for table, fields in FIELDS.items()},
                  'views': {'identifier_crosswalk': {'materialized': False,
                            'definition_file': 'crosswalk.sql',
                            'url_meaning': 'Constructed from stored owner/login and repository name, not verified as current'}},
                  'excluded_source_fields': excluded,
                  'timestamp_semantics': ctx.manifest.get('timestamp_semantics'),
                  'collector_semantics': ctx.manifest.get('collector_semantics'),
                  'profile_rule': 'NULL bio or ambiguous all-default signature implies unavailable analysis profile; raw counts are preserved separately',
                  'constant_sponsorship': 'sponsors_count is omitted because the collector writes a constant zero, not an observed sponsorship count',
                  'row_policy': 'No stored source account/repository rows are dropped within the chosen full or sampled scope; code-inferred complete does not certify rowset completeness'}
    json_write(directory / 'data-dictionary.json', dictionary)
    lines = ['# Release data dictionary', '',
             'Account and repository IDs named `id`/`user_id` are local surrogates.',
             'Numeric GitHub IDs and constructed names/URLs are exposed by the SQL crosswalk view.',
             'Private profile text and social-link URLs are excluded. License and restricted add-on decisions remain with the author.', '']
    for table, fields in FIELDS.items():
        lines += [f'## `{table}`', '', '| Field | Nullable type | Meaning |', '| --- | --- | --- |']
        lines += [f'| `{name}` | {kind} | {description} |' for name, (kind, description) in fields.items()]
        lines += ['']
    (directory / 'data-dictionary.md').write_text('\n'.join(lines) + '\n')
    return excluded


def export(ctx, export_dir, sample_fraction=1.0, seed=DEFAULT_SEED, quality_evidence=None,
           pilot_evidence=None, evidence_name=None):
    started = time.monotonic()
    export_dir = Path(export_dir).resolve()
    if not math.isfinite(sample_fraction) or not 0 < sample_fraction <= 1:
        raise ValueError('--sample-fraction must be in (0, 1]')
    if ctx.db.is_relative_to(export_dir) or ctx.out_dir.is_relative_to(export_dir):
        raise ValueError('Input database and evidence output must be outside --export-dir')
    if export_dir.exists():
        raise FileExistsError(f'Refusing to overwrite existing export directory: {export_dir}')
    export_dir.parent.mkdir(parents=True, exist_ok=True)
    audit = validate_quality(ctx, quality_evidence or ctx.out_dir / 'quality.json')
    estimate = full_estimate(ctx, pilot_evidence) if sample_fraction == 1 else None
    initial_headroom = check_headroom(export_dir.parent, estimate['required_additional_bytes'] if estimate else 0)
    counts = {}
    with closing(ctx.connect()) as source:
        source_counts = {table: source.execute(f'SELECT COUNT(*) FROM {quote(table)}').fetchone()[0]
                         for table in ('users', 'repositories')}
        source_columns = {table: [row[1] for row in source.execute(f'PRAGMA table_info({quote(table)})')]
                          for table in ('users', 'repositories', 'user_links')}
        selected_ids = select_accounts(source, source_counts['users'], sample_fraction, seed)
        selected_n = source_counts['users'] if selected_ids is None else len(selected_ids)
        sample = {'requested_fraction': sample_fraction, 'selected_accounts': selected_n,
                  'source_accounts': source_counts['users'],
                  'account_fraction': selected_n/source_counts['users'] if source_counts['users'] else None,
                  'seed': seed, 'method': 'all accounts' if selected_ids is None else 'Exact floor(N*fraction) positions sampled without replacement from accounts ordered by local id',
                  'python': sys.version.split()[0],
                  'selected_ids_sha256': hashlib.sha256('\n'.join(map(str, selected_ids)).encode()).hexdigest() if selected_ids is not None else None,
                  'caveat': 'Account sampling, not repository sampling. Portfolio-size heavy tails can make size extrapolation unstable'}
        with tempfile.TemporaryDirectory(prefix='.release-export-', dir=export_dir.parent) as temporary:
            staging = Path(temporary)
            with closing(sqlite3.connect(staging / 'portfolios.sqlite')) as destination:
                destination.execute('PRAGMA journal_mode=OFF')
                destination.execute('PRAGMA synchronous=OFF')
                destination.execute('PRAGMA temp_store=MEMORY')
                destination.execute('PRAGMA cache_size=-131072')
                writers = {table: TableWriter(table, destination, staging, ctx.provenance) for table in FIELDS}
                users = repositories = None
                try:
                    # Maintain indexes incrementally, avoiding a full 54M-row
                    # in-memory CREATE INDEX sort at the end of the export.
                    destination.execute('CREATE UNIQUE INDEX users_github_id_idx ON users(github_id)')
                    destination.execute('CREATE UNIQUE INDEX users_login_idx ON users(github_name)')
                    destination.execute('CREATE INDEX users_status_idx ON users(retrieval_status)')
                    destination.execute('CREATE INDEX repositories_owner_repo_idx ON repositories(user_id,repository_id)')
                    repositories = iter(input_stream(source, 'repositories', selected_ids))
                    current = next(repositories, None)
                    classes = Counter()
                    profiles_available = 0
                    users = input_stream(source, 'users', selected_ids)
                    for source_user in users:
                        user = canonical_user(ctx, source_user)
                        writers['users'].append(user)
                        classes[user['retrieval_status']] += 1
                        profiles_available += user['profile_available']
                        stars = []
                        if current is not None and current['user_id'] < user['id']:
                            raise ValueError('Unmatched repository owner despite prerequisite quality audit')
                        while current is not None and current['user_id'] == user['id']:
                            repository = dict(current)
                            if type(repository['stars']) is not int or repository['stars'] < 0:
                                raise ValueError('Invalid stars despite prerequisite quality audit')
                            writers['repositories'].append(repository)
                            stars.append(repository['stars'])
                            current = next(repositories, None)
                        writers['portfolios'].append(canonical_portfolio(user, stars))
                    if current is not None:
                        raise ValueError('Unmatched repository rows after final selected account')
                    for table, writer in writers.items():
                        writer.close()
                        counts[table] = writer.rows
                except BaseException:
                    for writer in writers.values():
                        writer.writer.close()
                    raise
                finally:
                    if users is not None:
                        users.close()
                    if repositories is not None:
                        repositories.close()
                if counts['users'] != selected_n or counts['portfolios'] != selected_n:
                    raise ValueError('Selected account counts do not match written tables')
                if sample_fraction == 1 and counts['repositories'] != source_counts['repositories']:
                    raise ValueError('Full export repository count differs from source count')
                destination.execute(CROSSWALK_SQL)
                destination.execute('CREATE TABLE release_metadata (key TEXT PRIMARY KEY,value TEXT)')
                source_provenance = {key: ctx.provenance[key] for key in ('input_sha256', 'data_kind', 'manifest_sha256')}
                source_provenance.update({'collector_semantics': ctx.provenance.get('collector_semantics'),
                                          'export_script_sha256': sha256(__file__), 'sampling': sample})
                manifest = analysis_manifest(ctx, sample)
                destination.executemany('INSERT INTO release_metadata VALUES (?,?)',
                                        [('provenance', json.dumps(source_provenance, sort_keys=True)),
                                         ('selection_manifest', json.dumps(manifest, sort_keys=True))])
                if ctx.provenance['data_kind'] == 'synthetic':
                    destination.execute('CREATE TABLE _fixture_metadata(label TEXT)')
                    destination.execute("INSERT INTO _fixture_metadata VALUES ('SYNTHETIC test data, never research results')")
                destination.commit()
            json_write(staging / 'analysis-manifest.json', manifest)
            (staging / 'crosswalk.sql').write_text(CROSSWALK_SQL + ';\n')
            excluded = write_dictionary(ctx, staging, source_columns, counts)
            records = [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
                       for path in sorted(staging.iterdir())]
            release_manifest = {'source_provenance': source_provenance,
                                'sampling': sample, 'source_table_counts': source_counts,
                                'released_table_counts': counts,
                                'retrieval_classes': dict(sorted(classes.items())),
                                'profile_available_accounts': profiles_available,
                                'files': records, 'excluded_source_fields': excluded,
                                'author_decisions': manifest['author_decisions']}
            json_write(staging / 'release-manifest.json', release_manifest)
            (staging / 'SHA256SUMS').write_text(''.join(f'{sha256(path)}  {path.name}\n'
                                                     for path in sorted(staging.iterdir())))
            artifact_bytes = sum(path.stat().st_size for path in staging.iterdir())
            check_headroom(staging)
            os.replace(staging, export_dir)
    result = {'export_directory': str(export_dir), 'sampling': sample,
              'source_table_counts': source_counts, 'released_table_counts': counts,
              'retrieval_classes': dict(sorted(classes.items())), 'profile_available_accounts': profiles_available,
              'files': [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
                        for path in sorted(export_dir.iterdir())],
              'artifact_bytes': artifact_bytes,
              'scaled_full_estimate_bytes': artifact_bytes/sample['account_fraction'] if sample['account_fraction'] else None,
              'heavy_tail_caveat': sample['caveat'], 'initial_headroom': initial_headroom,
              'pilot_projection': estimate, 'seconds': time.monotonic()-started,
              'memory': memory_usage(), 'quality_evidence': audit,
              'excluded_source_fields': excluded,
              'destination_database_access': {'journal_mode': 'OFF', 'synchronous': 'OFF',
                                               'temp_store': 'MEMORY', 'cache_kib': 131072,
                                               'note': 'Build-only settings on a new, disposable destination; supplied source remains immutable'},
              'crosswalk': {'view': 'identifier_crosswalk', 'materialized': False,
                            'github_keys': ['github_owner_id', 'github_repository_id'],
                            'local_keys': ['local_owner_id', 'local_repository_row_id']}}
    name = evidence_name or ('export-sample.json' if sample_fraction < 1 else 'export.json')
    ctx.write_json(name, result)
    return result


def main():
    cli = parser(__doc__)
    cli.add_argument('--export-dir', type=Path, default=Path('build/release-v2'))
    cli.add_argument('--sample-fraction', type=float, default=1.0)
    cli.add_argument('--seed', type=int, default=DEFAULT_SEED)
    cli.add_argument('--quality-evidence', type=Path)
    cli.add_argument('--pilot-evidence', type=Path)
    cli.add_argument('--evidence-name')
    args = cli.parse_args()
    export(Context(args), args.export_dir, args.sample_fraction, args.seed,
           args.quality_evidence, args.pilot_evidence, args.evidence_name)


if __name__ == '__main__':
    main()
