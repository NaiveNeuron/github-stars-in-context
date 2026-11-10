#!/usr/bin/env python3
"""Compare saved source and released R1–R4 evidence without opening a database.

The export evidence supplies the released SQLite/analysis-manifest digests.
Every scientific leaf in the declared sections is compared exactly, including
counts, denominators, quantiles, boundary ties and selected-set fingerprints.
This is a local equivalence check, separate from independent public download.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('r1-thresholds', 'r2-portfolios', 'r3-owner-clustering', 'r4-gharchive-join')
SECTIONS = {
    'r1-thresholds': {'baseline', 'thresholds', 'parameters', 'input_account_records_by_retrieval_class'},
    'r2-portfolios': {'bands', 'cohort', 'h_summary_complete_cohort', 'h_vs_total_stars_top_k'},
    'r3-owner-clustering': {'baseline', 'thresholds', 'parameters', 'input_account_records_by_retrieval_class'},
    'r4-gharchive-join': {'archive', 'join'},
}
IGNORED = {
    'r1-thresholds': {'collector_semantics', 'figure_files', 'plot', 'policies', 'quality_evidence', 'shared_stream'},
    'r2-portfolios': {'collector_semantics', 'analysis_code_sha256', 'figure', 'figure_files',
                      'limitations', 'policies', 'quality_evidence', 'shared_stream'},
    'r3-owner-clustering': {'collector_semantics', 'policies', 'quality_evidence'},
    'r4-gharchive-join': {'limitations', 'policies', 'quality_evidence', 'source_scan'},
}
ARCHIVE_REQUIRED = {'hour_utc', 'compressed_sha256', 'plaintext_sha256', 'compressed_bytes',
                    'plaintext_bytes', 'counts', 'gzip_consumed_to_eof'}
ARCHIVE_IGNORED = {'http', 'documentation_url', 'raw_archive_saved'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    def reject(value):
        raise ValueError(f'Nonfinite JSON value {value} in {path}')
    return json.loads(Path(path).read_text(), parse_constant=reject)


def digest(value, label):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError(f'{label} must be a SHA-256 digest')
    return value


def required(mapping, keys, label):
    if not isinstance(mapping, dict):
        raise ValueError(f'{label} must be an object')
    missing = set(keys)-set(mapping)
    if missing:
        raise ValueError(f'{label} missing required sections: {sorted(missing)}')


def validate_quality_reference(document, label):
    provenance, results = document['provenance'], document['results']
    quality = results.get('quality_evidence')
    required(quality, {'input_sha256', 'manifest_sha256', 'physical_checks'}, label+'.quality_evidence')
    for key in ('input_sha256', 'manifest_sha256'):
        if quality[key] != provenance[key]:
            raise ValueError(f'{label} quality evidence has a different {key}')
    checks = quality['physical_checks']
    for key in ('invalid_stars', 'orphan_repository_rows', 'duplicate_key_excess_rows'):
        if type(checks.get(key)) is not int or checks[key] != 0:
            raise ValueError(f'{label} quality prerequisite {key} is not zero')


def lineage(source, released, export):
    required(export, {'provenance', 'results'}, 'export evidence')
    origin = export['provenance']
    original = digest(origin.get('input_sha256'), 'export original input')
    source_manifest = digest(origin.get('manifest_sha256'), 'export source manifest')
    kind = origin.get('data_kind')
    if kind not in ('research', 'synthetic'):
        raise ValueError('Export evidence has an unsupported data kind')
    record = export['results']
    if record.get('sampling', {}).get('requested_fraction') != 1:
        raise ValueError('Reproduction requires the full export, not a pilot sample')
    required(record.get('source_table_counts'), {'users', 'repositories'}, 'export source counts')
    required(record.get('released_table_counts'), {'users', 'repositories', 'portfolios'}, 'export released counts')
    for table in ('users', 'repositories'):
        if record['released_table_counts'][table] != record['source_table_counts'][table]:
            raise ValueError(f'Full export lost {table} rows')
    if record['released_table_counts']['portfolios'] != record['source_table_counts']['users']:
        raise ValueError('Full export portfolio/account counts differ')
    files = {}
    for item in record.get('files', []):
        filename = Path(item['path']).name
        if filename in files:
            raise ValueError(f'Ambiguous exported filename {filename}')
        files[filename] = item
    required(files, {'portfolios.sqlite', 'analysis-manifest.json'}, 'export file records')
    release_sha = digest(files['portfolios.sqlite'].get('sha256'), 'released SQLite')
    release_manifest = digest(files['analysis-manifest.json'].get('sha256'), 'released analysis manifest')
    for collection, role, input_sha, manifest_sha in (
            (source, 'unreleased_source', original, source_manifest),
            (released, 'released_export', release_sha, release_manifest)):
        for name in NAMES:
            document = collection[name]
            required(document, {'provenance', 'results'}, name)
            p = document['provenance']
            expected = {'input_sha256': input_sha, 'manifest_sha256': manifest_sha,
                        'source_sha256': original, 'input_role': role, 'data_kind': kind}
            for field, value in expected.items():
                if p.get(field) != value:
                    raise ValueError(f'{role}/{name}: {field} differs from export lineage')
            validate_quality_reference(document, role+'/'+name)
    return {'source_input_sha256': original, 'released_input_sha256': release_sha,
            'source_manifest_sha256': source_manifest,
            'released_analysis_manifest_sha256': release_manifest, 'data_kind': kind,
            'source_table_counts': record['source_table_counts'],
            'released_table_counts': record['released_table_counts']}


def scientific_sections(name, document):
    result = document['results']
    required(result, SECTIONS[name], name)
    unknown = set(result)-SECTIONS[name]-IGNORED[name]
    if unknown:
        raise ValueError(f'{name}: unreviewed result sections {sorted(unknown)}; classify them before comparing')
    selected = {key: result[key] for key in sorted(SECTIONS[name])}
    if name == 'r4-gharchive-join':
        archive = result['archive']
        required(archive, ARCHIVE_REQUIRED, name+'.archive')
        unknown_archive = set(archive)-ARCHIVE_REQUIRED-ARCHIVE_IGNORED-{'url'}
        if unknown_archive:
            raise ValueError(f'{name}.archive: unreviewed fields {sorted(unknown_archive)}')
        selected['archive'] = {key: value for key, value in archive.items() if key not in ARCHIVE_IGNORED}
    else:
        policy = result.get('collector_semantics')
        required(policy, {'policy_id', 'revision'}, name+'.collector_semantics')
        selected['collector_policy'] = {key: policy[key] for key in ('policy_id', 'revision')}
    if 'shared_stream' in result:
        selected['shared_stream'] = {key: value for key, value in result['shared_stream'].items()
                                     if key != 'data_pass_seconds'}
    return selected


def compare(left, right, path, compared, differences):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = path+'.'+key
            if key not in left or key not in right:
                differences.append({'path': child, 'reason': 'missing_field',
                                    'missing_from': 'source' if key not in left else 'released'})
            else:
                compare(left[key], right[key], child, compared, differences)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append({'path': path, 'reason': 'list_length', 'source': len(left), 'released': len(right)})
        for index, (a, b) in enumerate(zip(left, right)):
            compare(a, b, f'{path}[{index}]', compared, differences)
    else:
        compared.append(path)
        if type(left) is not type(right) or left != right:
            differences.append({'path': path, 'reason': 'value_or_type', 'source': left, 'released': right})


def verify_documents(source, released, export):
    chain = lineage(source, released, export)
    checks = {}
    for name in NAMES:
        left, right = scientific_sections(name, source[name]), scientific_sections(name, released[name])
        optional = {}
        if ('shared_stream' in left) != ('shared_stream' in right):
            optional['shared_stream'] = 'Not compared because coverage metadata was saved by only one runner'
            left.pop('shared_stream', None)
            right.pop('shared_stream', None)
        elif 'shared_stream' in left:
            optional['shared_stream'] = 'Coverage counts compared; operational duration excluded'
        compared, differences = [], []
        compare(left, right, name+'.results', compared, differences)
        if not compared:
            raise ValueError(f'{name}: no scientific leaves were compared')
        checks[name] = {'status': 'failed' if differences else 'passed',
                        'scientific_sections_compared': sorted(set(left) | set(right)),
                        'leaf_count': len(compared), 'field_paths_compared': compared,
                        'optional_runner_metadata': optional, 'differences': differences}
    return {'status': 'passed' if all(x['status'] == 'passed' for x in checks.values()) else 'failed',
            'lineage': chain, 'checks': checks,
            'comparison': 'Exact JSON values and types; no numerical tolerance. Top-k selected-set hashes are scientific results and are compared',
            'excluded': 'Operational timing, script/helper hashes, figures and their hashes, HTTP headers, and SQL query-plan metadata',
            'scope': 'Local saved-evidence aggregate equivalence. Independent download and reproduction remain separate checks'}


def run(source_dir, released_dir, export_evidence=None, out_dir=None):
    source_dir, released_dir = Path(source_dir), Path(released_dir)
    export_evidence = Path(export_evidence) if export_evidence else source_dir / 'export.json'
    out_dir = Path(out_dir) if out_dir else released_dir
    inputs = {}
    try:
        source, released = {}, {}
        for directory, collection, prefix in ((source_dir, source, 'source'), (released_dir, released, 'released')):
            for name in NAMES:
                path = directory / (name+'.json')
                collection[name] = load(path)
                inputs[prefix+'/'+path.name] = sha256(path)
        export = load(export_evidence)
        inputs['export.json'] = sha256(export_evidence)
        results = verify_documents(source, released, export)
        origin = results['lineage']
    except (OSError, ValueError, KeyError, TypeError) as error:
        results = {'status': 'failed', 'checks': {}, 'lineage_error': str(error)}
        origin = {}
    if origin.get('data_kind') == 'synthetic' and out_dir.resolve().is_relative_to(ROOT / 'evidence/release'):
        raise ValueError('Synthetic comparison cannot write research reproduction evidence')
    report = {'provenance': {**origin,
                             'input_role': 'released_export' if origin else 'unverified_evidence',
                             'input_sha256': origin.get('released_input_sha256'),
                             'source_sha256': origin.get('source_input_sha256'),
                             'manifest_sha256': origin.get('released_analysis_manifest_sha256'),
                             'generated_at': datetime.now(timezone.utc).isoformat(),
                             'script': Path(__file__).name, 'script_sha256': sha256(__file__),
                             'input_evidence_sha256': inputs, 'database_opened': False},
              'results': results}
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / 'reproduction.json'
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False)+'\n')
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--source-dir', type=Path, default=ROOT/'evidence/release')
    cli.add_argument('--released-dir', type=Path, default=ROOT/'evidence/exported-reuse')
    cli.add_argument('--export-evidence', type=Path)
    cli.add_argument('--out-dir', type=Path)
    args = cli.parse_args()
    report = run(args.source_dir, args.released_dir, args.export_evidence, args.out_dir)
    print(json.dumps({'status': report['results']['status'],
                      'output': str((args.out_dir or args.released_dir)/'reproduction.json')}))
    return 0 if report['results']['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
