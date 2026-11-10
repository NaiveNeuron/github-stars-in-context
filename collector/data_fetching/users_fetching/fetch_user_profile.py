import time
import requests
import sqlite3
import threading
import concurrent.futures
from typing import Optional, Dict, List, Tuple
from utils.logger import logger
from utils.paths import DB_PATH
from datetime import datetime
from utils.token_manager import GLOBAL_TOKEN_MANAGER
from utils.secrets import GITHUB_TOKENS

GRAPHQL_URL = "https://api.github.com/graphql"

if not GITHUB_TOKENS:
    logger.warning("No GitHub tokens loaded. Some features may not work.")

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=len(GITHUB_TOKENS) * 2, pool_maxsize=len(GITHUB_TOKENS) * 5, max_retries=2)
_session.mount("https://", _adapter)


def _fetch_single_profile(login: str, token: str) -> Optional[Dict]:
    try:
        response = _session.post(
            GRAPHQL_URL,
            json={"query": USER_PROFILE_QUERY_BASIC, "variables": {"login": login}},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "git-h-index/1.0"
            },
            timeout=(10, 30)
        )
        if response.status_code == 401:
            logger.warning(f"Token {token[:8]} invalid (401) for {login}")
            return None
        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
            if GLOBAL_TOKEN_MANAGER:
                GLOBAL_TOKEN_MANAGER.mark_rate_limited(token, reset)
            return "RATE_LIMITED"
        if response.status_code != 200:
            logger.debug(f"API error {response.status_code} for {login}")
            return None
        data = response.json()
        if errors := data.get("errors"):
            msg = "; ".join(e.get("message", "") for e in errors)
            if "rate limit" in msg.lower():
                reset = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                if GLOBAL_TOKEN_MANAGER:
                    GLOBAL_TOKEN_MANAGER.mark_rate_limited(token, reset)
                return "RATE_LIMITED"
            logger.debug(f"GraphQL errors for {login}: {msg}")
            return None
        user = data.get("data", {}).get("user")
        if not user:
            return None
        bio = (user.get("bio") or "").strip()
        return {
            "github_id": int(user["databaseId"]),
            "github_name": user["login"],
            "followers_count": user["followers"]["totalCount"],
            "following_count": user["following"]["totalCount"],
            "sponsors_count": 0,
            "organizations_count": user["organizations"]["totalCount"],
            "company": (user.get("company") or "").strip() or None,
            "location": (user.get("location") or "").strip() or None,
            "bio": bio,
            "bio_length": len(bio)
        }
    except requests.exceptions.Timeout:
        logger.debug(f"Timeout for {login}")
        return None
    except Exception as e:
        logger.debug(f"Network error for {login}: {str(e)[:100]}")
        return None


USER_PROFILE_QUERY_BASIC = """
query($login: String!) {
user(login: $login) {
databaseId login company location bio
followers { totalCount }
following { totalCount }
organizations(first: 100) { totalCount }
}
}
"""


def _get_batch_users(batch_size: int) -> List[Tuple[int, str]]:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, github_name
        FROM users
        WHERE (bio IS NULL)
        AND last_updated < datetime('now', '-1 hour')
        ORDER BY
        CASE WHEN bio IS NULL THEN 0 ELSE 1 END,
        last_updated ASC
        LIMIT ?
        """, (batch_size,))
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        logger.error(f"DB error fetching batch: {e}")
        return []


def _bulk_update_profiles(results: List[Tuple[int, Optional[Dict]]]) -> int:
    if not results:
        return 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        updated = 0
        for user_id, profile_data in results:
            if profile_data is None:
                cursor.execute("UPDATE users SET bio = '', bio_length = 0, last_updated = ? WHERE id = ?", (now, user_id))
            else:
                cursor.execute("""
                UPDATE users SET
                followers_count = ?, following_count = ?, sponsors_count = ?,
                organizations_count = ?, company = ?, location = ?,
                bio = ?, bio_length = ?, last_updated = ?
                WHERE id = ?
                """, (
                    profile_data["followers_count"],
                    profile_data["following_count"],
                    profile_data["sponsors_count"],
                    profile_data["organizations_count"],
                    profile_data["company"],
                    profile_data["location"],
                    profile_data["bio"],
                    profile_data["bio_length"],
                    now,
                    user_id
                ))
            updated += cursor.rowcount
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        logger.error(f"DB bulk update failed: {e}")
        return 0


def enrich_user_profiles(batch_size: int = 100, max_workers: Optional[int] = None) -> int:
    if not GLOBAL_TOKEN_MANAGER:
        logger.error("TokenManager not initialized.")
        return 0

    if max_workers is None:
        max_workers = min(20, len(GLOBAL_TOKEN_MANAGER.tokens) * 3)

    user_batch = _get_batch_users(batch_size)
    if not user_batch:
        logger.info("No users need profile enrichment")
        return 0

    logger.info(f"Starting PARALLEL enrichment of {len(user_batch)} users "
                f"using {max_workers} workers and {len(GLOBAL_TOKEN_MANAGER.tokens)} tokens")

    results: List[Tuple[int, Optional[Dict]]] = []
    lock = threading.Lock()

    def process_user(user_id: int, username: str):
        max_attempts = len(GLOBAL_TOKEN_MANAGER.tokens) * 2
        attempts = 0
        while attempts < max_attempts:
            token, from_cooldown = GLOBAL_TOKEN_MANAGER.get_available_token()
            if not token:
                time.sleep(1)
                continue
            attempts += 1
            res = _fetch_single_profile(username, token)
            if res == "RATE_LIMITED":
                continue
            elif res is None:
                if attempts >= max_attempts:
                    with lock: results.append((user_id, None))
                    return
            else:
                with lock:
                    results.append((user_id, res))
                return
        with lock:
            results.append((user_id, None))

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_user, user_id, username)
            for user_id, username in user_batch
        ]
        concurrent.futures.wait(futures)

    updated = _bulk_update_profiles(results)
    elapsed = time.time() - start_time
    success = sum(1 for _, p in results if p is not None)
    failed = len(results) - success
    logger.info(
        f"Enrichment complete: {success} succeeded, {failed} failed | "
        f"Total: {len(results)} users | "
        f"Time: {elapsed:.1f}s ({len(results) / elapsed:.1f} users/sec) | "
        f"DB updated: {updated}"
    )
    if failed > 0:
        logger.warning(f"{failed} profiles failed (marked with empty bio). "
                       "Check logs for details. Consider increasing tokens or reducing batch size.")
    return success