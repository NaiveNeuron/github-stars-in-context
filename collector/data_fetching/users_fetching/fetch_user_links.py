import re
import time
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlparse
from utils.logger import logger
from utils.paths import DB_PATH
from utils.token_manager import GLOBAL_TOKEN_MANAGER
from utils.secrets import GITHUB_TOKENS

GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_PROFILE_URL = "https://github.com/{login}"

USER_PROFILE_WITH_ALL_LINKS_QUERY = """
query($login: String!) {
    user(login: $login) {
        databaseId
        login
        websiteUrl
        twitterUsername
        bio
        company
        location
        email
        organizations(first: 100) {
            nodes {
                websiteUrl
                login
            }
        }
        repositories(first: 100, ownerAffiliations: OWNER, orderBy: {field: STARGAZERS, direction: DESC}) {
            nodes {
                homepageUrl
                url
            }
        }
    }
}
"""


def extract_urls_from_text(text: str) -> List[str]:
    if not text:
        return []
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    found = re.findall(url_pattern, text)
    return list(set(found))


def extract_emails_from_text(text: str) -> List[str]:
    if not text:
        return []
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    found = re.findall(email_pattern, text)
    return list(set(found))


def normalize_url(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url.startswith('@') or url.startswith('mailto:'):
        return None
    if not url.startswith(('http://', 'https://')):
        url = f"https://{url}"
    return url


def build_social_url(platform: str, username: str) -> Optional[str]:
    if not username:
        return None
    username = username.lstrip('@')
    platforms = {
        'twitter': f"https://twitter.com/{username}",
        'x': f"https://x.com/{username}",
        'instagram': f"https://instagram.com/{username}",
        'mastodon': f"https://mastodon.social/{username}",
        'linkedin': f"https://linkedin.com/in/{username}",
        'facebook': f"https://facebook.com/{username}",
        'youtube': f"https://youtube.com/@{username}",
        'tiktok': f"https://tiktok.com/@{username}",
        'pinterest': f"https://pinterest.com/{username}",
        'medium': f"https://medium.com/@{username}",
        'dev': f"https://dev.to/{username}",
        'hashnode': f"https://hashnode.com/@{username}",
        'github': f"https://github.com/{username}",
    }
    return platforms.get(platform)


def _parse_mastodon_from_location(location: str) -> Optional[str]:
    if not location:
        return None
    mastodon_pattern = r'@(\w+)@([\w\.]+\.[\w]+)'
    match = re.search(mastodon_pattern, location)
    if match:
        username, instance = match.groups()
        return f"https://{instance}/@{username}"
    return None


def _parse_social_from_bio(bio: str) -> List[Dict[str, str]]:
    links = []
    if not bio:
        return links

    twitter_patterns = [
        r'twitter\.com/([a-zA-Z0-9_]+)',
        r'x\.com/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)\s*(?:on\s+)?(?:twitter|x)',
    ]
    for pattern in twitter_patterns:
        matches = re.findall(pattern, bio, re.IGNORECASE)
        for username in matches:
            links.append({'url': f"https://twitter.com/{username}", 'type': 'twitter'})
            links.append({'url': f"https://x.com/{username}", 'type': 'x'})

    instagram_patterns = [
        r'instagram\.com/([a-zA-Z0-9_.]+)',
        r'@([a-zA-Z0-9_.]+)\s*(?:on\s+)?instagram',
    ]
    for pattern in instagram_patterns:
        matches = re.findall(pattern, bio, re.IGNORECASE)
        for username in matches:
            links.append({'url': f"https://instagram.com/{username}", 'type': 'instagram'})

    linkedin_patterns = [
        r'linkedin\.com/in/([a-zA-Z0-9_-]+)',
        r'linkedin\.com/company/([a-zA-Z0-9_-]+)',
    ]
    for pattern in linkedin_patterns:
        matches = re.findall(pattern, bio, re.IGNORECASE)
        for username in matches:
            links.append({'url': f"https://linkedin.com/in/{username}", 'type': 'linkedin'})

    return links


def _fetch_user_profile_html(login: str) -> Optional[str]:
    try:
        token, _ = GLOBAL_TOKEN_MANAGER.get_available_token() if GLOBAL_TOKEN_MANAGER else (None, False)
        headers = {
            "User-Agent": "git-h-index/1.0",
            "Accept": "text/html",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.get(
            GITHUB_PROFILE_URL.format(login=login),
            headers=headers,
            timeout=(10, 30)
        )
        if response.status_code == 200:
            return response.text
        return None
    except Exception as e:
        logger.debug(f"Failed to fetch HTML profile for {login}: {str(e)[:100]}")
        return None


def _extract_links_from_html(html: str, login: str) -> List[Dict[str, str]]:
    links = []
    if not html:
        return links

    social_patterns = [
        (r'href="(https?://twitter\.com/[^"]+)"', 'twitter'),
        (r'href="(https?://x\.com/[^"]+)"', 'x'),
        (r'href="(https?://instagram\.com/[^"]+)"', 'instagram'),
        (r'href="(https?://mastodon\.social/[^"]+)"', 'mastodon'),
        (r'href="(https?://[\w\.]+/@\w+)"', 'mastodon'),
        (r'href="(https?://linkedin\.com/[^"]+)"', 'linkedin'),
        (r'href="(https?://youtube\.com/[^"]+)"', 'youtube'),
        (r'href="(https?://tiktok\.com/[^"]+)"', 'tiktok'),
        (r'href="(https?://medium\.com/[^"]+)"', 'medium'),
        (r'href="(https?://dev\.to/[^"]+)"', 'dev'),
        (r'href="(https?://hashnode\.com/[^"]+)"', 'hashnode'),
        (r'href="(https?://[\w\-]+\.(?:com|org|net|io|dev|app|co)/[^"]*)"', 'website'),
    ]

    for pattern, link_type in social_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for url in matches:
            if not any(l['url'] == url for l in links):
                links.append({'url': url, 'type': link_type})

    return links


def _fetch_user_links_data(login: str, token: str) -> Optional[Dict]:
    try:
        response = requests.post(
            GRAPHQL_URL,
            json={
                "query": USER_PROFILE_WITH_ALL_LINKS_QUERY,
                "variables": {"login": login}
            },
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

        return {
            "github_id": int(user["databaseId"]),
            "github_name": user["login"],
            "website_url": user.get("websiteUrl"),
            "twitter_username": user.get("twitterUsername"),
            "bio": user.get("bio") or "",
            "company": user.get("company") or "",
            "location": user.get("location") or "",
            "email": user.get("email"),
            "organizations": user.get("organizations", {}).get("nodes", []),
            "repositories": user.get("repositories", {}).get("nodes", [])
        }

    except requests.exceptions.Timeout:
        logger.debug(f"Timeout for {login}")
        return None
    except Exception as e:
        logger.debug(f"Network error for {login}: {str(e)[:100]}")
        return None


def _process_user_links(user_data: Dict, fetch_html: bool = True) -> List[Dict[str, str]]:
    links = []
    seen_urls = set()

    def add_link(url: str, link_type: str):
        if not url:
            return
        if not url.startswith(('http://', 'https://', 'mailto:')):
            return
        try:
            parsed = urlparse(url)
            if parsed.netloc.endswith('github.com'):
                return
        except Exception:
            pass
        if url not in seen_urls:
            seen_urls.add(url)
            links.append({'url': url, 'type': link_type})
            logger.debug(f"Added link for {user_data['github_name']}: {url} ({link_type})")

    if user_data.get("website_url"):
        add_link(user_data["website_url"], "website")

    if user_data.get("twitter_username"):
        twitter_url = build_social_url('twitter', user_data["twitter_username"])
        add_link(twitter_url, "twitter")
        x_url = build_social_url('x', user_data["twitter_username"])
        add_link(x_url, "x")

    if user_data.get("email"):
        add_link(f"mailto:{user_data['email']}", "email")

    bio_links = extract_urls_from_text(user_data.get("bio", ""))
    for url in bio_links:
        add_link(url, "bio_extracted")

    bio_emails = extract_emails_from_text(user_data.get("bio", ""))
    for email in bio_emails:
        add_link(f"mailto:{email}", "email_bio")

    company_links = extract_urls_from_text(user_data.get("company", ""))
    for url in company_links:
        add_link(url, "company_extracted")

    mastodon_url = _parse_mastodon_from_location(user_data.get("location", ""))
    if mastodon_url:
        add_link(mastodon_url, "mastodon")

    location_links = extract_urls_from_text(user_data.get("location", ""))
    for url in location_links:
        add_link(url, "location_extracted")

    social_links = _parse_social_from_bio(user_data.get("bio", ""))
    for link in social_links:
        add_link(link['url'], link['type'])

    for org in user_data.get("organizations", []):
        if org.get("websiteUrl"):
            add_link(org["websiteUrl"], "organization")

    for repo in user_data.get("repositories", []):
        if repo.get("homepageUrl") and repo["homepageUrl"] != user_data.get("website_url"):
            add_link(repo["homepageUrl"], "repository_homepage")

    if fetch_html and len(links) < 5:
        logger.debug(f"Fetching HTML profile for {user_data['github_name']} to find more links")
        html = _fetch_user_profile_html(user_data["github_name"])
        if html:
            html_links = _extract_links_from_html(html, user_data["github_name"])
            for link in html_links:
                add_link(link['url'], link['type'])

    logger.info(f"Extracted {len(links)} links for {user_data['github_name']}: {[l['url'] for l in links]}")
    return links


def parse_user_links_batch(batch_size: int = 100, max_workers: Optional[int] = None, days_since_update: int = 30, fetch_html_fallback: bool = True) -> int:
    from data_storage.database import DatabaseManager
    from config import FAILED_FETCH_MARKER_DATE
    if not GLOBAL_TOKEN_MANAGER:
        logger.error("TokenManager not initialized.")
        return 0
    if max_workers is None:
        max_workers = min(14, len(GLOBAL_TOKEN_MANAGER.tokens) * 2)
    db = DatabaseManager()
    users_to_parse = db.get_users_needing_links_parse(batch_size, days_since_update)
    if not users_to_parse:
        logger.info("No users need links parsing")
        return 0
    logger.info(
        f"Starting links parsing for {len(users_to_parse)} users "
        f"using {max_workers} workers and {len(GLOBAL_TOKEN_MANAGER.tokens)} tokens"
    )
    import concurrent.futures
    import threading
    results: List[Tuple[int, bool, int, str]] = []
    lock = threading.Lock()

    def process_user(user_id: int, username: str):
        from config import FAILED_FETCH_MARKER_DATE
        max_attempts = len(GLOBAL_TOKEN_MANAGER.tokens) * 2
        max_rate_limit_waits = 3
        rate_limit_waits = 0
        attempts = 0
        rate_limit_count = 0

        while attempts < max_attempts:
            token, from_cooldown = GLOBAL_TOKEN_MANAGER.get_available_token()
            if not token:
                with GLOBAL_TOKEN_MANAGER.lock:
                    if GLOBAL_TOKEN_MANAGER.token_cooldowns:
                        earliest_reset = min(GLOBAL_TOKEN_MANAGER.token_cooldowns.values())
                        wait_seconds = max(5, earliest_reset - time.time())
                        if wait_seconds > 300 and rate_limit_waits >= max_rate_limit_waits:
                            logger.debug(
                                f"All tokens rate-limited for {wait_seconds:.0f}s, "
                                f"skipping user {username} (will retry in next batch)"
                            )
                            with lock:
                                results.append((user_id, False, 0, "rate_limit_skip"))
                            return
                        if wait_seconds > 0 and wait_seconds <= 60:
                            logger.debug(
                                f"Waiting {wait_seconds:.0f}s for token reset (user: {username}, "
                                f"wait #{rate_limit_waits + 1})"
                            )
                            time.sleep(min(wait_seconds, 30))
                            rate_limit_waits += 1
                            continue
                        else:
                            time.sleep(1)
                            continue
                    else:
                        time.sleep(1)
                        continue

            attempts += 1
            user_data = _fetch_user_links_data(username, token)

            if user_data == "RATE_LIMITED":
                rate_limit_count += 1
                logger.debug(f"Token rate-limited during fetch for {username}, trying next token")
                attempts -= 1
                continue
            elif user_data is None:
                if attempts >= max_attempts:
                    logger.warning(
                        f"Failed to fetch links for user {username} (ID: {user_id}) "
                        f"after {attempts} attempts - marking as parse failed"
                    )
                    db.mark_links_parse_failed(user_id, FAILED_FETCH_MARKER_DATE)
                    with lock:
                        results.append((user_id, False, 0, "parse_failed"))
                    return
                else:
                    continue
            else:
                links = _process_user_links(user_data, fetch_html=fetch_html_fallback)
                db.clear_user_links(user_id)
                db.add_user_links(user_id, links)
                db.mark_links_parsed(user_id)
                with lock:
                    results.append((user_id, True, len(links), "success"))
                return

        if rate_limit_count >= max_attempts - 1:
            logger.debug(
                f"User {username} (ID: {user_id}) skipped due to rate limiting "
                f"({rate_limit_count} rate limits) - will retry in next batch"
            )
            with lock:
                results.append((user_id, False, 0, "rate_limit_skip"))
            return

        logger.warning(f"Exhausted all attempts for user {username} (ID: {user_id}) - marking as parse failed")
        db.mark_links_parse_failed(user_id, FAILED_FETCH_MARKER_DATE)
        with lock:
            results.append((user_id, False, 0, "exhausted_attempts"))

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_user, user["id"], user["github_name"]) for user in users_to_parse]
        concurrent.futures.wait(futures)

    elapsed = time.time() - start_time
    success = sum(1 for _, success, _, _ in results if success)
    total_links = sum(count for _, _, count, _ in results)
    rate_limit_skips = sum(1 for _, _, _, reason in results if reason == "rate_limit_skip")
    parse_failures = sum(1 for _, _, _, reason in results if reason == "parse_failed")
    exhausted_attempts = sum(1 for _, _, _, reason in results if reason == "exhausted_attempts")

    logger.info(
        f"Links parsing complete: {success} succeeded, {parse_failures + exhausted_attempts} failed, "
        f"{rate_limit_skips} skipped (rate limit) | "
        f"Total: {len(results)} users | "
        f"Time: {elapsed:.1f}s ({len(results) / elapsed:.1f} users/sec) | "
        f"Total links extracted: {total_links}"
    )
    if rate_limit_skips > 0:
        logger.warning(f"{rate_limit_skips} users were skipped due to rate limiting. They will be retried in the ""next batch run.")
    return success

def reset_links_parse_timestamps(keep_failed_users: bool = True, failed_marker_date: str = "6200-01-01T00:00:00") -> int:
    from data_storage.database import DatabaseManager

    db = DatabaseManager()
    reset_count = db.reset_links_parse_timestamps(keep_failed_users=keep_failed_users, failed_marker_date=failed_marker_date)
    logger.info(f"Reset links_parsed_at for {reset_count} users")
    return reset_count


def reset_rate_limit_skipped_users() -> int:
    from data_storage.database import DatabaseManager
    import sqlite3

    db = DatabaseManager()

    try:
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users
                SET links_parsed_at = NULL
                WHERE links_parsed_at IS NULL
                AND last_updated != ?
            """, ("6200-01-01T00:00:00",))
            reset_count = cursor.rowcount
            conn.commit()
            logger.info(f"Reset {reset_count} rate-limit-skipped users for retry")
            return reset_count
    except sqlite3.Error as e:
        logger.error(f"Error resetting rate-limit-skipped users: {str(e)}")
        return 0



if __name__ == "__main__":
    logger.info("STARTING LINKS PARSER (ALL LINKS)")
    while True:
        parse_user_links_batch(batch_size=300, days_since_update=60, fetch_html_fallback=True)