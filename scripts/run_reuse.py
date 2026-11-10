"""Run the approved R1 and R2 analyses in one immutable source pass.

Writes aggregate JSON and small figures only. No account-level export or
paper text is produced. Publication eligibility remains a separate decision.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
from pathlib import Path
import time

from release_common import Context, ROOT, parser, sha256
from reuse_stream import DEFAULT_THRESHOLDS, iter_summaries, validate_quality


def load_example(filename, name):
    path = ROOT / 'examples' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    cli = parser(__doc__)
    cli.add_argument('--quality-evidence', type=Path)
    cli.add_argument('--macro-medians', action='store_true')
    cli.add_argument('--no-plots', action='store_true')
    args = cli.parse_args()
    ctx = Context(args)
    quality = validate_quality(ctx, args.quality_evidence)
    modules = {'R1': load_example('03_thresholds.py', 'threshold_reuse'),
               'R2': load_example('04_portfolios.py', 'portfolio_reuse')}
    ctx.provenance['analysis_scripts_sha256'] = {
        str(Path(module.__file__).relative_to(ROOT)): sha256(module.__file__) for module in modules.values()}
    ctx.provenance['analysis_specification'] = {
        'thresholds': list(DEFAULT_THRESHOLDS),
        'top_k': list(modules['R2'].TOP_K),
        'source': 'Versioned analysis code and parameters; no local supervisor files required'}
    thresholds = modules['R1'].Accumulator(macro_medians=args.macro_medians)
    portfolios = modules['R2'].Accumulator()
    started = time.monotonic()
    statuses = Counter()
    accounts = rows = 0
    for summary in iter_summaries(ctx, DEFAULT_THRESHOLDS):
        if summary['h_observed'] != summary['h_stored']:
            raise ValueError('Streamed h-index differs from stored h despite prerequisite certificate')
        statuses[summary['retrieval_status']] += 1
        accounts += 1
        rows += summary['n_repositories']
        thresholds.consume(summary)
        portfolios.consume(summary)
        if accounts % 250_000 == 0:
            print(f'Processed {accounts:,} accounts / {rows:,} repository rows in {time.monotonic()-started:.1f}s', flush=True)
    if dict(statuses) != {key: value for key,value in quality['retrieval_classes'].items() if value}:
        raise ValueError('Streamed account classes differ from accepted source audit')
    r1, r2 = thresholds.finalize(), portfolios.finalize()
    del thresholds, portfolios
    if r1['baseline']['accounts'] != r2['cohort']['complete_code_inferred_accounts']:
        raise ValueError('R1 and R2 population denominators differ')
    for result in (r1,r2):
        result['quality_evidence'] = quality
        result['collector_semantics'] = ctx.manifest.get('collector_semantics')
        result['shared_stream'] = {'accounts': accounts, 'account_linked_repository_rows': rows,
                                  'h_stored_mismatches': 0,
                                  'retrieval_class_counts': dict(statuses),
                                  'data_pass_seconds': time.monotonic()-started}
    # Persist the aggregates before plotting so a figure failure cannot lose
    # the completed immutable data pass. Final writes add artifact hashes.
    ctx.write_json('r1-thresholds.json', r1)
    ctx.write_json('r2-portfolios.json', r2)
    if not args.no_plots:
        for key,result,prefix in [('R1',r1,'r1-thresholds'),('R2',r2,'r2-portfolios')]:
            modules[key].plot(ctx,result)
            result['figure_files'] = [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
                                      for path in [ctx.out_dir/f'{prefix}.pdf',ctx.out_dir/f'{prefix}.png']]
    ctx.write_json('r1-thresholds.json',r1)
    ctx.write_json('r2-portfolios.json',r2)
    ctx.write_json('r1-r2-run.json', {'accounts_streamed':accounts, 'repository_rows_streamed':rows,
        'seconds':time.monotonic()-started, 'publication_text_written':False,
        'full_export_run':False, 'plots_generated':not args.no_plots,
        'results':{name:sha256(ctx.out_dir/name) for name in ('r1-thresholds.json','r2-portfolios.json')}})


if __name__ == '__main__':
    main()
