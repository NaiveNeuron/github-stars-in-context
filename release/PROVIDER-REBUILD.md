# Rebuilding the export from the source database

You do not need this to use the dataset. The public Parquet files are the
release. This file documents how they were produced, for the authors and for
anyone who later obtains the source database.

The source database is a 9,408,057,344-byte SQLite file with SHA-256
`6feb097dfbcebbdbde2ba3bab24b65be7565b0eac6735dd28b7b86a1a31743c8`. It is not
public, because it also holds the profile fields that the release leaves out.

`manifest.json` in this directory is the configuration file that the export read
on 6 November 2026. It is kept unchanged because the saved results record
its checksum. Its licence and access fields were still empty at that time.
The current values are in `release.json` in the data repository.

## Commands

Install `requirements.lock`, set `SOURCE_DB` to the source file and run these
commands from the repository root. Every read of the source uses
`mode=ro&immutable=1`. Output goes to new directories only.

```sh
SOURCE_DB=/path/to/source.sqlite
SOURCE_SHA=6feb097dfbcebbdbde2ba3bab24b65be7565b0eac6735dd28b7b86a1a31743c8
MANIFEST=release/manifest.json
python scripts/inventory.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest "$MANIFEST" --source-audit --out-dir results/provider-source
python scripts/quality.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest "$MANIFEST" --source-audit --out-dir results/provider-source --skip-h-agreement --verify-stored-h --timestamp-sentinel users.last_updated=6200-01-01
python scripts/funnel.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest "$MANIFEST" --source-audit --out-dir results/provider-source
python scripts/export_release.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest "$MANIFEST" --source-audit --quality-evidence results/provider-source/quality.json --out-dir results/provider-export --export-dir rebuilt-pilot --sample-fraction .01 --seed 20261106
python scripts/export_release.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest "$MANIFEST" --source-audit --quality-evidence results/provider-source/quality.json --out-dir results/provider-export --export-dir rebuilt-full --sample-fraction 1 --pilot-evidence results/provider-export/export-sample.json
```

The first export keeps all repository rows of a 1% account sample. The second
checks the size projected from that sample and then exports every account and
repository row. It writes the retrieval class and the profile flag for each
account before it drops the biography, company, location and link fields.
The export also writes a SQLite file with the same rows, which is not part
of the public release.

A rebuilt export may differ byte for byte from the released files, because
Parquet and SQLite writers are not guaranteed to be deterministic across
versions. Compare the row counts, the retrieval classes and the results of
`make parquet-check` before using a rebuilt export.

The `sqlite-check`, `archive-join` and `compare-results` targets of the
Makefile run the reference implementations on that SQLite file. The public
Parquet checks do not need them.

## Starting from a new collection

We have not run this path end to end. It is described so that the steps are
clear.

A new collection observes GitHub at a new time. It will not reproduce the
released rows or the published counts.

1. In `collector/`, install the collector and set your own GitHub token in
   `.env`, as its README describes
2. Run the stages as separate commands: `users`, `repos`,
   `reset-last-updated`, then `profiles` (with
   `uv run python -m data_fetching.main <command>`)
3. Stop the collector. The database is `collector/data/db/github_hindex.db`
4. Compute the SHA-256 of that file and use it as `SOURCE_SHA` in the
   commands above, with `SOURCE_DB` set to the file. Do not reuse the
   checksum of the released source database
5. Record the collection dates yourself. The manifest holds the
   classification rules, not the dates of a new collection
