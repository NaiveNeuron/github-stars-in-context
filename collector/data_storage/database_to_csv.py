import sqlite3
import csv
import hashlib
from pathlib import Path
from typing import Optional

from utils.logger import logger
from utils.paths import DB_PATH, CSV_PATH, CSV_DIR


def hash_username(username: str) -> str:
    return hashlib.sha256(username.encode('utf-8')).hexdigest()[:16]


def export_users_sample(limit: int = 200, order_by: str = "id", anonymize_name: bool = True) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = f"""
    SELECT
        id,
        github_id,
        github_name,
        created_at,
        last_updated,
        h_index,
        repositories_fetched
    FROM users
    ORDER BY {order_by}
    LIMIT ?
    """

    cursor.execute(query, (limit,))
    rows = cursor.fetchall()

    if not rows:
        logger.error("No users found in the database.")
        conn.close()
        return

    logger.info(f"Exporting {len(rows)} users to {CSV_PATH}...")

    fieldnames = [description[0] for description in cursor.description]

    Path(CSV_PATH).parent.mkdir(parents=True, exist_ok=True)

    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            row_dict = dict(row)

            if anonymize_name and 'github_name' in row_dict:
                original_name = row_dict['github_name']
                row_dict['github_name'] = f"user_{hash_username(original_name)}"

            if 'repositories_fetched' in row_dict:
                row_dict['repositories_fetched'] = 'Yes' if row_dict['repositories_fetched'] else 'No'

            writer.writerow(row_dict)

    conn.close()

    logger.info(f"Done! Exported {len(rows)} users to '{CSV_PATH}'")
    logger.info(f"Full path: {Path(CSV_PATH).resolve()}")


def export_top_h_index_users(limit: int = 100, output_path: Optional[Path] = None, anonymize_name: bool = False) -> Path:
    if output_path is None:
        output_path = CSV_DIR / "top_h_index_users.csv"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
    SELECT
        id,
        github_id,
        github_name,
        created_at,
        last_updated,
        h_index,
        followers_count,
        following_count,
        organizations_count,
        bio_length,
        repositories_fetched
    FROM users
    WHERE h_index > 0
    ORDER BY h_index DESC
    LIMIT ?
    """

    cursor.execute(query, (limit,))
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        logger.error("No users with h-index found in the database.")
        raise ValueError("No users with h-index > 0 found in database")

    fieldnames = [description[0] for description in cursor.description]
    fieldnames.insert(0, 'rank')

    conn.close()

    logger.info(f"Exporting top {len(rows)} h-index users to {output_path}...")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            row_dict = dict(row)

            if anonymize_name and 'github_name' in row_dict:
                original_name = row_dict['github_name']
                row_dict['github_name'] = f"user_{hash_username(original_name)}"

            if 'repositories_fetched' in row_dict:
                row_dict['repositories_fetched'] = 'Yes' if row_dict['repositories_fetched'] else 'No'

            row_dict['rank'] = idx
            writer.writerow(row_dict)

    logger.info(f"✓ Done! Exported top {len(rows)} h-index users to '{output_path}'")
    logger.info(f"  Full path: {output_path.resolve()}")
    logger.info(f"  Highest h-index: {rows[0]['h_index']}")
    logger.info(f"  Lowest h-index (top {limit}): {rows[-1]['h_index']}")

    return output_path

if __name__ == "__main__":
    export_top_h_index_users(limit=100, anonymize_name=False)

