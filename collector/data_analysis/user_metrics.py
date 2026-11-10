import duckdb
from pathlib import Path
from utils.logger import logger
from utils.paths import DB_PATH, CSV_DIR, USER_METRICS_PATH

def export_user_metrics_to_csv():
    query = """
        WITH user_repo_data AS (
            SELECT
                u.github_id          AS user_id,
                u.h_index,
                u.followers_count,
                u.following_count,
                u.organizations_count,
                u.bio_length,
                u.created_at,
                COUNT(r.repository_id)       AS repo_count,
                COALESCE(SUM(r.stars), 0)    AS total_stars,
                COALESCE(AVG(r.stars), 0.0)  AS avg_stars,
                COALESCE(MEDIAN(r.stars), 0) AS median_stars,
                COALESCE(MAX(r.stars), 0)    AS max_stars,
                COALESCE(VARIANCE(r.stars), 0.0) AS star_variance
            FROM sqlite_db.users u
            LEFT JOIN sqlite_db.repositories r ON u.id = r.user_id
            WHERE u.github_id IS NOT NULL
              AND u.repositories_fetched = 1
            GROUP BY u.github_id, u.created_at, u.h_index,
                     u.followers_count, u.following_count, u.organizations_count,
                     u.bio_length
        )
        SELECT
            user_id,
            h_index,
            repo_count,
            total_stars,
            avg_stars,
            median_stars,
            max_stars,
            star_variance,
            followers_count,
            following_count,
            organizations_count,
            bio_length
        FROM user_repo_data
        WHERE h_index IS NOT NULL
        """

    try:
        USER_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect()
        conn.execute(f"ATTACH '{DB_PATH}' AS sqlite_db (TYPE SQLITE);")

        total_users = conn.execute(""" SELECT COUNT(*) FROM sqlite_db.users WHERE github_id IS NOT NULL """).fetchone()[0]

        export_query = f"COPY ({query}) TO '{USER_METRICS_PATH}' (HEADER, DELIMITER ',')"
        conn.execute(export_query)

        exported_count = conn.execute(f"SELECT COUNT(*) FROM read_csv_auto('{USER_METRICS_PATH}')").fetchone()[0]
        logger.info(f"Successfully exported metrics for {exported_count}/{total_users} users")
        logger.info(f"CSV file saved to: {USER_METRICS_PATH}")

        if exported_count < total_users:
            logger.warning(f"{total_users - exported_count} users were not exported. Check for data issues.")

        return USER_METRICS_PATH
    except Exception as e:
        logger.error(f"Error exporting user metrics: {str(e)}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()