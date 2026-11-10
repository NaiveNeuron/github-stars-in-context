import json
import sqlite3
import requests
import time
import concurrent.futures
from typing import List, Dict, Optional
from utils.logger import logger, log_block
from utils.token_manager import GLOBAL_TOKEN_MANAGER
from utils.secrets import GITHUB_TOKENS

GRAPHQL_URL = "https://api.github.com/graphql"
QUERY = """
query GetUserRepos($login: String!, $cursor: String) {
user(login: $login) {
databaseId
createdAt
repositories(
first: 100
after: $cursor
ownerAffiliations: OWNER
privacy: PUBLIC
isFork: false
orderBy: {field: CREATED_AT, direction: ASC}
) {
totalCount
pageInfo {
endCursor
hasNextPage
}
nodes {
name
createdAt
databaseId
stargazerCount
}
}
}
}
"""


def _is_rate_limit_error(response: requests.Response) -> bool:
    if response.status_code == 403:
        return True
    try:
        data = response.json()
        if isinstance(data, dict) and "errors" in data:
            for error in data["errors"]:
                if "rate limit" in str(error).lower():
                    return True
    except:
        pass
    return False


def fetch_user_and_repos(username, token):
    logger.info(f"Starting repository fetch for user: {username}")
    logger.debug(f"Using pagination with first: 100 repositories per page")
    all_repos = []
    cursor = None
    has_next_page = True
    page_count = 0
    user_id = None
    user_created_at = None

    while has_next_page:
        page_count += 1
        logger.debug(f"Fetching page {page_count} (cursor: {cursor if cursor else 'initial'})")
        variables = {"login": username, "cursor": cursor}

        response = requests.post(
            GRAPHQL_URL,
            json={"query": QUERY, "variables": variables},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )

        if response.status_code != 200:
            if GLOBAL_TOKEN_MANAGER and _is_rate_limit_error(response):
                reset = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                GLOBAL_TOKEN_MANAGER.mark_rate_limited(token, reset)
                raise Exception(f"RATE_LIMIT: Token {token[:8]} limited")

            error = response.json().get('errors', [{'message': 'Unknown error'}])[0]
            error_msg = f"GraphQL Error {response.status_code}: {error['message']}"
            logger.error(error_msg)
            raise Exception(error_msg)

        data = response.json()
        if 'errors' in data:
            error_details = json.dumps(data['errors'], indent=2)
            logger.error(f"GraphQL API Error: {error_details}")
            raise Exception(f"GraphQL API Error: {error_details}")

        user_data = data['data']['user']
        if not user_data:
            error_msg = f"User '{username}' not found or token lacks permissions"
            logger.error(error_msg)
            raise Exception(error_msg)

        if page_count == 1:
            user_id = user_data['databaseId']
            user_created_at = user_data['createdAt']
            logger.info(f"User ID: {user_id}, Account created: {user_created_at}")

        repos = user_data['repositories']['nodes']
        repo_count = len(repos)
        all_repos.extend(repos)
        logger.info(f"Fetched {repo_count} repositories from page {page_count}")

        if repo_count > 0:
            logger.debug(f"First repo on page: {repos[0]['name']} ({repos[0]['stargazerCount']} stars), "
                         f"Last repo on page: {repos[-1]['name']} ({repos[-1]['stargazerCount']} stars)")

        page_info = user_data['repositories']['pageInfo']
        has_next_page = page_info['hasNextPage']
        cursor = page_info['endCursor']

        if has_next_page:
            logger.debug(f"Pagination continues (next cursor: {cursor})")
        else:
            logger.info(f"Pagination complete. Final cursor: {cursor}")

    total_repos = len(all_repos)
    logger.info(f"Successfully fetched {total_repos} repositories CREATED BY {username}")
    if total_repos > 0:
        logger.debug(f"First repository: {all_repos[0]['name']} ({all_repos[0]['stargazerCount']} stars), "
                     f"Last repository: {all_repos[-1]['name']} ({all_repos[-1]['stargazerCount']} stars)")

    return {"user_id": user_id, "user_created_at": user_created_at, "repositories": all_repos}


def save_github_user_to_db(username: str) -> None:
    logger.info(f"STARTING DATABASE SAVE PROCESS FOR {username}")
    try:
        from data_storage.database import DatabaseManager
        from config import AMOUNT_OF_DAYS
        from datetime import datetime, timedelta
        db = DatabaseManager()
        logger.debug("Database manager initialized")
        user_data = db.get_user_data(username)

        if user_data:
            last_updated = datetime.fromisoformat(user_data["last_updated"])
            time_since_update = datetime.now() - last_updated
            if time_since_update < timedelta(days=AMOUNT_OF_DAYS):
                logger.info(f"User {username} already in database and updated {time_since_update.days} days ago "
                            f"(threshold: {AMOUNT_OF_DAYS} days). Skipping update.")
                return

        logger.info(f"Saving GitHub user data for {username} to database")
        token = GITHUB_TOKENS[0] if GITHUB_TOKENS else ""
        if GLOBAL_TOKEN_MANAGER:
            token, _ = GLOBAL_TOKEN_MANAGER.get_available_token()

        if not token:
            raise Exception("No GitHub tokens available")

        user_data = db.update_user_from_github(username, token)
        log_block(f"Successfully saved data for {username}",
                  f"Total repositories: {len(user_data['repositories'])}",
                  f"Current h-index: {user_data['h_index']}",
                  f"Last updated: {user_data['last_updated']}")
    except Exception as e:
        logger.error(f"Failed to save GitHub user {username} to database: {str(e)}")
        logger.debug("Exception details:", exc_info=True)
        raise


def fetch_next_user_repositories() -> bool:
    logger.info("Looking for next user to process repositories for")
    try:
        from data_storage.database import DatabaseManager
        from config import FAILED_FETCH_MARKER_DATE
        db = DatabaseManager()
        user = db.get_next_user_for_repository_fetch()
        if not user:
            logger.info("No users need repository fetching")
            return False

        username = user["github_name"]
        logger.info(f"Processing repositories for user: {username}")

        token = GITHUB_TOKENS[0] if GITHUB_TOKENS else ""
        if GLOBAL_TOKEN_MANAGER:
            token, _ = GLOBAL_TOKEN_MANAGER.get_available_token()
            if not token:
                logger.warning("No tokens available, waiting...")
                time.sleep(5)
                return True

        user_data = db.update_user_from_github(username, token)
        log_block(f"Successfully processed repositories for {username}",
                  f"Total repositories: {len(user_data['repositories'])}",
                  f"Current h-index: {user_data['h_index']}",
                  f"Last updated: {user_data['last_updated']}")
        return True
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to process next user for repository fetching: {error_msg}")
        logger.debug("Exception details:", exc_info=True)

        is_graphql_rate_limit = "RATE_LIMIT" in error_msg or "rate limit" in error_msg.lower()
        if is_graphql_rate_limit:
            logger.warning("GitHub GraphQL rate limit exceeded. Waiting 30 seconds before retrying...")
            time.sleep(30)
            return True

        if 'user' in locals() and user:
            try:
                db.mark_repositories_fetch_failed(user["id"], FAILED_FETCH_MARKER_DATE)
                logger.info(f"Marked user {username} (ID: {user['id']}) as having repository fetch failed")
                return True
            except Exception as db_error:
                logger.error(
                    f"Failed to update repository fetch status for user {user['github_name']}: {str(db_error)}")
                return False
        return False


def _process_single_user(username: str, user_id: int, token: str) -> Dict:
    logger.debug(f"Thread started for user: {username}")
    try:
        from data_storage.database import DatabaseManager
        db = DatabaseManager()
        user_data = db.update_user_from_github(username, token)
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET repositories_fetched = 1 WHERE id = ?", (user_id,))
            conn.commit()
        logger.info(f"Thread completed for user: {username}")
        return user_data
    except Exception as e:
        logger.error(f"Thread failed for user {username}: {str(e)}")
        if "RATE_LIMIT" in str(e):
            raise e
        try:
            from data_storage.database import DatabaseManager
            db = DatabaseManager()
            db.reset_repository_fetch_status(user_id)
        except Exception as reset_error:
            logger.error(f"Failed to reset repository fetch status for user {username}: {str(reset_error)}")
        raise


def process_batch_of_users(batch_size: int = 10) -> bool:
    if not GLOBAL_TOKEN_MANAGER:
        logger.error("TokenManager not initialized. Cannot process batch.")
        return False

    logger.info(f"Starting parallel processing for up to {batch_size} users")
    try:
        from data_storage.database import DatabaseManager
        from config import FAILED_FETCH_MARKER_DATE
        import time

        db = DatabaseManager()
        users_to_process = db.get_users_for_repository_fetch(batch_size)

        if not users_to_process:
            logger.info("No users need repository fetching")
            return False

        logger.info(f"Processing {len(users_to_process)} users in parallel: "
                    f"{[user['github_name'] for user in users_to_process]}")

        while True:
            token, _ = GLOBAL_TOKEN_MANAGER.get_available_token()

            if not token:
                with GLOBAL_TOKEN_MANAGER.lock:
                    if GLOBAL_TOKEN_MANAGER.token_cooldowns:
                        earliest_reset = min(GLOBAL_TOKEN_MANAGER.token_cooldowns.values())
                        wait_seconds = max(5, earliest_reset - time.time())
                    else:
                        wait_seconds = 5

                if wait_seconds > 300:
                    logger.critical(f"All tokens rate limited. Waiting {wait_seconds // 60} minutes...")
                else:
                    logger.warning(f"All tokens rate limited. Waiting {wait_seconds} seconds...")
                time.sleep(wait_seconds)
                continue

            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_user = {
                    executor.submit(
                        _process_single_user,
                        user["github_name"],
                        user["id"],
                        token
                    ): user
                    for user in users_to_process
                }

                users_to_retry = []
                rate_limit_errors = 0

                for future in concurrent.futures.as_completed(future_to_user):
                    user = future_to_user[future]
                    try:
                        user_data = future.result()
                        log_block(f"Successfully processed repositories for {user['github_name']}",
                                  f"Total repositories: {len(user_data['repositories'])}",
                                  f"Current h-index: {user_data['h_index']}",
                                  f"Last updated: {user_data['last_updated']}")
                    except Exception as e:
                        error_msg = str(e)
                        logger.error(f"Failed to process user {user['github_name']}: {error_msg}")

                        if "RATE_LIMIT" in error_msg or "rate limit" in error_msg.lower():
                            rate_limit_errors += 1
                            users_to_retry.append(user)
                            GLOBAL_TOKEN_MANAGER.mark_rate_limited(token, int(time.time() + 60))
                        else:
                            try:
                                db.mark_repositories_fetch_failed(user["id"], FAILED_FETCH_MARKER_DATE)
                                logger.info(f"Marked user {user['github_name']} (ID: {user['id']}) as failed")
                            except Exception as db_error:
                                logger.error(f"Failed to update status for user {user['github_name']}: {str(db_error)}")

                if rate_limit_errors == len(users_to_process):
                    logger.error(
                        f"All {len(users_to_process)} users failed due to rate limits with token {token[:8]}...")
                    continue

                return len(users_to_process) == batch_size
    except Exception as e:
        logger.critical(f"Critical error in parallel processing: {str(e)}", exc_info=True)
        return True