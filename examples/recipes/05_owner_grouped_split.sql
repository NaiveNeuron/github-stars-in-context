-- R5. Owner-grouped splitting of a star-filtered repository sample.
-- Sample: repository rows with at least 100 stars, owned by accounts whose
-- retrieval completed. Two deterministic 80/20 splits are compared:
--   row split:   each repository is assigned by a hash of its GitHub ID
--   owner split: each repository follows a hash of its owner's GitHub ID
-- A test row "shares an owner with training" when the same account also owns
-- a training row. The owner split has no such rows by construction.
-- The hash is the first 32 bits of MD5, so the assignment does not depend on
-- the DuckDB version. To export an assignment, select from `assigned`.
-- This shows the mechanism on this dataset. It does not measure leakage in
-- any existing benchmark.
WITH sample AS (
    SELECT r.repository_id, r.user_id, p.github_id AS owner_github_id
    FROM release_repositories r
    JOIN release_portfolios p ON p.user_id = r.user_id
    WHERE p.retrieval_status = 'complete' AND r.stars >= 100
), assigned AS (
    SELECT *,
           CAST('0x' || substr(md5(CAST(repository_id AS VARCHAR)), 1, 8) AS BIGINT) % 100 < 20
               AS row_split_test,
           CAST('0x' || substr(md5(CAST(owner_github_id AS VARCHAR)), 1, 8) AS BIGINT) % 100 < 20
               AS owner_split_test
    FROM sample
), per_owner AS (
    SELECT user_id,
           count(*) FILTER (WHERE NOT row_split_test) AS row_split_train_rows,
           count(*) FILTER (WHERE NOT owner_split_test) AS owner_split_train_rows
    FROM assigned GROUP BY user_id
), summary AS (
    SELECT 'row' AS split,
           count(*) FILTER (WHERE NOT a.row_split_test) AS train_rows,
           count(*) FILTER (WHERE a.row_split_test) AS test_rows,
           count(*) FILTER (WHERE a.row_split_test AND o.row_split_train_rows > 0)
               AS test_rows_sharing_owner_with_train,
           count(DISTINCT a.user_id) FILTER (WHERE a.row_split_test) AS test_owners,
           count(DISTINCT a.user_id) FILTER (WHERE a.row_split_test AND o.row_split_train_rows > 0)
               AS test_owners_also_in_train
    FROM assigned a JOIN per_owner o ON o.user_id = a.user_id
    UNION ALL
    SELECT 'owner',
           count(*) FILTER (WHERE NOT a.owner_split_test),
           count(*) FILTER (WHERE a.owner_split_test),
           count(*) FILTER (WHERE a.owner_split_test AND o.owner_split_train_rows > 0),
           count(DISTINCT a.user_id) FILTER (WHERE a.owner_split_test),
           count(DISTINCT a.user_id) FILTER (WHERE a.owner_split_test AND o.owner_split_train_rows > 0)
    FROM assigned a JOIN per_owner o ON o.user_id = a.user_id
)
SELECT *,
       CAST(test_rows_sharing_owner_with_train AS DOUBLE) / nullif(test_rows, 0)
           AS test_row_share_sharing_owner
FROM summary ORDER BY split DESC;
