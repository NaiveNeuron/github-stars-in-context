import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import FAILED_FETCH_MARKER_DATE
from data_analysis.h_index_calculator import calculate_h_index_from_stars
from utils.logger import logger
from utils.paths import DB_PATH

class DatabaseManager:
    def __init__(self):
        self.db_path = DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._initialize_db()
        logger.info(f"Database initialized at {self.db_path}")

    def _initialize_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.executescript(self._load_schema())
                conn.commit()
                logger.debug("Database schema initialized successfully")
        except sqlite3.Error as e:
            logger.critical(f"Database initialization error: {str(e)}", exc_info=True)
            raise

    def _load_schema(self) -> str:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(module_dir)
        sql_path = os.path.join(project_root, "sql", "schema.sql")

        try:
            with open(sql_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            logger.critical("Load schema")
            raise FileNotFoundError()

    def get_or_create_user(self, github_id: int, github_name: str, created_at: str) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, repositories_fetched FROM users WHERE github_id = ?",
                    (github_id,)
                )
                result = cursor.fetchone()
                if result:
                    user_id = result[0]
                    cursor.execute(
                        "UPDATE users SET last_updated = ? WHERE id = ?",
                        (datetime.now().isoformat(), user_id)
                    )
                    conn.commit()
                    logger.debug(f"Updated existing user {github_name} (ID: {user_id})")
                    return user_id

                cursor.execute(
                    """
                    INSERT INTO users (
                        github_id, github_name, created_at, last_updated, 
                        repositories_fetched, bio, bio_length
                    ) VALUES (?, ?, ?, ?, 0, NULL, 0)
                    """,
                    (github_id, github_name, created_at, datetime.now().isoformat())
                )
                user_id = cursor.lastrowid
                conn.commit()
                logger.debug(f"Created new user {github_name} (ID: {user_id}) with bio=NULL")
                return user_id
        except sqlite3.Error as e:
            logger.error(f"Error getting/creating user {github_name}: {str(e)}")
            raise

    def update_user_repositories(self, user_id: int, repositories: List[Dict]) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                seen_repo_ids = set()

                for repo in repositories:
                    repo_id = repo["databaseId"]
                    seen_repo_ids.add(repo_id)

                    cursor.execute(
                        "SELECT id FROM repositories WHERE user_id = ? AND repository_id = ?",
                        (user_id, repo_id)
                    )
                    result = cursor.fetchone()

                    if not result:
                        cursor.execute(
                            """
                                INSERT INTO repositories (user_id, repository_id, name, created_at, stars)
                                VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                user_id,
                                repo_id,
                                repo["name"],
                                repo["createdAt"],
                                repo["stargazerCount"]
                            )
                        )
                        logger.debug(f"Added new repository: {repo['name']} (ID: {repo_id})")
                    else:
                        cursor.execute(
                            "UPDATE repositories SET stars = ? WHERE user_id = ? AND repository_id = ?",
                            (repo["stargazerCount"], user_id, repo_id)
                        )

                if seen_repo_ids:
                    placeholders = ",".join(["?"] * len(seen_repo_ids))
                    delete_sql = f"DELETE FROM repositories WHERE user_id = ? AND repository_id NOT IN ({placeholders})"
                    cursor.execute(
                        delete_sql,
                        (user_id, *seen_repo_ids)
                    )
                    deleted_count = cursor.rowcount
                    if deleted_count > 0:
                        logger.info(f"Deleted {deleted_count} repositories (no longer exist on GitHub)")

                conn.commit()
                logger.info(f"Successfully updated {len(repositories)} repositories for user ID {user_id}")

        except sqlite3.Error as e:
            logger.error(f"Error updating repositories for user ID {user_id}: {str(e)}")
            raise

    def calculate_and_update_h_index(self, user_id: int) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT stars FROM repositories WHERE user_id = ? ORDER BY stars DESC",
                    (user_id,)
                )
                star_counts = [row[0] for row in cursor.fetchall()]

                h_index = calculate_h_index_from_stars(star_counts)

                cursor.execute(
                    "UPDATE users SET h_index = ? WHERE id = ?",
                    (h_index, user_id)
                )
                conn.commit()
                logger.info(f"Calculated h-index {h_index} for user ID {user_id} "
                            f"from {len(star_counts)} repositories")
                return h_index
        except sqlite3.Error as e:
            logger.error(f"Error calculating h-index for user ID {user_id}: {str(e)}")
            raise

    def get_user_data(self, github_name: str) -> Dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT id, github_id, github_name, created_at, last_updated, h_index, repositories_fetched FROM users WHERE github_name = ?",
                    (github_name,)
                )
                user_row = cursor.fetchone()

                if not user_row:
                    logger.warning(f"User {github_name} not found in database")
                    return None

                cursor.execute(
                    "SELECT name, created_at, repository_id, stars FROM repositories WHERE user_id = ? ORDER BY stars DESC",
                    (user_row[0],)
                )
                repos = [{
                    "name": row[0],
                    "created_at": row[1],
                    "repository_id": row[2],
                    "stars": row[3]
                } for row in cursor.fetchall()]

                return {
                    "id": user_row[0],
                    "github_id": user_row[1],
                    "github_name": user_row[2],
                    "created_at": user_row[3],
                    "last_updated": user_row[4],
                    "h_index": user_row[5],
                    "repositories_fetched": bool(user_row[6]),
                    "repositories": repos
                }

        except sqlite3.Error as e:
            logger.error(f"Error retrieving user data for {github_name}: {str(e)}")
            raise

    def update_user_from_github(self, github_name: str, token: str = None) -> Dict:
        logger.info(f"Updating user data from GitHub for {github_name}")
        from data_fetching.github_parser import fetch_user_and_repos
        from utils.secrets import GITHUB_TOKEN

        token_to_use = token if token is not None else GITHUB_TOKEN
        fresh_data = fetch_user_and_repos(github_name, token_to_use)
        user_id = self.get_or_create_user(
            fresh_data["user_id"],
            github_name,
            fresh_data["user_created_at"]
        )
        self.update_user_repositories(user_id, fresh_data["repositories"])
        self.calculate_and_update_h_index(user_id)
        self.mark_repositories_fetched(user_id)
        return self.get_user_data(github_name)
    def mark_repositories_fetched(self, user_id: int) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET repositories_fetched = 1 WHERE id = ?",
                    (user_id,)
                )
                conn.commit()
                logger.debug(f"Marked user ID {user_id} as having repositories fetched")
        except sqlite3.Error as e:
            logger.error(f"Error marking repositories as fetched for user {user_id}: {str(e)}")
            raise

    def get_next_user_for_repository_fetch(self) -> Optional[Dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, github_name FROM users WHERE repositories_fetched = 0 LIMIT 1"
                )
                result = cursor.fetchone()
                if result:
                    return {"id": result[0], "github_name": result[1]}
                return None
        except sqlite3.Error as e:
            logger.error(f"Error getting next user for repository fetch: {str(e)}")
            raise

    def mark_repositories_fetch_failed(self, user_id: int, failed_date: str) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET repositories_fetched = 1, last_updated = ? WHERE id = ?",
                    (failed_date, user_id)
                )
                conn.commit()
                logger.debug(f"Marked user ID {user_id} as having repository fetch failed (last_updated set to {failed_date})")
        except sqlite3.Error as e:
            logger.error(f"Error marking repositories fetch as failed for user {user_id}: {str(e)}")
            raise

    def get_users_for_repository_fetch(self, limit: int = 3) -> List[Dict]:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("BEGIN EXCLUSIVE")

            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, github_id, github_name
                FROM users
                WHERE repositories_fetched = 0
                ORDER BY id ASC
                LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()

            if rows:
                user_ids = [row[0] for row in rows]
                placeholders = ','.join(['?'] * len(user_ids))
                cursor.execute(f"""
                    UPDATE users 
                    SET repositories_fetched = -1 
                    WHERE id IN ({placeholders})
                """, user_ids)

                conn.commit()

                return [{
                    "id": row[0],
                    "github_id": row[1],
                    "github_name": row[2]
                } for row in rows]

            return []
        except sqlite3.Error as e:
            logger.error(f"Error getting users for repository fetch: {str(e)}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

    def reset_repository_fetch_status(self, user_id: int) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE users 
                    SET repositories_fetched = 0 
                    WHERE id = ? AND repositories_fetched = -1
                """, (user_id,))
                conn.commit()
                logger.debug(f"Reset repository fetch status for user ID {user_id}")
        except sqlite3.Error as e:
            logger.error(f"Error resetting repository fetch status for user {user_id}: {str(e)}")
            raise

    def get_next_user_with_missing_bio(self) -> Optional[Dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, github_name 
                    FROM users 
                    WHERE bio IS NULL OR bio = ''
                    ORDER BY last_updated ASC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                return {"id": row[0], "github_name": row[1]} if row else None
        except sqlite3.Error as e:
            logger.error(f"Error search use with empty bio: {str(e)}")
            return None

    def add_user_links(self, user_id: int, links: List[Dict[str, str]]) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()

                for link in links:
                    try:
                        cursor.execute("""
                            INSERT OR IGNORE INTO user_links (user_id, url, link_type, created_at)
                            VALUES (?, ?, ?, ?)
                        """, (user_id, link['url'], link.get('type', 'unknown'), now))
                    except sqlite3.IntegrityError:
                        pass

                conn.commit()
                logger.debug(f"Added {len(links)} links for user ID {user_id}")
        except sqlite3.Error as e:
            logger.error(f"Error adding links for user ID {user_id}: {str(e)}")
            raise

    def get_user_links(self, user_id: int) -> List[str]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT url FROM user_links WHERE user_id = ?", (user_id,))
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting links for user ID {user_id}: {str(e)}")
            return []

    def mark_links_parsed(self, user_id: int) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET links_parsed_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), user_id)
                )
                conn.commit()
                logger.debug(f"Marked user ID {user_id} as links parsed")
        except sqlite3.Error as e:
            logger.error(f"Error marking links parsed for user {user_id}: {str(e)}")
            raise

    def get_users_needing_links_parse(self, limit: int = 100, days_since_update: int = 30) -> List[Dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if days_since_update > 0:
                    cutoff_date = (
                            datetime.now() - timedelta(days=days_since_update)
                    ).isoformat()

                    cursor.execute("""
                        SELECT id, github_name
                        FROM users
                        WHERE (links_parsed_at IS NULL OR links_parsed_at < ?)
                        AND last_updated != '6200-01-01T00:00:00'
                        ORDER BY 
                            CASE WHEN links_parsed_at IS NULL THEN 0 ELSE 1 END,
                            links_parsed_at ASC
                        LIMIT ?
                    """, (cutoff_date, limit))
                else:
                    cursor.execute("""
                        SELECT id, github_name
                        FROM users
                        WHERE last_updated != '6200-01-01T00:00:00'
                        ORDER BY id ASC
                        LIMIT ?
                    """, (limit,))

                rows = cursor.fetchall()
                return [{"id": row[0], "github_name": row[1]} for row in rows]

        except sqlite3.Error as e:
            logger.error(f"Error getting users needing links parse: {str(e)}")
            raise

    def reset_links_parse_timestamps(self, keep_failed_users: bool = True, failed_marker_date: str = "6200-01-01T00:00:00") -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if keep_failed_users:
                    cursor.execute("""
                        UPDATE users
                        SET links_parsed_at = NULL
                        WHERE last_updated != ?
                    """, (failed_marker_date,))
                else:
                    cursor.execute("""
                        UPDATE users
                        SET links_parsed_at = NULL
                    """)

                reset_count = cursor.rowcount
                conn.commit()
                logger.info(f"Reset links_parsed_at for {reset_count} users")
                return reset_count

        except sqlite3.Error as e:
            logger.error(f"Error resetting links parse timestamps: {str(e)}")
            raise

    def clear_user_links(self, user_id: int) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_links WHERE user_id = ?", (user_id,))
                conn.commit()
                logger.debug(f"Cleared links for user ID {user_id}")
        except sqlite3.Error as e:
            logger.error(f"Error clearing links for user ID {user_id}: {str(e)}")
            raise

    def get_links_parse_stats(self) -> Dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM users")
                total_users = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT COUNT(*) FROM users 
                    WHERE links_parsed_at IS NOT NULL
                    AND last_updated != '6200-01-01T00:00:00'
                """)
                parsed_users = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM user_links")
                total_links = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT COUNT(*) FROM users u
                    WHERE u.id NOT IN (SELECT DISTINCT user_id FROM user_links)
                    AND u.last_updated != '6200-01-01T00:00:00'
                """)
                users_without_links = cursor.fetchone()[0]

                return {
                    "total_users": total_users,
                    "parsed_users": parsed_users,
                    "total_links": total_links,
                    "users_without_links": users_without_links,
                    "parse_percentage": (parsed_users / total_users * 100) if total_users > 0 else 0
                }

        except sqlite3.Error as e:
            logger.error(f"Error getting links parse stats: {str(e)}")
            return {}

    def mark_links_parse_failed(self, user_id: int, failed_date: str = "6200-01-01T00:00:00") -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET last_updated = ? WHERE id = ?",
                    (failed_date, user_id)
                )
                conn.commit()
                logger.debug(f"Marked user ID {user_id} as links parse failed (last_updated set to {failed_date})")
        except sqlite3.Error as e:
            logger.error(f"Error marking links parse as failed for user {user_id}: {str(e)}")
            raise

    def reset_last_updated_time(self, keep_failed_users: bool = True, failed_marker_date: str = "6200-01-01T00:00:00", reset_date: str = "1900-01-01T00:00:00") -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if keep_failed_users:
                    cursor.execute("""
                        UPDATE users
                        SET last_updated = ?
                        WHERE last_updated != ?
                    """, (reset_date, failed_marker_date))
                else:
                    cursor.execute("""
                        UPDATE users
                        SET last_updated = ?
                    """, (reset_date,))

                reset_count = cursor.rowcount
                conn.commit()
                logger.info(f"Reset last_updated to {reset_date} for {reset_count} users")
                return reset_count
        except sqlite3.Error as e:
            logger.error(f"Error resetting last_updated time: {str(e)}")
            raise

    def reset_profile_data(self, keep_failed_users: bool = True, failed_marker_date: str = "6200-01-01T00:00:00") -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if keep_failed_users:
                    cursor.execute("""
                        UPDATE users
                        SET bio = NULL, bio_length = 0,
                            followers_count = 0, following_count = 0,
                            sponsors_count = 0, organizations_count = 0,
                            company = NULL, location = NULL
                        WHERE last_updated != ?
                    """, (failed_marker_date,))
                else:
                    cursor.execute("""
                        UPDATE users
                        SET bio = NULL, bio_length = 0,
                            followers_count = 0, following_count = 0,
                            sponsors_count = 0, organizations_count = 0,
                            company = NULL, location = NULL
                    """)

                reset_count = cursor.rowcount
                conn.commit()
                logger.info(f"Reset profile data for {reset_count} users")
                return reset_count
        except sqlite3.Error as e:
            logger.error(f"Error resetting profile data: {str(e)}")
            raise