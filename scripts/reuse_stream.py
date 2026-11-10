"""Stream account-linked source rows once for the approved R1/R2 diagnostics.

No database, cache of accounts, or exported table is written. Repository rows
are grouped through the existing owner index. The temporary star list belongs
to one account only; zero-star rows remain part of its repository count.
"""
from __future__ import annotations

import json
from pathlib import Path

from release_common import is_count, sha256

DEFAULT_THRESHOLDS = (1, 10, 100, 1000)


def validate_quality(ctx, path=None):
    """Require the matching accepted physical audit and a declared class policy."""
    if ctx.provenance.get('input_role') != 'released_export':
        if not ctx.source_audit or not ctx.collector_policy_active:
            raise ValueError('Unreleased reuse diagnostics require --source-audit and the pinned collector policy')
    path = Path(path) if path else ctx.out_dir / 'quality.json'
    document = json.loads(path.read_text())
    if document['provenance']['input_sha256'] != ctx.provenance['input_sha256']:
        raise ValueError('Quality audit and reuse input checksums differ')
    if document['provenance']['manifest_sha256'] != ctx.provenance['manifest_sha256']:
        raise ValueError('Quality audit and reuse classification manifests differ')
    result = document['results']
    if result['invalid_stars'] or result['orphans']['repositories']:
        raise ValueError('Resolve invalid stars or orphan repository rows before these examples')
    for name, counts in result['duplicates'].items():
        if counts['excess_rows']:
            raise ValueError(f'Resolve duplicate-key violations before reuse: {name}')
    ctx.provenance['prerequisite_quality_sha256'] = sha256(path)
    ctx.provenance['reuse_stream_sha256'] = sha256(__file__)
    ctx.provenance['analysis_population'] = 'code-inferred complete account portfolios'
    ctx.provenance['independent_rowset_certification'] = False
    return {'quality_sha256': sha256(path),
            'input_sha256': document['provenance']['input_sha256'],
            'manifest_sha256': document['provenance']['manifest_sha256'],
            'retrieval_classes': result['retrieval_classes'],
            'physical_checks': {'invalid_stars': result['invalid_stars'],
                                'orphan_repository_rows': result['orphans']['repositories'],
                                'duplicate_key_excess_rows': sum(v['excess_rows'] for v in result['duplicates'].values())}}


def iter_summaries(ctx, thresholds=DEFAULT_THRESHOLDS):
    """Yield every source account, including empty and failed/unknown accounts.

    Each consumer selects its declared population. A repository ID associated
    with multiple accounts remains a separate account-linked observation.
    No profile fields or account names are returned.
    """
    thresholds = tuple(thresholds)
    if thresholds != tuple(sorted(set(thresholds))) or any(type(t) is not int or t < 1 for t in thresholds):
        raise ValueError('Thresholds must be distinct positive integers in ascending order')
    conn = ctx.connect()
    try:
        classification, parameters = ctx.classification_sql('u')
        users = conn.execute(f'SELECT u.id,u.github_id,u.h_index,{classification} AS retrieval_status '
                             'FROM users u ORDER BY u.id', parameters)
        repositories = iter(conn.execute('SELECT user_id,stars FROM repositories ORDER BY user_id'))
        current = next(repositories, None)
        for user in users:
            identifier = user['id']
            if current is not None and current['user_id'] < identifier:
                raise ValueError('Unmatched repository owner encountered; source audit must be refreshed')
            stars = []
            total = maximum = squares = 0
            retained_counts = [0] * len(thresholds)
            retained_stars = [0] * len(thresholds)
            while current is not None and current['user_id'] == identifier:
                value = current['stars']
                if not is_count(value):
                    raise ValueError('Invalid stars encountered despite prerequisite quality audit')
                stars.append(value)
                total += value
                maximum = max(maximum, value)
                squares += value * value
                for index, threshold in enumerate(thresholds):
                    if value < threshold:
                        break
                    retained_counts[index] += 1
                    retained_stars[index] += value
                current = next(repositories, None)
            stars.sort(reverse=True)
            h = 0
            for position, value in enumerate(stars, 1):
                if value < position:
                    break
                h = position
            yield {'user_id': identifier, 'github_id': user['github_id'],
                   'retrieval_status': user['retrieval_status'],
                   'n_repositories': len(stars), 'total_stars': total,
                   'max_stars': maximum, 'sum_squared_stars': squares,
                   'h_observed': h, 'h_stored': user['h_index'],
                   'thresholds': {str(t): {'repositories': retained_counts[i], 'stars': retained_stars[i]}
                                  for i,t in enumerate(thresholds)}}
        if current is not None:
            raise ValueError('Repository rows remain after the last source account')
    finally:
        conn.close()
