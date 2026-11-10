"""Quantify schema quality, observed scores and evidence-backed retrieval classes."""
import json
from pathlib import Path
from collections import Counter
from functools import lru_cache
from datetime import datetime, timezone

from release_common import CLASSES, CORE_COLUMNS, Context, is_count, parser, quote, sha256


def duplicates(conn, table, columns):
    keys = ', '.join(quote(c) for c in columns)
    present = ' AND '.join(f'{quote(c)} IS NOT NULL' for c in columns)
    counts = conn.execute(f'SELECT COUNT(*) n FROM {quote(table)} WHERE {present} GROUP BY {keys} HAVING COUNT(*)>1')
    groups = rows = excess = 0
    for (n,) in counts:
        groups += 1
        rows += n
        excess += n - 1
    return {"groups": groups, "rows_in_groups": rows, "excess_rows": excess}


@lru_cache(maxsize=8192)
def parse_timestamp(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return 'missing', None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return 'invalid', None
    return ('timezone_unspecified' if parsed.tzinfo is None else 'parsed_utc'), parsed


def timestamps(conn, table, column, semantics, candidate_sentinels=()):
    # Stream one column and bounded-cache parsed values. A GROUP BY on raw
    # timestamps could sort the entire repository table on a full disk.
    result = {'rows': 0, 'missing': 0, 'invalid': 0, 'timezone_unspecified': 0,
              'parsed_utc': 0, 'min_utc': None, 'max_utc': None,
              'raw_min': None, 'raw_max': None, 'semantics': semantics,
              'raw_calendar_daily_counts': {}, 'future_calendar_rows': 0,
              'candidate_sentinel_counts': {prefix: 0 for prefix in candidate_sentinels}}
    lo = hi = None
    daily = Counter()
    this_year = datetime.now(timezone.utc).year
    for (value,) in conn.execute(f'SELECT {quote(column)} FROM {quote(table)}'):
        result['rows'] += 1
        kind, parsed = parse_timestamp(value)
        result[kind] += 1
        if kind == 'missing':
            continue
        raw = str(value)
        result['raw_min'] = raw if result['raw_min'] is None else min(result['raw_min'], raw)
        result['raw_max'] = raw if result['raw_max'] is None else max(result['raw_max'], raw)
        for prefix in candidate_sentinels:
            result['candidate_sentinel_counts'][prefix] += raw.startswith(prefix)
        if parsed is None:
            continue
        daily[parsed.date().isoformat()] += 1
        result['future_calendar_rows'] += parsed.year > this_year
        if kind == 'parsed_utc':
            utc = parsed.astimezone(timezone.utc)
            lo = utc if lo is None else min(lo, utc)
            hi = utc if hi is None else max(hi, utc)
    result['min_utc'] = lo.isoformat() if lo is not None else None
    result['max_utc'] = hi.isoformat() if hi is not None else None
    result['raw_calendar_daily_counts'] = dict(sorted(daily.items()))
    result['future_year_threshold'] = this_year
    result['daily_count_semantics'] = 'Calendar date as recorded; no timezone or collection-stage meaning inferred'
    return result


def missing_cells(conn, table, columns):
    expressions = ['COUNT(*)']
    for col in columns:
        field = quote(col['name'])
        expressions.extend([f'SUM(CASE WHEN {field} IS NULL THEN 1 ELSE 0 END)',
                            f"SUM(CASE WHEN typeof({field})='text' AND trim({field})='' THEN 1 ELSE 0 END)"])
    row = conn.execute(f"SELECT {', '.join(expressions)} FROM {quote(table)}").fetchone()
    return row[0], [(row[1+2*i] or 0, row[2+2*i] or 0) for i in range(len(columns))]


def required_column(ctx, table, column):
    name = column['name']
    if table == 'users' and ctx._has_release_field('profile_available'):
        if name == 'followers_count':
            # This is deliberately nullable in the minimized analysis view.
            return False
        if name == 'followers_count_raw':
            return True
    return bool(column['notnull'] or column['pk'] or name in CORE_COLUMNS.get(table, []))


def verify_stored_h(conn, ctx):
    """Exact h certificate: >=h repositories reach h, but <=h reach h+1."""
    classification, parameters = ctx.classification_sql('u')
    certificates = f'''SELECT u.id, u.h_index, {classification} AS retrieval_class,
        SUM(CASE WHEN r.user_id IS NOT NULL AND r.stars>=u.h_index THEN 1 ELSE 0 END) AS at_h,
        SUM(CASE WHEN r.user_id IS NOT NULL AND r.stars>u.h_index THEN 1 ELSE 0 END) AS above_h,
        SUM(CASE WHEN r.user_id IS NOT NULL AND
            (typeof(r.stars)!='integer' OR r.stars<0 OR r.stars IS NULL) THEN 1 ELSE 0 END) AS invalid_stars
        FROM users u LEFT JOIN repositories r ON r.user_id=u.id GROUP BY u.id'''
    valid = "typeof(h_index)='integer' AND h_index>=0 AND invalid_stars=0"
    agrees = 'at_h>=h_index AND above_h<=h_index'
    query = f'''WITH certificates AS ({certificates}) SELECT COUNT(*),
        SUM(CASE WHEN {valid} THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND {agrees} THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND NOT ({agrees}) THEN 1 ELSE 0 END),
        SUM(CASE WHEN typeof(h_index)!='integer' OR h_index<0 OR h_index IS NULL THEN 1 ELSE 0 END),
        SUM(CASE WHEN invalid_stars>0 THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND retrieval_class='complete' THEN 1 ELSE 0 END),
        SUM(CASE WHEN {valid} AND retrieval_class='complete' AND {agrees} THEN 1 ELSE 0 END)
        FROM certificates'''
    values = conn.execute(query, parameters).fetchone()
    keys = ('population', 'compared', 'agree', 'disagree', 'invalid_or_missing_stored',
            'invalid_portfolio', 'code_inferred_complete_compared', 'code_inferred_complete_agree')
    result = dict(zip(keys, (v or 0 for v in values)))
    result.update({'available': True, 'method': 'exact_two_threshold_certificate',
                   'equivalence': 'h_observed=h_stored iff at least h_stored repository rows have stars>=h_stored and at most h_stored have stars>=h_stored+1; requires valid nonnegative integer counts',
                   'independent_of_supervisor_script': True,
                   'preconditions': 'Unique account ids; certificate counts the stored repository rows, not independent repository coverage',
                   'complete_counter_scope': 'Declared complete labels, whether collector-inferred or explicit sidecar',
                   'eligible_compared': None, 'eligible_agree': None,
                   'eligibility_note': 'Source diagnostics only; code-inferred completion is not publication eligibility'})
    return result


def apply_collector_semantics(ctx, conn, result, verify_h=False):
    ledger = ctx._has_release_field('retrieval_status')
    if not ctx.collector_policy_active and not ledger:
        if verify_h:
            result['h_agreement'] = verify_stored_h(conn, ctx)
        return result
    from release_common import FAILED_FETCH_SENTINEL
    classification, params = ctx.classification_sql('u')
    classes = {key: 0 for key in CLASSES}
    presence = {key: {'with_repository_rows': 0, 'without_repository_rows': 0} for key in CLASSES}
    query = (f'SELECT cls, has_rows, COUNT(*) FROM (SELECT {classification} AS cls, '
             'EXISTS(SELECT 1 FROM repositories r WHERE r.user_id=u.id) AS has_rows FROM users u) GROUP BY cls,has_rows')
    for label, has_rows, n in conn.execute(query, params):
        classes[label] += n
        presence[label]['with_repository_rows' if has_rows else 'without_repository_rows'] += n
    missing = ctx.profile_missing_sql('u')
    signature = ctx.profile_default_signature_sql('u')
    if ctx._has_release_field('bio_was_null'):
        bio_null_sql = 'u.bio_was_null'
    else:
        bio_null_sql = 'u.bio IS NULL' if 'bio' in ctx.user_columns else 'NULL'
    raw_followers = 'u.followers_count_raw' if ctx._has_release_field('followers_count_raw') else 'u.followers_count'
    profile_rows = []
    profile_sql = (f'SELECT u.last_updated=?, {bio_null_sql}, {signature} AS default_signature, '
                   f'{missing} AS profile_missing, COUNT(*), SUM({raw_followers}=0) '
                   'FROM users u GROUP BY 1,2,3,4')
    for sentinel, bio_null, default_signature, is_missing, n, zero_f in conn.execute(profile_sql, (FAILED_FETCH_SENTINEL,)):
        profile_rows.append({'sentinel': bool(sentinel), 'bio_null': bool(bio_null) if bio_null is not None else None,
                             'all_default_signature': bool(default_signature), 'profile_fields_missing': bool(is_missing),
                             'accounts': n, 'zero_raw_followers': zero_f})
    joint = [{'raw_flag': flag, 'sentinel': bool(sentinel),
              'bio_null': bool(bio_null) if bio_null is not None else None, 'accounts': n}
             for flag,sentinel,bio_null,n in conn.execute(
                 f'SELECT u.repositories_fetched,u.last_updated=?,{bio_null_sql},COUNT(*) FROM users u GROUP BY 1,2,3',
                 (FAILED_FETCH_SENTINEL,))]
    result.pop('completeness_established', None)
    result.pop('timestamp_semantics_established', None)
    result.update({
        'retrieval_classes': classes,
        'accounts_by_retrieval_class_and_repository_presence': presence,
        'retrieval_class_inference_basis': ('Persisted released account classification ledger; not independent rowset certification' if ledger
                                           else 'Revision-pinned collector state inference; not independent rowset certification'),
        'collector_semantics': ctx.manifest.get('collector_semantics'),
        'persisted_classification_ledger_used': ledger,
        'record_level_retrieval_evidence_available': bool(ctx.statuses),
        'accounts_with_record_level_retrieval_evidence': len(ctx.statuses),
        'accounts_with_non_unknown_class': result['accounts']-classes['unknown'],
        'independent_rowset_certification': False,
        'flag_sentinel_bio_joint_counts': joint,
        'profile_quality': {
            'joint_counts': profile_rows,
            'non_sentinel_all_default_signature': sum(r['accounts'] for r in profile_rows if not r['sentinel'] and r['all_default_signature']),
            'sentinel_all_default_signature': sum(r['accounts'] for r in profile_rows if r['sentinel'] and r['all_default_signature']),
            'all_default_signature': sum(r['accounts'] for r in profile_rows if r['all_default_signature']),
            'bio_null_unprocessed_or_unavailable': sum(r['accounts'] for r in profile_rows if r['bio_null']) if bio_null_sql != 'NULL' else None,
            'bio_null_indicator_available': bio_null_sql != 'NULL',
            'accounts_with_profile_fields_masked': sum(r['accounts'] for r in profile_rows if r['profile_fields_missing']),
            'accounts_with_profile_fields_retained': sum(r['accounts'] for r in profile_rows if not r['profile_fields_missing']),
            'policy': 'Mask unprocessed bio-NULL and ambiguous all-default profiles; keep raw values unchanged. This signature can also match a genuine empty profile, so it is conservative missingness inference, not proof of a specific API failure',
            'sponsors_count': ('Hardcoded zero in fetch_user_profile.py:65; not an observed sponsorship count' if ctx.collector_policy_active else 'Not included in the minimized export'),
        },
        'timestamp_field_write_paths_established': bool(ctx.collector_policy_active),
        'repository_observation_window_established': False,
        'timestamp_semantics_basis': ('Pinned collector write paths, conditional on provider-identified execution history; no repository observation timestamp available' if ctx.collector_policy_active else 'Supplied export timestamp semantics; no collection interval inferred from omitted profile fields'),
        'observation_window': ctx.manifest.get('observation_window'),
    })
    if ledger:
        result['audit_scope'] = 'Released minimized export diagnostics; classification and profile availability use persisted ledgers'
    for field, description in ctx.manifest.get('timestamp_semantics', {}).items():
        if field in result.get('timestamps', {}):
            result['timestamps'][field]['semantics'] = description
    if verify_h:
        result['h_agreement'] = verify_stored_h(conn, ctx)
    return result


def main():
    cli = parser(__doc__)
    cli.add_argument('--skip-h-agreement', action='store_true',
                     help='Leave recomputation pending; useful when a separate recomputation is in progress')
    cli.add_argument('--integrity-mode', choices=('full', 'quick'), default='quick',
                     help='quick_check avoids full index-content verification; reported explicitly')
    cli.add_argument('--timestamp-sentinel', action='append', default=[], metavar='TABLE.COLUMN=PREFIX',
                     help='Count a candidate sentinel without assigning its semantic meaning')
    cli.add_argument('--reuse-structural-audit', type=Path, help='Reuse physical checks only after matching the immutable input checksum')
    cli.add_argument('--verify-stored-h', action='store_true', help='Independently verify stored h using exact threshold certificates')
    args = cli.parse_args()
    sentinel_prefixes = {}
    for item in args.timestamp_sentinel:
        field, prefix = item.split('=', 1)
        sentinel_prefixes.setdefault(field, []).append(prefix)
    ctx = Context(args)
    with ctx.connect() as conn:
        if args.reuse_structural_audit:
            saved = json.loads(args.reuse_structural_audit.read_text())
            if saved['provenance']['input_sha256'] != ctx.provenance['input_sha256']:
                raise ValueError('Structural audit belongs to a different input DB')
            result = saved['results']
            # Validation and eligibility depend on policy/sidecars, not only
            # the DB bytes. Never carry them over from a physical-only audit.
            result['validation'] = {'available': False, 'sample_accounts': None, 'agree': None,
                                    'details': [], 'reason': 'Independent validation not rerun when reusing structural checks'}
            result['h_agreement'] = {'available': False, 'compared': None, 'agree': None, 'disagree': None,
                                     'eligible_compared': None, 'eligible_agree': None,
                                     'reason': 'A fresh h check was not requested'}
            result['portfolios'] = {'eligible': None, 'valid_portfolio': None,
                                     'empty_observed': result.get('empty_observed_accounts')}
            result = apply_collector_semantics(ctx, conn, result, args.verify_stored_h)
            result['reused_structural_audit'] = {
                'file': args.reuse_structural_audit.name,
                'sha256': sha256(args.reuse_structural_audit),
                'original_provenance': saved['provenance'],
                'provenance_caveat': 'Original helper hash was read at output time while the helper file was being adapted. The exact executed physical-audit script is preserved in quality-structural-script.json; the DB hash and immutable access were verified'}
            result['parameters'] = {**result.get('parameters', {}), 'verify_stored_h': args.verify_stored_h,
                                    'reuse_structural_audit': args.reuse_structural_audit.name}
            result['audit_scope'] = 'Unreleased source diagnostics only; no export or manuscript rendering'
            ctx.write_json('quality.json', result)
            return
        count = lambda sql: conn.execute(sql).fetchone()[0]
        n_users, n_repos = count('SELECT COUNT(*) FROM users'), count('SELECT COUNT(*) FROM repositories')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        duplicate_checks = {}
        for table, columns in [('users',['id']),('users',['github_id']),('users',['github_name']),
                                ('repositories',['id']),('repositories',['user_id','repository_id'])]:
            duplicate_checks[table + '.' + '+'.join(columns)] = duplicates(conn, table, columns)
        if 'user_links' in tables:
            duplicate_checks['user_links.user_id+url'] = duplicates(conn, 'user_links', ['user_id','url'])
        orphan_counts = {'repositories': count('SELECT COUNT(*) FROM repositories r LEFT JOIN users u ON u.id=r.user_id WHERE u.id IS NULL')}
        if 'user_links' in tables:
            orphan_counts['user_links'] = count('SELECT COUNT(*) FROM user_links r LEFT JOIN users u ON u.id=r.user_id WHERE u.id IS NULL')
        missingness, time_ranges = {}, {}
        missing_required = required_cells = 0
        semantics = ctx.manifest.get('timestamp_semantics', {})
        for table in ('users','repositories','user_links'):
            if table not in tables:
                continue
            columns = conn.execute(f'PRAGMA table_info({quote(table)})').fetchall()
            n, cells = missing_cells(conn, table, columns)
            missingness[table] = {}
            for col, (nulls, blank) in zip(columns, cells):
                name = col['name']
                required = required_column(ctx, table, col)
                missingness[table][name] = {'null': nulls, 'blank': blank, 'denominator': n, 'required': required}
                if required:
                    missing_required += nulls + blank
                    required_cells += n
                if name.endswith('_at') or name == 'last_updated':
                    field = table + '.' + name
                    time_ranges[field] = timestamps(conn, table, name, semantics.get(field, 'Unresolved'),
                                                    sentinel_prefixes.get(field, ()))
        invalid_stars = count("SELECT COUNT(*) FROM repositories WHERE stars IS NULL OR typeof(stars)!='integer' OR stars<0")
        cross_owner_ids = count('SELECT COUNT(*) FROM (SELECT repository_id FROM repositories WHERE repository_id IS NOT NULL GROUP BY repository_id HAVING COUNT(DISTINCT user_id)>1)')
        classes = Counter({k: 0 for k in CLASSES})
        agreement = {'compared': 0, 'agree': 0, 'disagree': 0, 'invalid_or_missing_stored': 0,
                     'invalid_portfolio': 0, 'eligible_compared': 0, 'eligible_agree': 0}
        observed = Counter()
        validation_rows = {int(r['user_id']): r for r in ctx.validation}
        if len(validation_rows) != len(ctx.validation):
            raise ValueError('validation sample has duplicate user IDs')
        validation_found, validation_agree, validation_details = set(), 0, []
        portfolio_rows = () if args.skip_h_agreement else ctx.iter_portfolios(conn)
        for row in portfolio_rows:
            classes[row['retrieval_status']] += 1
            observed['eligible'] += row['eligible']
            observed['valid_portfolio'] += row['valid_portfolio']
            observed['empty_observed'] += row['n_repositories'] == 0
            if row['h_observed'] is None:
                agreement['invalid_portfolio'] += 1
            elif not is_count(row['h_stored']):
                agreement['invalid_or_missing_stored'] += 1
            else:
                equal = row['h_observed'] == row['h_stored']
                agreement['compared'] += 1
                agreement['agree' if equal else 'disagree'] += 1
                if row['eligible']:
                    agreement['eligible_compared'] += 1
                    agreement['eligible_agree'] += equal
            if row['user_id'] in validation_rows:
                v = validation_rows[row['user_id']]
                equal = row['h_observed'] == int(v['expected_h']) and row['n_repositories'] == int(v['expected_repository_count'])
                validation_found.add(row['user_id'])
                validation_agree += equal
                validation_details.append({'user_id': row['user_id'], 'agree': equal, 'evidence_ref': v['evidence_ref']})
        if not args.skip_h_agreement and validation_rows.keys() - validation_found:
            raise ValueError('validation evidence refers to absent accounts')
        flag_classes = {}
        for flag, n in conn.execute('SELECT repositories_fetched,COUNT(*) FROM users GROUP BY repositories_fetched'):
            flag_classes[str(flag)] = n
        flags_by_presence = []
        for flag, has_rows, n in conn.execute(
                'SELECT u.repositories_fetched, EXISTS(SELECT 1 FROM repositories r WHERE r.user_id=u.id), COUNT(*) '
                'FROM users u GROUP BY 1,2'):
            flags_by_presence.append({'raw_flag': flag, 'has_repository_rows': bool(has_rows), 'accounts': n})
        empty_accounts = sum(r['accounts'] for r in flags_by_presence if not r['has_repository_rows'])
        link_cross_tabs = {}
        if any(col['name'] == 'links_parsed_at' for col in conn.execute('PRAGMA table_info(users)')):
            for prefix in sentinel_prefixes.get('users.last_updated', []):
                rows = conn.execute(
                    'SELECT substr(last_updated,1,?)=?, links_parsed_at IS NOT NULL, COUNT(*), '
                    'SUM(followers_count=0) FROM users GROUP BY 1,2', (len(prefix), prefix))
                link_cross_tabs[prefix] = [{'matches_candidate_sentinel': bool(a), 'links_parsed_present': bool(b),
                                           'accounts': n, 'zero_stored_followers': zero} for a,b,n,zero in rows]
        stored_h = {str(h): n for h,n in conn.execute('SELECT h_index,COUNT(*) FROM users GROUP BY h_index')}
        stored_h_values = [int(value) for value in stored_h if value != 'None']
        if args.skip_h_agreement:
            agreement = {key: None for key in agreement}
            agreement.update({'available': False, 'reason': 'Skipped explicitly; separate supervisor recomputation in progress'})
            classes.update({'unknown': n_users - len(ctx.statuses)})
            for status in ctx.statuses.values():
                classes[status['status']] += 1
            observed = {'eligible': None, 'valid_portfolio': None, 'empty_observed': empty_accounts}
        else:
            agreement['available'] = True
        integrity_sql = 'PRAGMA integrity_check' if args.integrity_mode == 'full' else 'PRAGMA quick_check'
        result = {
            'accounts': n_users, 'repository_rows': n_repos,
            'integrity_check': [r[0] for r in conn.execute(integrity_sql)],
            'integrity_check_mode': args.integrity_mode,
            'duplicates': duplicate_checks, 'cross_owner_repository_ids': cross_owner_ids,
            'orphans': orphan_counts, 'invalid_stars': invalid_stars,
            'missingness': missingness, 'missing_required_cells': missing_required,
            'required_cell_denominator': required_cells,
            'timestamps': time_ranges, 'observation_window': ctx.manifest.get('observation_window'),
            'retrieval_classes': dict(classes), 'retrieval_class_denominator': n_users,
            'fetched_flag_classes': flag_classes,
            'fetched_flag_by_repository_presence': flags_by_presence,
            'empty_observed_accounts': empty_accounts,
            'timestamp_link_presence_cross_tab': link_cross_tabs,
            'stored_h_summary': {'denominator': n_users, 'zero': stored_h.get('0', 0),
                                 'max': max(stored_h_values) if stored_h_values else None, 'by_value': stored_h},
            'completeness_established': bool(ctx.statuses),
            'timestamp_semantics_established': False,
            'parameters': {'skip_h_agreement': args.skip_h_agreement, 'integrity_mode': args.integrity_mode,
                           'candidate_sentinels': sentinel_prefixes},
            'fetched_flag_interpretation': 'Raw flag only; not proof of pagination completion or failure',
            'portfolios': dict(observed), 'h_agreement': agreement,
            'validation': {'available': bool(validation_rows) and not args.skip_h_agreement, 'sample_accounts': len(validation_rows) if validation_rows and not args.skip_h_agreement else None,
                           'agree': validation_agree if validation_rows and not args.skip_h_agreement else None, 'details': validation_details,
                           'reason': ('Skipped explicitly' if args.skip_h_agreement and validation_rows else None) if validation_rows else 'Independent validation evidence not supplied'},
        }
        result = apply_collector_semantics(ctx, conn, result, args.verify_stored_h)
        ctx.write_json('quality.json', result)


if __name__ == '__main__':
    main()
