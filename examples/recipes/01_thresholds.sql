-- R1. Account-linked repository rows, not globally distinct repository IDs.
-- The baseline includes complete-class empty and zero-star portfolios.
-- Conditional micro fractions pool rows/stars; macro means weight owners equally.
WITH positive_rows AS (
    SELECT r.user_id,
           count(*) AS n1, sum(CAST(r.stars AS HUGEINT)) AS s1,
           count(*) FILTER (WHERE r.stars >= 10) AS n10,
           coalesce(sum(CAST(r.stars AS HUGEINT)) FILTER (WHERE r.stars >= 10), 0) AS s10,
           count(*) FILTER (WHERE r.stars >= 100) AS n100,
           coalesce(sum(CAST(r.stars AS HUGEINT)) FILTER (WHERE r.stars >= 100), 0) AS s100,
           count(*) FILTER (WHERE r.stars >= 1000) AS n1000,
           coalesce(sum(CAST(r.stars AS HUGEINT)) FILTER (WHERE r.stars >= 1000), 0) AS s1000
    FROM release_repositories r
    JOIN release_portfolios p ON p.user_id = r.user_id
    WHERE p.retrieval_status = 'complete' AND r.stars >= 1
    GROUP BY r.user_id
), per_owner_threshold AS (
    SELECT p.user_id, p.n_repositories, p.total_stars,
           t.threshold, t.kept_rows, t.kept_stars
    FROM release_portfolios p
    LEFT JOIN positive_rows r ON r.user_id = p.user_id
    CROSS JOIN LATERAL (VALUES
        (1, coalesce(r.n1, 0), coalesce(r.s1, 0)),
        (10, coalesce(r.n10, 0), coalesce(r.s10, 0)),
        (100, coalesce(r.n100, 0), coalesce(r.s100, 0)),
        (1000, coalesce(r.n1000, 0), coalesce(r.s1000, 0))
    ) t(threshold, kept_rows, kept_stars)
    WHERE p.retrieval_status = 'complete'
), totals AS (
    SELECT t.threshold, count(p.user_id) AS baseline_accounts,
           coalesce(sum(n_repositories),0) AS baseline_rows, coalesce(sum(CAST(total_stars AS HUGEINT)),0) AS baseline_stars,
           count(*) FILTER (WHERE kept_rows > 0) AS retained_accounts,
           coalesce(sum(kept_rows),0) AS retained_rows, coalesce(sum(kept_stars),0) AS retained_stars,
           coalesce(sum(n_repositories) FILTER (WHERE kept_rows > 0), 0) AS retained_owner_baseline_rows,
           coalesce(sum(CAST(total_stars AS HUGEINT)) FILTER (WHERE kept_rows > 0), 0) AS retained_owner_baseline_stars,
           coalesce(sum(n_repositories-kept_rows) FILTER (WHERE kept_rows > 0), 0) AS hidden_rows,
           coalesce(sum(CAST(total_stars AS HUGEINT)-kept_stars) FILTER (WHERE kept_rows > 0), 0) AS hidden_stars,
           avg(CAST(n_repositories-kept_rows AS DOUBLE)/nullif(n_repositories,0))
               FILTER (WHERE kept_rows > 0) AS macro_mean_hidden_row_fraction,
           avg(CAST(total_stars-kept_stars AS DOUBLE)/nullif(total_stars,0))
               FILTER (WHERE kept_rows > 0) AS macro_mean_hidden_star_fraction
    FROM (VALUES (1),(10),(100),(1000)) t(threshold)
    LEFT JOIN per_owner_threshold p ON p.threshold=t.threshold GROUP BY t.threshold
)
SELECT *,
       CAST(retained_accounts AS DOUBLE)/nullif(baseline_accounts,0) AS retained_account_fraction,
       CAST(retained_rows AS DOUBLE)/nullif(baseline_rows,0) AS retained_row_fraction,
       CAST(retained_stars AS DOUBLE)/nullif(baseline_stars,0) AS retained_star_fraction,
       CAST(hidden_rows AS DOUBLE)/nullif(retained_owner_baseline_rows,0) AS micro_hidden_row_fraction,
       CAST(hidden_stars AS DOUBLE)/nullif(retained_owner_baseline_stars,0) AS micro_hidden_star_fraction
FROM totals ORDER BY threshold;
