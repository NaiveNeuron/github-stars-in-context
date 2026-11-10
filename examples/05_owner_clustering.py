#!/usr/bin/env python3
"""Describe repeated ownership among star-threshold-selected repository rows.

Counts use code-inferred complete account records. Repository units are
account-linked rows, including identifiers stored under multiple accounts.
No account identifiers, split manifests, figures or leakage estimates are
written. Only positive selected-row counts are buffered for owner quantiles.
"""
from __future__ import annotations

from array import array
from collections import Counter
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_common import Context, parser  # noqa: E402

THRESHOLDS = (1, 10, 100, 1000)


def fraction(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'share': numerator / denominator if denominator else None}


class Accumulator:
    def __init__(self, thresholds=THRESHOLDS):
        values = tuple(thresholds)
        if not values or any(type(value) is not int or value < 1 for value in values):
            raise ValueError('Thresholds must be positive integers')
        if len(set(values)) != len(values):
            raise ValueError('Thresholds must not repeat')
        self.thresholds = tuple(sorted(values))
        self.classes = Counter()
        self.accounts = self.repositories = 0
        self.cells = {
            threshold: {'selected_rows': 0, 'multi_owners': 0, 'multi_owner_rows': 0,
                        'owner_counts': array('Q')}
            for threshold in self.thresholds
        }

    def consume(self, row):
        self.classes[row['retrieval_status']] += 1
        if row['retrieval_status'] != 'complete':
            return
        self.accounts += 1
        self.repositories += row['n_repositories']
        for threshold in self.thresholds:
            count = row['thresholds'][str(threshold)]['repositories']
            if not count:
                continue
            cell = self.cells[threshold]
            cell['selected_rows'] += count
            cell['owner_counts'].append(count)
            if count >= 2:
                cell['multi_owners'] += 1
                cell['multi_owner_rows'] += count

    def finalize(self):
        records = []
        names = ('min', 'q1', 'median', 'q3', 'p90', 'p95', 'p99', 'max')
        probabilities = (0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1)
        for threshold in self.thresholds:
            cell = self.cells[threshold]
            owners, selected = len(cell['owner_counts']), cell['selected_rows']
            quantiles = dict.fromkeys(names)
            if owners:
                values = np.quantile(np.asarray(cell['owner_counts']), probabilities, method='linear')
                quantiles = {name: int(value) if name in ('min', 'max') else float(value)
                             for name, value in zip(names, values)}
            records.append({
                'threshold': threshold,
                'selected_account_linked_repository_rows': selected,
                'unique_owners': owners,
                'selected_repository_rows_per_owner': {
                    'denominator_owners': owners,
                    'mean': selected / owners if owners else None,
                    'quantiles': quantiles,
                    'quantile_method': 'NumPy linear interpolation (Hyndman-Fan type 7)',
                    'undefined_reason': None if owners else 'no_retained_owners',
                },
                'owners_with_multiple_selected_rows': fraction(cell['multi_owners'], owners),
                'selected_rows_from_owners_with_multiple_selected_rows': fraction(cell['multi_owner_rows'], selected),
                'owners_with_exactly_one_selected_row': owners-cell['multi_owners'],
                'selected_rows_from_single_selected_row_owners': selected-cell['multi_owner_rows'],
            })
        return {
            'baseline': {'complete_class_accounts': self.accounts,
                         'account_linked_repository_rows': self.repositories},
            'input_account_records_by_retrieval_class': dict(sorted(self.classes.items())),
            'thresholds': records,
            'parameters': {'thresholds': list(self.thresholds),
                           'quantile_probabilities': list(probabilities)},
            'policies': {
                'population': 'Account records classified complete under the revision-pinned collector policy',
                'threshold': 'Select each account-linked repository row with stars >= threshold, including equality',
                'owner_unit': 'One source account record with at least one selected row; prerequisite quality checks require unique source account keys',
                'repository_unit': 'Account-linked rows, not globally deduplicated GitHub repository IDs. An ID stored under more than one account is counted in each stored ownership context',
                'multi_owner_definition': 'An owner contributes at least two selected rows at the same threshold. Its total unfiltered portfolio size is not the criterion',
                'clustered_repository_fraction': 'Sum selected rows from owners contributing at least two selected rows, divided by all selected rows',
                'multi_owner_fraction': 'Number of retained owners contributing at least two selected rows, divided by all retained owners',
                'distribution': 'Equal owner weights; count only positive selected-row counts. Empty, zero-star and otherwise unretained accounts remain in the baseline but not the retained-owner distribution',
                'zero_denominators': 'Shares, means and quantiles are null when no relevant population exists',
                'sampling_scope': 'Follower-selected public non-fork repository population in the supplied snapshot, not all GitHub accounts',
                'completeness': 'Complete means code-inferred fetch completion, not independent stored-rowset certification',
                'empty_refresh': 'The pinned collector can retain older rows after an empty refresh because stale-row deletion requires nonempty seen_repo_ids',
                'interpretation': 'Repeated ownership describes clustering that can inform owner-aware splitting. No benchmark train/test overlap or predictive performance is measured',
                'identifiers': 'No account names, IDs or split manifests saved in this evidence',
            },
        }


def run(ctx, quality_evidence=None):
    from reuse_stream import iter_summaries, validate_quality

    if not (ctx.source_audit and ctx.collector_policy_active):
        ctx.require_release()
    audit = validate_quality(ctx, quality_evidence or ctx.out_dir / 'quality.json')
    accumulator = Accumulator()
    for row in iter_summaries(ctx, thresholds=THRESHOLDS):
        accumulator.consume(row)
    result = accumulator.finalize()
    result['quality_evidence'] = audit
    result['collector_semantics'] = ctx.manifest.get('collector_semantics')
    ctx.write_json('r3-owner-clustering.json', result)
    return result


def main():
    cli = parser(__doc__)
    cli.add_argument('--quality-evidence', type=Path)
    args = cli.parse_args()
    run(Context(args), args.quality_evidence)


if __name__ == '__main__':
    main()
