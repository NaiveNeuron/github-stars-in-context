#!/usr/bin/env python3
"""Measure star-threshold retention in code-inferred complete portfolios.

All repository counts are account-linked rows, not deduplicated GitHub IDs.
The baseline includes empty and zero-star complete-class account records.
No account names or individual examples are saved. Source-audit execution is
allowed for the supervisor-authorized R1 analysis under the pinned policy.
"""
from __future__ import annotations

from array import array
from collections import Counter
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_common import Context, parser, sha256  # noqa: E402

THRESHOLDS = (1, 10, 100, 1000)


def ratio(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'share': numerator / denominator if denominator else None}


class Accumulator:
    def __init__(self, thresholds=THRESHOLDS, macro_medians=False):
        values = tuple(thresholds)
        if not values or any(type(value) is not int or value < 1 for value in values):
            raise ValueError('Thresholds must be positive integers')
        if len(set(values)) != len(values):
            raise ValueError('Thresholds must not repeat')
        self.thresholds = tuple(sorted(values))
        self.macro_medians = macro_medians
        self.classes = Counter()
        self.accounts = self.repositories = self.stars = 0
        self.cells = {
            threshold: {
                'accounts': 0, 'repositories': 0, 'stars': 0,
                'owner_baseline_repositories': 0, 'owner_baseline_stars': 0,
                'hidden_repository_fraction_sum': 0.0, 'hidden_star_fraction_sum': 0.0,
                'repository_fractions': array('d') if macro_medians else None,
                'star_fractions': array('d') if macro_medians else None,
            }
            for threshold in self.thresholds
        }

    def consume(self, row):
        self.classes[row['retrieval_status']] += 1
        if row['retrieval_status'] != 'complete':
            return
        n, stars = row['n_repositories'], row['total_stars']
        self.accounts += 1
        self.repositories += n
        self.stars += stars
        for threshold in self.thresholds:
            selected = row['thresholds'][str(threshold)]
            retained_n, retained_stars = selected['repositories'], selected['stars']
            if not retained_n:
                continue
            cell = self.cells[threshold]
            cell['accounts'] += 1
            cell['repositories'] += retained_n
            cell['stars'] += retained_stars
            cell['owner_baseline_repositories'] += n
            cell['owner_baseline_stars'] += stars
            hidden_repository_fraction = (n - retained_n) / n
            hidden_star_fraction = (stars - retained_stars) / stars
            cell['hidden_repository_fraction_sum'] += hidden_repository_fraction
            cell['hidden_star_fraction_sum'] += hidden_star_fraction
            if self.macro_medians:
                cell['repository_fractions'].append(hidden_repository_fraction)
                cell['star_fractions'].append(hidden_star_fraction)

    def finalize(self):
        thresholds = []
        for threshold in self.thresholds:
            cell = self.cells[threshold]
            owners = cell['accounts']
            row_base, star_base = cell['owner_baseline_repositories'], cell['owner_baseline_stars']
            row_hidden, star_hidden = row_base-cell['repositories'], star_base-cell['stars']
            macro = {
                'accounts': owners,
                'mean_hidden_repository_fraction': cell['hidden_repository_fraction_sum'] / owners if owners else None,
                'mean_hidden_star_fraction': cell['hidden_star_fraction_sum'] / owners if owners else None,
                'median_hidden_repository_fraction': None,
                'median_hidden_star_fraction': None,
                'median_status': 'not_requested' if not self.macro_medians else 'empty_population',
            }
            if self.macro_medians and owners:
                # Copies only compact fractions, never account records or names.
                macro['median_hidden_repository_fraction'] = float(np.median(np.asarray(cell['repository_fractions'])))
                macro['median_hidden_star_fraction'] = float(np.median(np.asarray(cell['star_fractions'])))
                macro['median_status'] = 'calculated_exactly_on_saved_double_precision_fractions'
            thresholds.append({
                'threshold': threshold,
                'retained': {
                    'accounts': ratio(owners, self.accounts),
                    'account_linked_repository_rows': ratio(cell['repositories'], self.repositories),
                    'stars': ratio(cell['stars'], self.stars),
                },
                'conditional_on_retained_owner': {
                    'accounts': owners,
                    'baseline_account_linked_repository_rows': row_base,
                    'baseline_stars': star_base,
                    'hidden_account_linked_repository_rows': ratio(row_hidden, row_base),
                    'hidden_stars': ratio(star_hidden, star_base),
                    'macro': macro,
                },
            })
        return {
            'baseline': {'accounts': self.accounts, 'account_linked_repository_rows': self.repositories,
                         'stars': self.stars},
            'input_account_records_by_retrieval_class': dict(sorted(self.classes.items())),
            'thresholds': thresholds,
            'parameters': {'thresholds': list(self.thresholds), 'macro_medians': self.macro_medians},
            'policies': {
                'population': 'Account records classified complete under the revision-pinned collector policy; all observed portfolio rows retained',
                'baseline_accounts': 'Includes empty and nonempty zero-star portfolios',
                'threshold': 'Retain each account-linked repository row with stars >= threshold, including exact equality',
                'owner_retention': 'At least one account-linked repository row reaches the threshold',
                'repository_unit': 'Account-linked repository rows; the same GitHub repository ID under multiple accounts is counted once per stored ownership row',
                'star_unit': 'Star totals summed over account-linked repository rows in the stated population',
                'conditional_micro': 'Among owners retaining at least one row: sum hidden rows or stars divided by sum baseline rows or stars for those owners',
                'conditional_macro': 'Equal owner weights: arithmetic mean and optional median of each retained owner hidden-row or hidden-star fraction',
                'zero_denominators': 'Shares and macro summaries are null when no relevant population exists',
                'sampling_scope': 'Follower-selected public non-fork repository population in the supplied snapshot, not all GitHub accounts',
                'completeness': 'Complete means code-inferred fetch completion, not independent stored-rowset certification',
                'empty_refresh': 'The pinned collector can retain older rows after an empty refresh because stale-row deletion requires nonempty seen_repo_ids',
                'identifiers': 'No names or individual account records saved in this evidence',
            },
        }


def plot(ctx, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    records = result['thresholds']
    x = [row['threshold'] for row in records]
    fig, axes = plt.subplots(2, 1, figsize=(3.5, 3.8), sharex=True)
    baseline = result['baseline']
    styles = [('account_linked_repository_rows', 'Repository rows', 'o'),
              ('stars', 'Stars', 's'), ('accounts', 'Accounts', '^')]
    for key, label, marker in styles:
        axes[0].plot(x, [row['retained'][key]['share'] for row in records], marker=marker,
                     linewidth=1.2, markersize=3, label=label)
    for key, label, marker in (
            ('hidden_account_linked_repository_rows', 'Rows / retained-owner baseline rows', 'o'),
            ('hidden_stars', 'Stars / retained-owner baseline stars', 's')):
        axes[1].plot(x, [row['conditional_on_retained_owner'][key]['share'] for row in records],
                     marker=marker, linewidth=1.2, markersize=3, label=label)
    axes[0].set_title('All complete-class owners', fontsize=8)
    axes[1].set_title('Owners with at least one retained repository', fontsize=8)
    for axis in axes:
        axis.set_xscale('log')
        axis.set_xticks(x, labels=[str(value) for value in x])
        axis.set_ylim(-0.025, 1.025)
        axis.set_yticks([0, 0.5, 1])
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.tick_params(labelsize=7)
        axis.grid(alpha=0.2)
        axis.legend(fontsize=7, loc='best', frameon=False)
    axes[0].set_ylabel('Retained fraction', fontsize=7)
    axes[1].set_ylabel('Hidden fraction', fontsize=7)
    axes[1].set_xlabel('Minimum repository stars (inclusive)', fontsize=7)
    fig.subplots_adjust(left=0.17, right=0.98, top=0.88, bottom=0.29, hspace=0.65)
    source = 'source audit' if ctx.source_audit else 'released export'
    fig.suptitle('Star-threshold retention', fontsize=9)
    footer = (
        f"Baseline accounts: {baseline['accounts']:,}; repository rows: {baseline['account_linked_repository_rows']:,}\n"
        + f"Baseline stars: {baseline['stars']:,}\n"
        + 'Complete class: code inference, not independent certification\n'
        + f"Input: {source}; follower-selected public non-fork population"
    )
    fig.text(0.04, 0.025, footer, fontsize=6.5, va='bottom', linespacing=1.3)
    if ctx.provenance.get('data_kind') == 'synthetic':
        fig.text(0.5, 0.5, 'SYNTHETIC TEST DATA', fontsize=14, color='crimson', alpha=0.3,
                 rotation=22, ha='center', va='center')
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for extension in ('pdf', 'png'):
        path = ctx.out_dir / f'r1-thresholds.{extension}'
        fig.savefig(path, dpi=180)
        files.append({'path': path.name, 'sha256': sha256(path), 'bytes': path.stat().st_size})
    plt.close(fig)
    result['plot'] = {'files': files, 'panels': ['retained shares of the complete-class baseline',
                                              'conditional hidden shares among retained owners'],
                      'conditional_weighting': 'micro; macro results are saved in JSON'}
    return result['plot']


def run(ctx, quality_evidence=None, macro_medians=False):
    from reuse_stream import iter_summaries, validate_quality

    if not (ctx.source_audit and ctx.collector_policy_active):
        ctx.require_release()
    audit = validate_quality(ctx, quality_evidence or ctx.out_dir / 'quality.json')
    accumulator = Accumulator(macro_medians=macro_medians)
    for row in iter_summaries(ctx, thresholds=THRESHOLDS):
        accumulator.consume(row)
    result = accumulator.finalize()
    result['quality_evidence'] = audit
    result['collector_semantics'] = ctx.manifest.get('collector_semantics')
    plot(ctx, result)
    ctx.write_json('r1-thresholds.json', result)
    return result


def main():
    cli = parser(__doc__)
    cli.add_argument('--quality-evidence', type=Path)
    cli.add_argument('--macro-medians', action='store_true',
                     help='Save exact medians using compact per-owner fraction buffers')
    args = cli.parse_args()
    run(Context(args), args.quality_evidence, args.macro_medians)


if __name__ == '__main__':
    main()
