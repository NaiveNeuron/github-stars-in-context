# Provider rebuild from the frozen source

Reviewers use the existing Parquet data. This optional recipe is for a
provider who already holds the original source DB. The bundle includes the
actual transformation source, `scripts/export_release.py`, and its dependency
closure. No collector checkout, credential, supervisor file or API call is
required to run this transformation. Nothing here recollects GitHub data.

The original source has 9,408,057,344 bytes and SHA256
`6feb097dfbcebbdbde2ba3bab24b65be7565b0eac6735dd28b7b86a1a31743c8`.
It contains private fields omitted from the minimized export and is not part
of this bundle. Use a closed, checkpointed source file. All source reads use
`mode=ro&immutable=1`. Only separate new output directories are written.

Install the full hashed `requirements.lock`. Set `SOURCE_DB` to the existing
source file, then run these commands from the extracted processing root:

```sh
SOURCE_DB=/path/to/existing/frozen-source.sqlite
SOURCE_SHA=6feb097dfbcebbdbde2ba3bab24b65be7565b0eac6735dd28b7b86a1a31743c8
python scripts/inventory.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest release/manifest.json --source-audit --out-dir results/provider-source
python scripts/quality.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest release/manifest.json --source-audit --out-dir results/provider-source --skip-h-agreement --verify-stored-h --timestamp-sentinel users.last_updated=6200-01-01
python scripts/funnel.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest release/manifest.json --source-audit --out-dir results/provider-source
python scripts/export_release.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest release/manifest.json --source-audit --quality-evidence results/provider-source/quality.json --out-dir results/provider-export --export-dir rebuilt-pilot --sample-fraction .01 --seed 20261106
python scripts/export_release.py --db "$SOURCE_DB" --expected-sha256 "$SOURCE_SHA" --manifest release/manifest.json --source-audit --quality-evidence results/provider-source/quality.json --out-dir results/provider-export --export-dir rebuilt-full --sample-fraction 1 --pilot-evidence results/provider-export/export-sample.json
```

Do not run this recipe as part of the current clean-environment smoke. It
would create another full dataset. The existing artifact has not been
re-exported during reviewer packaging.

The pilot and full export are separate invocations. The pilot keeps all
repository rows for a deterministic 1% account sample. The full export checks
that pilot's size projection with a 25% margin and a 5 GiB reserve, then retains
all source accounts and repository rows. It persists class/profile indicators
before omitting bio, company, location and user_links. It writes the actual
SQLite/Parquet files, data dictionary, crosswalk, checksums and export evidence.

`release/manifest.json` supplies the pinned collector-derived interpretation,
not a dependency on collector files. Execution history still needs author
confirmation, and the empty-refresh caveat remains. Data/code licenses,
public access and official release identity are pending. Rebuilding does not
resolve those author decisions.

A rebuild records its own code/environment and input hashes. Byte-for-byte
identity with an earlier Arrow/SQLite export is not assumed. Compare source
counts, persisted classes and the scientific aggregates before using a new
export as the released artifact.
