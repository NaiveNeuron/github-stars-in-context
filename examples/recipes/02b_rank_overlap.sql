-- R2 account-selection check. No names or account IDs are returned.
-- Exact-k ties use ascending numeric GitHub account ID, not local owner IDs.
-- Include every complete-class account, including zero-star/empty portfolios.
WITH ranked AS (
    SELECT u.github_id, p.h_observed, p.total_stars,
           row_number() OVER (ORDER BY p.h_observed DESC,u.github_id ASC) AS h_rank,
           row_number() OVER (ORDER BY p.total_stars DESC,u.github_id ASC) AS star_rank
    FROM release_portfolios p JOIN release_users u ON p.user_id=u.id
    WHERE p.retrieval_status='complete'
), boundaries AS (
    SELECT k.requested_k, count(r.github_id) AS cohort_accounts,
           least(k.requested_k,count(r.github_id)) AS effective_k,
           min(r.h_observed) FILTER (WHERE r.h_rank<=k.requested_k) AS h_cutoff,
           min(r.total_stars) FILTER (WHERE r.star_rank<=k.requested_k) AS star_cutoff
    FROM (VALUES (100),(1000),(10000)) k(requested_k)
    LEFT JOIN ranked r ON true GROUP BY k.requested_k
), counts AS (
    SELECT b.requested_k,b.cohort_accounts,b.effective_k,b.h_cutoff,b.star_cutoff,
           count(*) FILTER (WHERE r.h_rank<=b.requested_k AND r.star_rank<=b.requested_k) AS exact_overlap_accounts,
           count(*) FILTER (WHERE r.h_observed=b.h_cutoff) AS h_boundary_tie_accounts,
           count(*) FILTER (WHERE r.total_stars=b.star_cutoff) AS star_boundary_tie_accounts,
           count(*) FILTER (WHERE r.h_observed>=b.h_cutoff) AS h_tie_inclusive_accounts,
           count(*) FILTER (WHERE r.total_stars>=b.star_cutoff) AS star_tie_inclusive_accounts,
           count(*) FILTER (WHERE r.h_observed>=b.h_cutoff AND r.total_stars>=b.star_cutoff) AS tie_inclusive_overlap_accounts
    FROM boundaries b LEFT JOIN ranked r ON true
    GROUP BY b.requested_k,b.cohort_accounts,b.effective_k,b.h_cutoff,b.star_cutoff
)
SELECT *,CAST(exact_overlap_accounts AS DOUBLE)/nullif(effective_k,0) AS exact_overlap_fraction,
       CAST(tie_inclusive_overlap_accounts AS DOUBLE)/nullif(h_tie_inclusive_accounts,0) AS tie_inclusive_h_overlap_fraction,
       CAST(tie_inclusive_overlap_accounts AS DOUBLE)/nullif(star_tie_inclusive_accounts,0) AS tie_inclusive_star_overlap_fraction
FROM counts ORDER BY requested_k;
