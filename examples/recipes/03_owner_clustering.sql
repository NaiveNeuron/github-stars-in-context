-- R3. Multiple selected rows per owner, at the same inclusive star threshold.
-- A globally repeated repository ID remains one row in each ownership context.
WITH per_owner AS (
    SELECT r.user_id,
           count(*) AS n1,
           count(*) FILTER (WHERE r.stars>=10) AS n10,
           count(*) FILTER (WHERE r.stars>=100) AS n100,
           count(*) FILTER (WHERE r.stars>=1000) AS n1000
    FROM release_repositories r JOIN release_portfolios p ON p.user_id=r.user_id
    WHERE p.retrieval_status='complete' AND r.stars>=1 GROUP BY r.user_id
), selected AS (
    SELECT r.user_id,t.threshold,t.selected_rows
    FROM per_owner r CROSS JOIN LATERAL
        (VALUES (1,r.n1),(10,r.n10),(100,r.n100),(1000,r.n1000)) t(threshold,selected_rows)
    WHERE t.selected_rows>0
), thresholds(threshold) AS (VALUES (1),(10),(100),(1000))
SELECT t.threshold,count(s.user_id) AS unique_owners,
       coalesce(sum(s.selected_rows),0) AS selected_rows,
       count(*) FILTER (WHERE s.selected_rows>=2) AS owners_with_multiple_selected_rows,
       coalesce(sum(s.selected_rows) FILTER (WHERE s.selected_rows>=2),0) AS rows_from_multiple_row_owners,
       CAST(coalesce(sum(s.selected_rows) FILTER (WHERE s.selected_rows>=2),0) AS DOUBLE)
           /nullif(sum(s.selected_rows),0) AS clustered_row_fraction,
       avg(s.selected_rows) AS mean_rows_per_retained_owner,
       quantile_cont(s.selected_rows,[0.0,0.25,0.5,0.75,0.9,0.95,0.99,1.0]) AS rows_per_owner_quantiles
FROM thresholds t LEFT JOIN selected s ON s.threshold=t.threshold
GROUP BY t.threshold ORDER BY t.threshold;
