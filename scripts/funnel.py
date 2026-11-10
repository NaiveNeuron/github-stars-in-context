"""Count source and release populations under a declared retrieval policy."""
from collections import Counter

from release_common import CLASSES, Context, parser


def run(ctx):
    with ctx.connect() as conn:
        scalar = lambda sql: conn.execute(sql).fetchone()[0]
        users = scalar('SELECT COUNT(*) FROM users')
        repos = scalar('SELECT COUNT(*) FROM repositories')
        flags = {str(flag): n for flag, n in conn.execute(
            'SELECT repositories_fetched,COUNT(*) FROM users GROUP BY repositories_fetched')}
        classification, parameters = ctx.classification_sql('u')
        classes = Counter({key: 0 for key in CLASSES})
        presence = {key: {'with_repository_rows': 0, 'without_repository_rows': 0}
                    for key in CLASSES}
        query = (
            'SELECT retrieval_class, has_rows, COUNT(*) FROM ('
            f'SELECT {classification} AS retrieval_class, '
            'EXISTS(SELECT 1 FROM repositories r WHERE r.user_id=u.id) AS has_rows '
            'FROM users u) GROUP BY retrieval_class, has_rows')
        for status, has_rows, n in conn.execute(query, parameters):
            if status not in classes:
                raise ValueError(f'Unexpected retrieval classification {status!r}')
            classes[status] += n
            key = 'with_repository_rows' if has_rows else 'without_repository_rows'
            presence[status][key] += n
        with_rows = sum(counts['with_repository_rows'] for counts in presence.values())
        empty_by_flag = {str(flag): n for flag, n in conn.execute(
            'SELECT u.repositories_fetched,COUNT(*) FROM users u WHERE NOT EXISTS '
            '(SELECT 1 FROM repositories r WHERE r.user_id=u.id) GROUP BY u.repositories_fetched')}
        released = ctx.provenance.get('input_role') == 'released_export'
        # A source audit must not silently duplicate the separate h-index
        # recomputation. SQL classification/presence counts need no portfolio sort.
        eligible = sum(row['eligible'] for row in ctx.iter_portfolios(conn)) if released else None
        frame = ctx.manifest.get('account_frame', {})
        discovered = frame.get('discovered_accounts') if frame.get('evidence_ref') else None
        if discovered is not None and (type(discovered) is not int or discovered < users):
            raise ValueError('documented discovered_accounts must include all source accounts')
        collector = ctx.manifest.get('collector_semantics')
        labels_available = bool(ctx.statuses) or bool(collector)
        result = {
            'discovered_accounts': discovered, 'source_accounts': users,
            'discovery_exclusions': discovered-users if discovered is not None else None,
            'discovery_evidence_ref': frame.get('evidence_ref'),
            'fetched_flag_accounts': flags.get('1', 0), 'raw_flag_counts': flags,
            'accounts_with_repository_rows': with_rows,
            'accounts_without_repository_rows': users-with_rows,
            'accounts_without_rows_by_raw_flag': empty_by_flag,
            'accounts_by_retrieval_class_and_repository_presence': presence,
            'repository_rows': repos, 'retrieval_classes': dict(classes),
            'retrieval_class_interpretation': (
                'Explicit sidecar labels override revision-pinned collector-code inference. '
                'Under the collector policy, complete denotes inferred fetch completion, '
                'not independent certification of the stored repository rowset'),
            'retrieval_labels_available': labels_available,
            'record_level_retrieval_evidence_available': bool(ctx.statuses),
            'accounts_with_record_level_retrieval_evidence': len(ctx.statuses),
            'accounts_with_non_unknown_class': users-classes['unknown'],
            'collector_semantics': collector,
            'independent_rowset_certification': False,
            'complete_accounts': classes['complete'], 'other_accounts': users-classes['complete'],
            'eligible_accounts': eligible,
            'eligibility_status': 'computed_on_released_export' if released else 'unresolved_source_audit',
            'released_accounts': users if released else None,
            'released_repository_rows': repos if released else None,
            'release_scope': ctx.manifest.get('release_scope'),
            'notes': [
                'Source account rows are not a verified complete discovery frame',
                'Raw negative flags remain separate and are unknown unless explicit evidence establishes their meaning',
                'Zero joined rows are not by themselves evidence of a verified empty portfolio',
                'The pinned collector deletes stale repository rows only when seen_repo_ids is nonempty. '
                'A completed refresh with an empty result can therefore retain older rows',
                'Repository created_at is GraphQL repository creation time, not fetch completion time',
                'No release counts or portfolio eligibility are asserted for an unreleased source database'],
        }
        ctx.write_json('funnel.json', result)
        return result


def main():
    run(Context(parser(__doc__).parse_args()))


if __name__ == '__main__':
    main()
