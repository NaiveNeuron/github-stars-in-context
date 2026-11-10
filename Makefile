PYTHON ?= python3
DATA ?= data
EXPECTED ?= expected
RESULTS ?= results

.PHONY: parquet-check sqlite-check archive-join compare-results
parquet-check:
	$(PYTHON) scripts/validate_recipes.py --release-dir "$(DATA)" --source-dir "$(EXPECTED)/source" --export-evidence "$(EXPECTED)/export.json" --out-dir "$(RESULTS)/parquet"

sqlite-check:
	$(PYTHON) scripts/inventory.py --db "$(DATA)/portfolios.sqlite" --manifest "$(DATA)/analysis-manifest.json" --out-dir "$(RESULTS)/sqlite"
	$(PYTHON) scripts/quality.py --db "$(DATA)/portfolios.sqlite" --manifest "$(DATA)/analysis-manifest.json" --out-dir "$(RESULTS)/sqlite" --skip-h-agreement --verify-stored-h --timestamp-sentinel users.last_updated=6200-01-01
	$(PYTHON) scripts/run_reuse.py --db "$(DATA)/portfolios.sqlite" --manifest "$(DATA)/analysis-manifest.json" --out-dir "$(RESULTS)/sqlite" --quality-evidence "$(RESULTS)/sqlite/quality.json"
	$(PYTHON) examples/05_owner_clustering.py --db "$(DATA)/portfolios.sqlite" --manifest "$(DATA)/analysis-manifest.json" --out-dir "$(RESULTS)/sqlite" --quality-evidence "$(RESULTS)/sqlite/quality.json"

archive-join:
	$(PYTHON) examples/06_gharchive_join.py --db "$(DATA)/portfolios.sqlite" --manifest "$(DATA)/analysis-manifest.json" --out-dir "$(RESULTS)/sqlite" --quality-evidence "$(RESULTS)/sqlite/quality.json"

compare-results:
	$(PYTHON) scripts/verify_reproduction.py --source-dir "$(EXPECTED)/source" --released-dir "$(RESULTS)/sqlite" --export-evidence "$(EXPECTED)/export.json" --out-dir "$(RESULTS)/sqlite"
