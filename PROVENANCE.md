# Provenance and checks

This file records what is known about how GitHub Stars in Context was
collected, what was checked, and what remains unknown. `release.json` in the
data repository holds the same facts in machine-readable form.

## Release

| Item | Value |
| --- | --- |
| Data | <https://huggingface.co/datasets/NaiveNeuron/github-stars-in-context>, tag `v1.1.0`, CC BY-NC 4.0 |
| Code | this repository, tag `v1.1.1`, MIT |
| Accounts | 3,671,497 |
| Repository rows | 53,990,808 (one row per repository per account) |
| Source database SHA-256 | `6feb097dfbcebbdbde2ba3bab24b65be7565b0eac6735dd28b7b86a1a31743c8` |
| Exported with | `scripts/export_release.py` (see `release/PROVIDER-REBUILD.md`) |

## Collection stages

| Stage | When | Basis for the date |
| --- | --- | --- |
| Account discovery | 8–9 December 2025 | Insertion timestamps stored with each account |
| Repository retrieval | December 2025 to early January 2026 (estimate) | The collector author's estimate. The latest stored repository creation date is 2 January 2026 |
| Profile fields | 28 January to 2 February 2026 | Stored update timestamps |
| External links (not released) | 4–9 March 2026 | Stored parse timestamps |

Per-request times were not stored and run logs were not kept. The repository
interval is an estimate. The latest stored repository creation date shows
that retrieval was still running on 2 January 2026. It does not show when
retrieval ended.

## Collector revisions

The collector in `collector/` is a snapshot of revision `fe2d719`
(17 May 2026). Collection ran on earlier revisions, which the author later
refactored. He reports that he also added columns to the existing database
by hand at that time. The records that remain do not identify every manual
change. We compared the revisions committed during collection with the
published one. The comparison describes source code. It does not show which
revision or command ran for each account.

| Revision | Date | Finding |
| --- | --- | --- |
| `503b3d2` | 2025-12-08 | Same repository query: `ownerAffiliations: OWNER`, `privacy: PUBLIC`, `isFork: false`, pagination until `hasNextPage` is false. A failed fetch stopped the run without marking the account |
| `1d13b40` | 2025-12-10 | Failure marker added: a failed fetch sets `repositories_fetched = 1` and `last_updated = 6200-01-01` |
| `2939eb8` | 2025-12-12 | Rate-limit errors are retried automatically |
| `c6d647f` | 2025-12-27 | Parallel fetching in place, with a temporary `-1` flag for accounts in progress |
| `fe2d719` | 2026-05-17 | Same query, failure marker, row-deletion rule and follower ranges as above |

The follower ranges for discovery (1–9, 10–99, 100–999, 1,000–9,999 and
10,000 or more) are the same in every revision. No revision searches for
accounts with zero followers.

According to the collector's author, stages ran in the order discovery,
repositories, profiles, links. He reset and re-ran fetches only after fixing
parser faults.

## Retrieval classes

`users.retrieval_status` is derived from the stored flags using the logic
above:

| Class | Rule | Accounts |
| --- | --- | ---: |
| `complete` | `repositories_fetched = 1` and not classified as failed | 3,615,479 |
| `failed` | `repositories_fetched = 1`, `last_updated` equal to the failure marker and no stored biography value | 56,012 |
| `unknown` | `repositories_fetched = -1` | 6 |

Of the complete accounts, 372,821 have no stored repository row.

These classes describe what the collector recorded. They were not checked
against GitHub at the time. One known gap: the collector deletes obsolete
rows only when a refresh returns at least one repository, so a successful
empty refresh can leave older rows in place.

## Checks

- **Structure.** The audit found no duplicate keys, repository rows without
  an account, negative star counts or missing required values
- **h-index.** Recomputing the h-index from repository rows matches the
  stored value for all 3,671,497 accounts. This checks arithmetic, not
  coverage
- **Parquet release.** `make parquet-check` reproduces 269 saved values from
  the public files
- **Profile fields.** 1,151,092 accounts have `profile_available = false`.
  The rule masks NULL biographies and profiles with only default values,
  which can include genuinely empty profiles. On 10 November 2026 we looked
  up a seeded random sample of 300 such accounts of the complete class on
  GitHub by numeric ID: 298 resolved and 296 had at least one follower. Of
  300 accounts with profile fields, 299 resolved. This supports treating the
  stored zeros as missing. It does not show the values of January 2026 or
  why they are missing. The check is `scripts/check_account_existence.py` and
  its result is `expected/profile-availability-check.json`

## What is not known

- How many accounts were missed, and how they are distributed over follower
  counts. Search cells that still exceeded 1,000 results on a single
  registration day were skipped. A failed search request was treated as an
  empty cell, and a failure on a later page stopped that cell's pagination.
  Neither kind of loss was logged. The collector's author recalls that
  skipped cells were mostly among accounts with one to four followers, which
  cannot be checked
- The exact time each account's repositories were fetched
- Whether an account is a person, a bot or a shared account. The collector
  keeps only `User` results of the search
- Twenty repository IDs appear under more than one account
