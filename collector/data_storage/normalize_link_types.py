import re
import sqlite3
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from functools import lru_cache
from utils.logger import logger, log_block
from utils.paths import DB_PATH


@dataclass(frozen=True)
class PlatformConfig:
    name: str
    patterns: List[re.Pattern]
    priority: int


class LinkTypeNormalizer:
    _RAW_PATTERNS: Dict[str, List[str]] = {
        'telegram': [
            r't\.me/[\w]+',
            r'telegram\.me/[\w]+',
            r'telegram\.dog/[\w]+',
            r'telegram\.org/[\w]+',
        ],
        'twitter': [
            r'twitter\.com/[\w]+',
            r'x\.com/[\w]+',
            r'twitter\.com/#!/[\w]+',
        ],
        'instagram': [
            r'instagram\.com/[\w]+',
            r'instagr\.am/[\w]+',
            r'www\.instagram\.com/[\w]+',
        ],
        'youtube': [
            r'youtube\.com/[\w]+',
            r'youtu\.be/[\w]+',
            r'www\.youtube\.com/[\w]+',
        ],
        'linkedin': [
            r'linkedin\.com/in/[\w\-]+',
            r'linkedin\.com/company/[\w\-]+',
            r'linkedin\.com/pub/[\w\-]+',
            r'www\.linkedin\.com/[\w\-]+',
        ],
        'mastodon': [
            r'mastodon\.social/@\w+',
            r'mastodon\.[\w\.]+/@\w+',
            r'[\w\-]+\.mastodon\.[\w]+/@\w+',
        ],
        'facebook': [
            r'facebook\.com/[\w]+',
            r'fb\.com/[\w]+',
            r'www\.facebook\.com/[\w]+',
        ],
        'tiktok': [
            r'tiktok\.com/@\w+',
            r'tiktok\.com/[\w]+',
            r'www\.tiktok\.com/[\w]+',
        ],
        'medium': [
            r'medium\.com/[@\w\-]+',
            r'[\w]+\.medium\.com',
            r'www\.medium\.com/[@\w\-]+',
        ],
        'dev_to': [
            r'dev\.to/[\w]+',
            r'www\.dev\.to/[\w]+',
        ],
        'hashnode': [
            r'hashnode\.com/[@\w]+',
            r'[\w]+\.hashnode\.dev',
            r'[\w]+\.hashnode\.com',
        ],
        'github': [
            r'github\.com/[\w]+',
            r'www\.github\.com/[\w]+',
        ],
        'gitlab': [
            r'gitlab\.com/[\w]+',
            r'www\.gitlab\.com/[\w]+',
        ],
        'stackoverflow': [
            r'stackoverflow\.com/users/[\w]+',
            r'stackoverflow\.com/[\w]+',
        ],
        'website': [
            r'https?://[\w\-]+\.(?:com|org|net|io|dev|app|co|me|info|biz|ru|de|fr|jp|cn|in|br|au|uk|ca|it|es|nl|pl|se|no|fi|dk|cz|sk|hu|ro|bg|hr|si|rs|ua|by|kz|uz|az|ge|am|md|lt|lv|ee)(/[\w\-\.]*)?',
        ],
        'email': [
            r'mailto:[\w\.\-]+@[\w\.\-]+\.[\w]+',
        ],
    }

    _PRIORITY_MAP: Dict[str, int] = {
        'telegram': 1,
        'twitter': 2,
        'instagram': 3,
        'youtube': 4,
        'linkedin': 5,
        'mastodon': 6,
        'facebook': 7,
        'tiktok': 8,
        'medium': 9,
        'dev_to': 10,
        'hashnode': 11,
        'github': 12,
        'gitlab': 13,
        'stackoverflow': 14,
        'website': 99,
        'email': 100,
        'unknown': 999,
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        self._platform_configs: Dict[str, PlatformConfig] = {}
        self._compile_patterns()
        self.stats = {
            'total_links': 0,
            'unknown_links': 0,
            'updated_links': 0,
            'unchanged_links': 0,
            'by_platform': {},
        }

    def _compile_patterns(self) -> None:
        for platform, pattern_strings in self._RAW_PATTERNS.items():
            compiled = [
                re.compile(pattern, re.IGNORECASE)
                for pattern in pattern_strings
            ]
            priority = self._PRIORITY_MAP.get(platform, 999)
            self._platform_configs[platform] = PlatformConfig(
                name=platform,
                patterns=compiled,
                priority=priority
            )

    @lru_cache(maxsize=1024)
    def _detect_platform_cached(self, url: str) -> str:
        if not url:
            return 'unknown'

        url_lower = url.lower().strip()
        best_match: Optional[Tuple[str, int]] = None

        for platform, config in self._platform_configs.items():
            for pattern in config.patterns:
                if pattern.search(url_lower):
                    if best_match is None or config.priority < best_match[1]:
                        best_match = (platform, config.priority)
                    break

        return best_match[0] if best_match else 'unknown'

    def _detect_platform(self, url: str) -> str:
        return self._detect_platform_cached(url)

    def clear_cache(self) -> None:
        self._detect_platform_cached.cache_clear()

    def get_available_platforms(self) -> List[str]:
        return list(self._platform_configs.keys())

    def get_platform_priority(self, platform: str) -> int:
        config = self._platform_configs.get(platform)
        return config.priority if config else 999

    def _get_all_links_count(self) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_links")
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            raise

    def _get_unknown_links_count(self) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM user_links WHERE link_type = 'unknown'")
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            raise

    def _get_current_distribution(self) -> Dict[str, int]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT link_type, COUNT(*) as count
                FROM user_links
                GROUP BY link_type
                ORDER BY count DESC
            """)
            results = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()
            return results
        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            raise

    def normalize_all_links(self, dry_run: bool = False) -> Dict:
        total_links = self._get_all_links_count()
        unknown_links = self._get_unknown_links_count()
        log_block(
            "LINK TYPE NORMALIZATION START",
            f"Total links in database: {total_links:,}",
            f"Links with type='unknown': {unknown_links:,}",
            f"Dry run: {dry_run}",
        )

        if total_links == 0:
            logger.warning("No links found in database")
            return self.stats

        batch_size = 1000
        offset = 0
        processed = 0

        logger.info(f"Processing links in batches of {batch_size}...")

        while True:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, url, link_type
                    FROM user_links
                    ORDER BY id ASC
                    LIMIT ? OFFSET ?
                """, (batch_size, offset))
                links = cursor.fetchall()
                conn.close()

                if not links:
                    logger.info(f"No more links to process at offset {offset}")
                    break

                self.stats['total_links'] += len(links)
                updates_to_apply = []

                for link_id, url, current_type in links:
                    detected_type = self._detect_platform(url)

                    if current_type == 'unknown' and detected_type != 'unknown':
                        self.stats['updated_links'] += 1
                        if 'unknown' not in self.stats['by_platform']:
                            self.stats['by_platform']['unknown'] = {'changed_to': {}}
                        if detected_type not in self.stats['by_platform']['unknown']['changed_to']:
                            self.stats['by_platform']['unknown']['changed_to'][detected_type] = 0
                        self.stats['by_platform']['unknown']['changed_to'][detected_type] += 1
                        if not dry_run:
                            updates_to_apply.append((link_id, detected_type))

                    elif current_type != detected_type and current_type != 'unknown':
                        if detected_type in ['telegram', 'twitter', 'instagram', 'youtube', 'linkedin']:
                            self.stats['updated_links'] += 1
                            if current_type not in self.stats['by_platform']:
                                self.stats['by_platform'][current_type] = {'changed_to': {}}
                            if detected_type not in self.stats['by_platform'][current_type]['changed_to']:
                                self.stats['by_platform'][current_type]['changed_to'][detected_type] = 0
                            self.stats['by_platform'][current_type]['changed_to'][detected_type] += 1
                            if not dry_run:
                                updates_to_apply.append((link_id, detected_type))
                        else:
                            self.stats['unchanged_links'] += 1
                    else:
                        self.stats['unchanged_links'] += 1

                if not dry_run and updates_to_apply:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.executemany("""
                        UPDATE user_links
                        SET link_type = ?
                        WHERE id = ?
                    """, [(new_type, link_id) for link_id, new_type in updates_to_apply])
                    conn.commit()
                    conn.close()
                    logger.info(f"Batch complete: Updated {len(updates_to_apply)} links (offset: {offset})")

                processed += len(links)
                offset += batch_size
                logger.debug(
                    f"Progress: {processed:,}/{total_links:,} links processed ({processed / max(total_links, 1) * 100:.1f}%)")

            except sqlite3.Error as e:
                logger.error(f"Database error at offset {offset}: {str(e)}")
                break

        log_block(
            "NORMALIZATION COMPLETE",
            f"Total links processed: {self.stats['total_links']:,}",
            f"Links updated: {self.stats['updated_links']:,} ({self.stats['updated_links'] / max(self.stats['total_links'], 1) * 100:.1f}%)",
            f"Links unchanged: {self.stats['unchanged_links']:,}",
        )

        if self.stats['by_platform']:
            logger.info("Changes by original platform:")
            for old_platform, changes in sorted(self.stats['by_platform'].items()):
                for new_platform, count in sorted(changes['changed_to'].items(), key=lambda x: -x[1]):
                    logger.info(f"  {old_platform} → {new_platform}: {count:,}")

        return self.stats

    def normalize_telegram_links_only(self, dry_run: bool = False) -> Dict:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, url, link_type
                FROM user_links
                WHERE (
                    url LIKE '%t.me/%'
                    OR url LIKE '%telegram.me/%'
                    OR url LIKE '%telegram.dog/%'
                    OR url LIKE '%telegram.org/%'
                )
                AND link_type != 'telegram'
            """)
            telegram_links = cursor.fetchall()

            if not telegram_links:
                logger.info("No misclassified Telegram links found")
                conn.close()
                return {'updated': 0, 'total_found': 0}

            logger.info(f"Found {len(telegram_links)} misclassified Telegram links")

            if not dry_run:
                cursor.executemany("""
                    UPDATE user_links
                    SET link_type = 'telegram'
                    WHERE id = ?
                """, [(link_id,) for link_id, _, _ in telegram_links])
                conn.commit()

            conn.close()

            log_block(
                "TELEGRAM LINKS NORMALIZED",
                f"Found: {len(telegram_links)}",
                f"Updated: {len(telegram_links) if not dry_run else 0}",
                f"Dry run: {dry_run}",
            )

            return {
                'updated': len(telegram_links) if not dry_run else 0,
                'total_found': len(telegram_links),
            }

        except sqlite3.Error as e:
            logger.error(f"Database error: {str(e)}")
            raise

    def show_current_distribution(self) -> None:
        distribution = self._get_current_distribution()
        log_block(
            "CURRENT LINK TYPE DISTRIBUTION",
            f"Total link types: {len(distribution)}",
        )
        for platform, count in sorted(distribution.items(), key=lambda x: -x[1]):
            percentage = count / sum(distribution.values()) * 100 if distribution else 0
            logger.info(f"  {platform:20} : {count:8,} ({percentage:5.1f}%)")


if __name__ == "__main__":
    import sys
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    telegram_only = '--telegram' in sys.argv or '-t' in sys.argv

    normalizer = LinkTypeNormalizer()
    normalizer.show_current_distribution()

    if telegram_only:
        logger.info("NORMALIZING TELEGRAM LINKS ONLY")
        results = normalizer.normalize_telegram_links_only(dry_run=dry_run)
    else:
        logger.info("NORMALIZING ALL LINK TYPES")
        results = normalizer.normalize_all_links(dry_run=dry_run)

    logger.info("FINAL LINK TYPE DISTRIBUTION")
    normalizer.show_current_distribution()