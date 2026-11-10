# GitHub Stars in Context

Processing code and reuse examples for **GitHub Stars in Context**, a dataset
linking 3,671,497 GitHub user accounts to the 53,990,808 public, non-fork
repositories they own, with star counts.

- **Data:** <https://huggingface.co/datasets/NaiveNeuron/github-stars-in-context>
  (three Parquet tables, 1.6 GB, CC BY-NC 4.0)
- **Code:** this repository (MIT)

The dataset card describes the files, the collection and the limits of the
data. This repository lets you check the release and reproduce the reuse
examples from the paper.

## Reproduce the examples

You need CPython 3.12 and about 2 GB of disk. After the download and the
package install, no network access or GitHub token is needed.

```sh
git clone https://github.com/NaiveNeuron/github-stars-in-context
cd github-stars-in-context

python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-parquet.lock

mkdir -p data
base=https://huggingface.co/datasets/NaiveNeuron/github-stars-in-context/resolve/v1.0.0
for f in users.parquet repositories.parquet portfolios.parquet analysis-manifest.json \
         data-dictionary.json data-dictionary.md crosswalk.sql PARQUET-SHA256SUMS; do
  curl -fL -o "data/$f" "$base/$f"
done
(cd data && shasum -a 256 -c PARQUET-SHA256SUMS)

make parquet-check PYTHON=.venv/bin/python
```

`requirements-parquet.lock` installs DuckDB and PyArrow at pinned,
hash-checked versions. The download needs no account or token.

`make parquet-check` verifies the Parquet checksums, runs the four SQL recipes
in `examples/recipes/` with DuckDB and compares 269 values against the saved
results in `expected/`. It ends with `PASS: 269 comparisons`. On an Apple M4
Max it takes about 20 seconds and 1.5 GB of memory.

## What the examples compute

All examples use the 3,615,479 accounts whose repository retrieval completed,
with 53,987,867 repository rows and 239,997,596 stars.

| Recipe | Question | Headline result |
| --- | --- | --- |
| `01_thresholds.sql` | What does a minimum-star filter keep? | A 10-star filter keeps 2.89% of repository rows and 90.64% of stars, and drops 88.51% of the rows of the owners it keeps |
| `02_portfolios.sql` | How concentrated are an account's stars? | Among accounts with at least 10,000 stars, the top repository holds a median 71.7% of them |
| `02b_rank_overlap.sql` | Do total stars and the h-index select the same accounts? | The top-1,000 sets (with ties: 1,125 and 1,000 accounts) share 265 |
| `03_owner_clustering.sql` | How often do selected repositories share an owner? | At 100 stars, 60.07% of selected rows belong to owners with more than one selected repository |

The h-index of an account is the largest *h* such that *h* of its
repositories have at least *h* stars each.

`examples/*.py` are the reference implementations that produced the paper's
figures and the GH Archive join. They read a SQLite build of the same tables,
which is not part of the public release because the Parquet files carry the
same rows. `expected/` holds their saved outputs. `release/PROVIDER-REBUILD.md`
documents how the Parquet files were exported from the source database with
`scripts/export_release.py`.

## Repository layout

| Path | Contents |
| --- | --- |
| `examples/recipes/` | DuckDB SQL for the four examples |
| `examples/*.py` | Reference implementations and figures |
| `scripts/` | Export, quality audit, validation and comparison code |
| `expected/` | Saved aggregate results and figures used as the comparison target |
| `evidence/comparison/` | A small capability comparison with ecosyste.ms |
| `release/` | Export manifest, recorded environment and rebuild notes |
| `collector/` | The collector that produced the source database, with its origin note |

## Limits

The accounts are a follower-selected set, not a random sample of GitHub.
Retrieval classes come from the collector's logic and are not an independent
check against GitHub. Follower counts are missing for 1,151,092 accounts.
See the dataset card for details.

## Licences

Code in this repository is released under the MIT licence (`LICENSE`). The
files under `evidence/comparison/sources/` are unmodified excerpts from
[ecosyste-ms/repos](https://github.com/ecosyste-ms/repos) and remain under
AGPL-3.0, as described in `evidence/comparison/THIRD-PARTY-NOTICES.md`. The data are
released under CC BY-NC 4.0. Manifest files written before the release may
still list the licences as pending.

## Citation

A paper describing the dataset is under review. See `CITATION.cff`.

To request removal of an account from the dataset, open an issue.
