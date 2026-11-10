from datetime import date, timedelta, time, datetime
from typing import List, Dict, Optional, Tuple
import requests
import time
from data_fetching.users_fetching.checkpoint import CheckpointManager
from data_storage.database import DatabaseManager
from utils.token_manager import GLOBAL_TOKEN_MANAGER
from utils.constants import PER_PAGE_GITHUB_LIMIT, GITHUB_SEARCH_LIMIT_API
from utils.logger import logger, log_block

SEARCH_USERS_QUERY = """
query ($query: String!, $first: Int!, $after: String) {
search(query: $query, type: USER, first: $first, after: $after) {
userCount
pageInfo {
endCursor
hasNextPage
}
edges {
cursor
node {
... on User {
login
databaseId
}
}
}
}
}
"""

USER_FOLLOWERS_QUERY = """
query ($login: String!) {
user(login: $login) {
followers {
totalCount
}
}
}
"""

GITHUB_EPOCH_DATE = date(2007, 1, 1)
MAX_SEARCH_RESULTS = GITHUB_SEARCH_LIMIT_API
FOLLOWER_BANDS: List[Tuple[int, int]] = [
    (10_000, 1_000_000_000),
    (1_000, 9_999),
    (100, 999),
    (10, 99),
    (1, 9),
]


class PopularUsersFinder:
    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json",})

    def _handle_rate_limit(self, token: str, response: requests.Response) -> None:
        reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
        current_time = int(time.time())
        wait_seconds = max(5, reset_time - current_time + 2)
        logger.warning(f"Rate limit exceeded on token {token[:8]}. Waiting {wait_seconds} seconds...")
        if self.token_manager:
            self.token_manager.mark_rate_limited(token, reset_time)
        time.sleep(wait_seconds)

    def _graphql(self, query: str, variables: Dict) -> Optional[Dict]:
        token, _ = self.token_manager.get_available_token() if self.token_manager else (None, False)
        if not token:
            logger.error("No tokens available for GraphQL request")
            return None

        try:
            response = self.session.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 403 and "rate limit" in response.text.lower():
                self._handle_rate_limit(token, response)
                return self._graphql(query, variables)
            if response.status_code != 200:
                logger.error(f"GitHub GraphQL error: {response.status_code} - {response.text}")
                return None
            payload = response.json()
            errors = payload.get("errors") or []
            if errors:
                messages = ", ".join(e.get("message", "") for e in errors)
                if "rate limit" in messages.lower():
                    self._handle_rate_limit(token, response)
                    return self._graphql(query, variables)
                logger.error(f"GitHub GraphQL errors: {messages}")
                return None
            return payload.get("data")
        except Exception as e:
            logger.error(f"Error calling GitHub GraphQL API: {e}")
            return None

    def fetch_page(self, search_query: str, cursor: Optional[str] = None,) -> Tuple[List[Dict], Optional[str], bool, int]:
        per_page = min(PER_PAGE_GITHUB_LIMIT, 100)
        logger.debug(f"Fetching GraphQL page: query='{search_query}', cursor={cursor}, per_page={per_page}")
        data = self._graphql(SEARCH_USERS_QUERY, {"query": search_query, "first": per_page, "after": cursor})
        if not data:
            logger.warning("No 'data' in GraphQL search response.")
            return [], None, False, 0

        search = data.get("search") or {}
        user_count = search.get("userCount") or 0
        edges = search.get("edges") or []
        users: List[Dict] = []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            login = node.get("login")
            github_id = node.get("databaseId")
            if login is None or github_id is None:
                logger.debug(f"Skipping node without login or databaseId: {node}")
                continue
            users.append({"login": login, "github_id": github_id})

        page_info = search.get("pageInfo") or {}
        end_cursor = page_info.get("endCursor")
        has_next_page = page_info.get("hasNextPage", False)
        return users, end_cursor, has_next_page, user_count

    def get_user_followers(self, login: str) -> Optional[int]:
        data = self._graphql(USER_FOLLOWERS_QUERY, {"login": login})
        if not data:
            return None
        user = data.get("user")
        if not user:
            logger.error(f"User {login} not found in GraphQL response.")
            return None
        followers = (user.get("followers") or {}).get("totalCount")
        if followers is None:
            logger.error(f"Followers field missing for user {login} in GraphQL response.")
            return None
        return followers

    @staticmethod
    def build_followers_qualifier(min_followers: int, max_followers: int) -> str:
        if max_followers >= 1_000_000_000:
            return f"followers:{min_followers}..1000000000"
        return f"followers:{min_followers}..{max_followers}"

    @staticmethod
    def build_created_qualifier(start_date: date, end_date: date) -> str:
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()
        return f"created:{start_str}..{end_str}"

    @staticmethod
    def build_search_query(min_followers: int, max_followers: int, start_date: date, end_date: date) -> str:
        followers_part = PopularUsersFinder.build_followers_qualifier(min_followers, max_followers)
        created_part = PopularUsersFinder.build_created_qualifier(start_date, end_date)
        return f"{followers_part} {created_part} sort:followers-desc"

    @staticmethod
    def get_last_non_null_user(users: List[Optional[Dict]]) -> Optional[Dict]:
        for user in reversed(users):
            if user is not None and user.get("github_id") is not None:
                return user
        return None

    @staticmethod
    def persist_users_to_db(db: DatabaseManager, users: List[Dict]) -> int:
        added_count = 0
        for user in users:
            try:
                created_at = datetime.now().isoformat()
                if user["github_id"] is None:
                    logger.warning(f"Skipping user {user['login']} because github_id is None")
                    continue
                db.get_or_create_user(user["github_id"], user["login"], created_at)
                added_count += 1
            except Exception as e:
                logger.error(f"Error adding user {user['login']}: {str(e)}")
        if added_count:
            logger.info(f"Persisted {added_count} users to database in this batch")
        return added_count

    def process_follower_band(self, db: DatabaseManager, min_followers: int, max_followers: int, band_index: int, stack: List[Tuple[date, date, int, int]],) -> None:
        total_added = 0
        processed_slices = 0
        while stack:
            start_date, end_date, f_min, f_max = stack.pop()
            search_query = self.build_search_query(f_min, f_max, start_date, end_date)
            logger.info(f"Processing slice: followers {f_min}..{f_max}, created {start_date}..{end_date}")
            users, cursor, has_next_page, user_count = self.fetch_page(search_query, cursor=None)

            if user_count == 0:
                logger.debug("Slice contains no users; skipping.")
                CheckpointManager.save_band_checkpoint(band_index, stack)
                continue

            if user_count > MAX_SEARCH_RESULTS:
                logger.debug(
                    f"Slice exceeded {MAX_SEARCH_RESULTS} users (userCount={user_count}). Splitting into smaller slices...")
                days = (end_date - start_date).days
                if days > 0:
                    mid = start_date + timedelta(days=days // 2)
                    left = (start_date, mid, f_min, f_max)
                    right = (mid + timedelta(days=1), end_date, f_min, f_max)
                    stack.append(left)
                    stack.append(right)
                else:
                    if f_min >= f_max:
                        logger.error(
                            f"Cannot split slice further: single day {start_date} with followers {f_min}..{f_max} still has userCount={user_count} > {MAX_SEARCH_RESULTS}. Some users may be skipped due to GitHub's 1000-result limit.")
                        CheckpointManager.save_band_checkpoint(band_index, stack)
                        continue
                    mid_f = f_min + (f_max - f_min) // 2
                    lower = (start_date, end_date, f_min, mid_f)
                    upper = (start_date, end_date, mid_f + 1, f_max)
                    stack.append(lower)
                    stack.append(upper)
                CheckpointManager.save_band_checkpoint(band_index, stack)
                continue

            processed_slices += 1
            logger.info(f"Slice userCount={user_count} fits under limit; fetching all pages.")
            last_user: Optional[Dict] = None
            if users:
                combined: List[Optional[Dict]] = [last_user] + users
                last_user = self.get_last_non_null_user(combined)
                total_added += self.persist_users_to_db(db, users)
            else:
                logger.warning("fetch_page returned empty user list for non-empty slice.")

            while has_next_page and cursor is not None:
                users, cursor, has_next_page, _ = self.fetch_page(search_query, cursor=cursor)
                if not users:
                    logger.debug("No more users in subsequent page.")
                    break
                combined = [last_user] + users
                last_user = self.get_last_non_null_user(combined)
                total_added += self.persist_users_to_db(db, users)

            if last_user:
                logger.info(
                    f"Last user in slice followers {f_min}..{f_max}, created {start_date}..{end_date}: {last_user['login']}")
            CheckpointManager.save_band_checkpoint(band_index, stack)

        logger.info(
            f"Finished follower band {min_followers}..{max_followers}: total users added/updated: {total_added}, slices processed: {processed_slices}")


def process_popular_users() -> None:
    if not GLOBAL_TOKEN_MANAGER:
        logger.critical("No tokens available for PopularUsersFinder")
        return

    checkpoint = CheckpointManager.load_band_checkpoint()
    if checkpoint:
        band_index, stack = checkpoint
        min_f, max_f = FOLLOWER_BANDS[band_index]
        log_block(
            f"Resuming follower band {band_index + 1}/{len(FOLLOWER_BANDS)}",
            f"Followers between {min_f} and {max_f if max_f < 1_000_000_000 else '∞'}",
        )
        finder = PopularUsersFinder(GLOBAL_TOKEN_MANAGER)
        db = DatabaseManager()
        finder.process_follower_band(db, min_f, max_f, band_index, stack)
        start_band_index = band_index + 1
    else:
        start_band_index = 0

    for band_idx in range(start_band_index, len(FOLLOWER_BANDS)):
        min_f, max_f = FOLLOWER_BANDS[band_idx]
        today = date.today()
        log_block(
            f"Processing follower band {band_idx + 1}/{len(FOLLOWER_BANDS)}",
            f"Followers between {min_f} and {max_f if max_f < 1_000_000_000 else '∞'}",
        )
        finder = PopularUsersFinder(GLOBAL_TOKEN_MANAGER)
        db = DatabaseManager()
        stack = [(GITHUB_EPOCH_DATE, today, min_f, max_f)]
        CheckpointManager.save_band_checkpoint(band_idx, stack)
        finder.process_follower_band(db, min_f, max_f, band_idx, stack)

    CheckpointManager.clear_band_checkpoint()
    logger.info("Completed processing of all follower bands. Checkpoint cleared.")


if __name__ == "__main__":
    try:
        process_popular_users()
    except KeyboardInterrupt:
        logger.info("Stopped by user (KeyboardInterrupt). Bye!")