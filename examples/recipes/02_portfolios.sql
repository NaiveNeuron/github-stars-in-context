-- R2. Fixed total-star bands, S>0, distributions restricted to N>=2.
-- Singletons are counted separately. N includes zero-star repository rows.
-- quantile_cont uses linear interpolation; probabilities match the Python boxes.
WITH bands(band, stars_lower_inclusive, stars_upper_exclusive, label) AS (
    VALUES (0,1,10,'1-9'), (1,10,100,'10-99'), (2,100,1000,'100-999'),
           (3,1000,10000,'1000-9999'), (4,10000,NULL,'>=10000')
), positive AS (
    SELECT *, CAST(max_stars AS DOUBLE)/total_stars AS dominant_share,
           (CAST(total_stars AS HUGEINT)*total_stars)/nullif(sum_squared_stars,0) AS breadth
    FROM release_portfolios
    WHERE retrieval_status = 'complete' AND total_stars > 0
)
SELECT b.band, b.label, b.stars_lower_inclusive, b.stars_upper_exclusive,
       count(*) FILTER (WHERE p.n_repositories >= 2) AS multi_repository_accounts,
       count(*) FILTER (WHERE p.n_repositories = 1) AS singleton_accounts,
       count(*) FILTER (WHERE p.n_repositories >= 2
                           AND 10*CAST(p.max_stars AS HUGEINT) >= 9*CAST(p.total_stars AS HUGEINT)) AS dominant_ge_0_9_accounts,
       CAST(count(*) FILTER (WHERE p.n_repositories >= 2
                           AND 10*CAST(p.max_stars AS HUGEINT) >= 9*CAST(p.total_stars AS HUGEINT)) AS DOUBLE)
           /nullif(count(*) FILTER (WHERE p.n_repositories >= 2),0) AS dominant_ge_0_9_fraction,
       quantile_cont(p.dominant_share,[0.0,0.05,0.25,0.5,0.75,0.95,1.0])
           FILTER (WHERE p.n_repositories >= 2) AS dominant_share_quantiles,
       quantile_cont(p.breadth,[0.0,0.05,0.25,0.5,0.75,0.95,1.0])
           FILTER (WHERE p.n_repositories >= 2) AS effective_breadth_quantiles
FROM bands b LEFT JOIN positive p
    ON p.total_stars >= b.stars_lower_inclusive
       AND (b.stars_upper_exclusive IS NULL OR p.total_stars < b.stars_upper_exclusive)
GROUP BY b.band,b.label,b.stars_lower_inclusive,b.stars_upper_exclusive
ORDER BY b.band;
