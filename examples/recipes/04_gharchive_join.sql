-- Join one GH Archive hour to the released repository rows by numeric
-- GitHub repository ID. Download the hour first (36 MB):
--   curl -fL -o data/gharchive-2026-01-03-0.json.gz \
--        https://data.gharchive.org/2026-01-03-0.json.gz
-- Run from the repository root with an in-memory DuckDB connection.
-- Expected result for this hour:
--   events 143769, keyed_events 143767, distinct_ids 63567,
--   matched_events 30738, matched_ids 17382
-- Event actors need not be repository owners, and one hour of events is not
-- a star history or a coverage estimate.
WITH events AS (
  SELECT CAST(repo.id AS BIGINT) AS repo_id
  FROM read_json('data/gharchive-2026-01-03-0.json.gz',
                 format = 'newline_delimited',
                 columns = {'repo': 'STRUCT(id BIGINT)'},
                 ignore_errors = true)
),
keyed AS (SELECT repo_id FROM events WHERE repo_id IS NOT NULL),
ours AS (SELECT DISTINCT repository_id FROM 'data/repositories.parquet')
SELECT
  (SELECT count(*) FROM events) AS events,
  (SELECT count(*) FROM keyed) AS keyed_events,
  (SELECT count(DISTINCT repo_id) FROM keyed) AS distinct_ids,
  (SELECT count(*) FROM keyed k JOIN ours o ON k.repo_id = o.repository_id) AS matched_events,
  (SELECT count(DISTINCT k.repo_id) FROM keyed k JOIN ours o ON k.repo_id = o.repository_id) AS matched_ids;
