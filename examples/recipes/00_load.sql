-- Run from the processing checkout root with the downloaded release/ directory.
-- Use an in-memory DuckDB connection. These views do not modify the Parquet files.
CREATE OR REPLACE TEMP VIEW release_users AS
SELECT * FROM read_parquet('release/users.parquet');
CREATE OR REPLACE TEMP VIEW release_repositories AS
SELECT * FROM read_parquet('release/repositories.parquet');
CREATE OR REPLACE TEMP VIEW release_portfolios AS
SELECT * FROM read_parquet('release/portfolios.parquet');
