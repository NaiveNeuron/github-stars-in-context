import sqlite3
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional
from utils.logger import logger, log_block
from utils.paths import DB_PATH, DATA_ANALYSIS_DIR


def get_top_h_index_developers(top_n: int = 20, min_h_index: int = 10) -> pd.DataFrame:
    logger.info(f"Fetching top {top_n} developers with h-index ≥ {min_h_index}")

    query = """
    WITH user_repo_stats AS (
        SELECT
            u.id AS user_id,
            u.github_name,
            u.h_index,
            u.followers_count,
            u.following_count,
            u.organizations_count,
            u.bio,
            u.bio_length,
            COUNT(r.repository_id) AS repo_count,
            COALESCE(SUM(r.stars), 0) AS total_stars,
            COALESCE(AVG(r.stars), 0) AS avg_stars,
            COALESCE(MAX(r.stars), 0) AS max_stars,
            COALESCE(MIN(r.stars), 0) AS min_stars
        FROM users u
        LEFT JOIN repositories r ON u.id = r.user_id
        WHERE u.h_index >= ?
        AND u.repositories_fetched = 1
        GROUP BY u.id, u.github_name, u.h_index, u.followers_count,
                 u.following_count, u.organizations_count, u.bio, u.bio_length
        ORDER BY u.h_index DESC, total_stars DESC
        LIMIT ?
    )
    SELECT
        ROW_NUMBER() OVER (ORDER BY h_index DESC, total_stars DESC) AS rank,
        github_name,
        h_index,
        followers_count,
        total_stars,
        repo_count,
        ROUND(avg_stars, 2) AS avg_stars,
        max_stars,
        organizations_count,
        bio_length,
        CASE 
            WHEN bio_length > 0 THEN SUBSTR(bio, 1, 100) || 
                CASE WHEN LENGTH(bio) > 100 THEN '...' ELSE '' END
            ELSE NULL 
        END AS bio_summary
    FROM user_repo_stats
    ORDER BY rank
    """

    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(query, conn, params=(min_h_index, top_n))
        conn.close()

        if df.empty:
            logger.warning(f"No developers found with h-index ≥ {min_h_index}")
            return df

        logger.info(f"Retrieved {len(df)} high h-index developers")
        log_block(
            "TOP DEVELOPERS SUMMARY",
            f"Highest h-index: {df['h_index'].max()}",
            f"Lowest h-index (top {top_n}): {df['h_index'].min()}",
            f"Total followers (sum): {df['followers_count'].sum():,}",
            f"Total stars (sum): {df['total_stars'].sum():,}",
            f"Average h-index: {df['h_index'].mean():.2f}"
        )

        return df

    except sqlite3.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error fetching top developers: {str(e)}")
        raise

def export_for_analysis(top_n: int = 20, min_h_index: int = 10, output_dir: Optional[Path] = None) -> Dict:
    from datetime import datetime

    if output_dir is None:
        output_dir = DATA_ANALYSIS_DIR / "empirical_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = get_top_h_index_developers(top_n=top_n, min_h_index=min_h_index)

    if df.empty:
        return {'error': 'No data found'}

    results = {'dataframe': df, 'timestamp': datetime.now().isoformat()}

    csv_path = output_dir / f"top_{top_n}_developers.csv"
    df.to_csv(csv_path, index=False)
    results['csv_path'] = csv_path
    logger.info(f"CSV saved: {csv_path}")

    log_block(
        "EMPIRICAL ANALYSIS EXPORT COMPLETE",
        f"Output directory: {output_dir}",
        f"Developers analyzed: {len(df)}",
        f"Files generated: CSV"
    )

    return results


if __name__ == "__main__":
    results = export_for_analysis(top_n=20, min_h_index=15)

    logger.info("EMPIRICAL ANALYSIS - SECTION 4.3 SUMMARY")

    df = results['dataframe']
    logger.info(f"\nTop {len(df)} Developers by h-index:\n")
    logger.info(df[['rank', 'github_name', 'h_index', 'followers_count',
              'total_stars', 'repo_count']].to_string(index=False))