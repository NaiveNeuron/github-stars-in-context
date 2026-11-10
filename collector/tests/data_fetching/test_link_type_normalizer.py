import unittest

from data_storage.normalize_link_types import LinkTypeNormalizer, PlatformConfig


class TestLinkTypeNormalizerInitialization(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_initialization_creates_platform_configs(self):
        platforms = self.normalizer.get_available_platforms()
        self.assertIn('telegram', platforms)
        self.assertIn('twitter', platforms)
        self.assertIn('github', platforms)
        self.assertIn('website', platforms)

    def test_platform_configs_have_compiled_patterns(self):
        for _platform, config in self.normalizer._platform_configs.items():
            self.assertIsInstance(config, PlatformConfig)
            self.assertIsInstance(config.name, str)
            self.assertIsInstance(config.patterns, list)
            self.assertIsInstance(config.priority, int)
            for pattern in config.patterns:
                self.assertTrue(hasattr(pattern, 'search'))

    def test_telegram_has_highest_priority(self):
        priority = self.normalizer.get_platform_priority('telegram')
        self.assertEqual(priority, 1)

    def test_unknown_has_lowest_priority(self):
        priority = self.normalizer.get_platform_priority('unknown')
        self.assertEqual(priority, 999)

    def test_website_has_low_priority(self):
        priority = self.normalizer.get_platform_priority('website')
        self.assertEqual(priority, 99)


class TestDetectPlatformTelegram(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_detect_telegram_short_url(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://t.me/durov'),
            'telegram'
        )

    def test_detect_telegram_full_url(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://telegram.me/username'),
            'telegram'
        )

    def test_detect_telegram_dog_url(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://telegram.dog/channel'),
            'telegram'
        )

    def test_detect_telegram_case_insensitive(self):
        self.assertEqual(
            self.normalizer._detect_platform('HTTPS://T.ME/USERNAME'),
            'telegram'
        )

    def test_detect_telegram_with_path(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://t.me/channel/123'),
            'telegram'
        )


class TestDetectPlatformTwitter(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_detect_twitter_com(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://twitter.com/elonmusk'),
            'twitter'
        )

    def test_detect_x_com(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://x.com/elonmusk'),
            'twitter'
        )

    def test_detect_twitter_with_hash_routing(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://twitter.com/#!/user'),
            'twitter'
        )


class TestDetectPlatformEdgeCases(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_detect_empty_url(self):
        self.assertEqual(self.normalizer._detect_platform(''), 'unknown')

    def test_detect_none_url(self):
        self.assertEqual(self.normalizer._detect_platform(None), 'unknown')

    def test_detect_unknown_platform(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://unknown-site.random/profile'),
            'unknown'
        )

    def test_detect_email_link(self):
        self.assertEqual(
            self.normalizer._detect_platform('mailto:test@example.com'),
            'email'
        )

    def test_detect_generic_website(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://example.com'),
            'website'
        )

    def test_detect_generic_website_with_path(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://example.com/path'),
            'website'
        )

    def test_detect_github_profile(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://github.com/torvalds'),
            'github'
        )

    def test_detect_linkedin_profile(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://linkedin.com/in/john-doe'),
            'linkedin'
        )

    def test_detect_linkedin_company(self):
        self.assertEqual(
            self.normalizer._detect_platform('https://linkedin.com/company/google'),
            'linkedin'
        )


class TestDetectPlatformPriority(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_telegram_priority_over_website(self):
        url = 'https://t.me/user'
        platform = self.normalizer._detect_platform(url)
        self.assertEqual(platform, 'telegram')
        self.assertLess(
            self.normalizer.get_platform_priority('telegram'),
            self.normalizer.get_platform_priority('website')
        )

    def test_twitter_priority_over_website(self):
        url = 'https://twitter.com/user'
        platform = self.normalizer._detect_platform(url)
        self.assertEqual(platform, 'twitter')
        self.assertLess(
            self.normalizer.get_platform_priority('twitter'),
            self.normalizer.get_platform_priority('website')
        )


class TestLinkTypeNormalizerCaching(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_cache_hits_on_repeated_calls(self):
        url = 'https://t.me/durov'

        result1 = self.normalizer._detect_platform(url)
        cache_info1 = self.normalizer._detect_platform_cached.cache_info()

        result2 = self.normalizer._detect_platform(url)
        cache_info2 = self.normalizer._detect_platform_cached.cache_info()

        self.assertEqual(result1, result2)
        self.assertEqual(result1, 'telegram')
        self.assertGreater(cache_info2.hits, cache_info1.hits)

    def test_cache_clear_resets_stats(self):
        url = 'https://github.com/user'
        self.normalizer._detect_platform(url)

        self.normalizer.clear_cache()
        cache_info_after = self.normalizer._detect_platform_cached.cache_info()

        self.assertEqual(cache_info_after.hits, 0)
        self.assertEqual(cache_info_after.misses, 0)

    def test_different_urls_have_different_cache_entries(self):
        self.normalizer._detect_platform('https://t.me/user1')
        self.normalizer._detect_platform('https://t.me/user2')
        self.normalizer._detect_platform('https://twitter.com/user1')

        cache_info = self.normalizer._detect_platform_cached.cache_info()
        self.assertEqual(cache_info.misses, 3)


class TestLinkTypeNormalizerIntegration(unittest.TestCase):
    def setUp(self):
        self.normalizer = LinkTypeNormalizer()
        self.test_cases = [
            ('https://t.me/durov', 'telegram'),
            ('https://telegram.me/channel', 'telegram'),
            ('https://twitter.com/elonmusk', 'twitter'),
            ('https://x.com/elonmusk', 'twitter'),
            ('https://instagram.com/cristiano', 'instagram'),
            ('https://youtube.com/channel/abc', 'youtube'),
            ('https://youtu.be/xyz123', 'youtube'),
            ('https://linkedin.com/in/john', 'linkedin'),
            ('https://linkedin.com/company/google', 'linkedin'),
            ('https://github.com/torvalds', 'github'),
            ('https://mastodon.social/@user', 'mastodon'),
            ('mailto:test@example.com', 'email'),
            ('https://example.com', 'website'),
            ('https://example.com/path', 'website'),
            ('https://medium.com/@user', 'medium'),
            ('https://dev.to/user', 'dev_to'),
            ('https://hashnode.com/@user', 'hashnode'),
            ('', 'unknown'),
            (None, 'unknown'),
            ('https://unknown-site.random/profile', 'unknown'),
        ]

    def tearDown(self):
        self.normalizer.clear_cache()

    def test_all_test_cases(self):
        for url, expected_platform in self.test_cases:
            with self.subTest(url=url, expected=expected_platform):
                result = self.normalizer._detect_platform(url)
                self.assertEqual(result, expected_platform)

    def test_batch_processing_performance(self):
        import time

        test_url = 'https://t.me/testuser'

        start = time.time()
        for _ in range(100):
            self.normalizer._detect_platform(test_url)
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.1)

        cache_info = self.normalizer._detect_platform_cached.cache_info()
        self.assertGreater(cache_info.hits, 90)


if __name__ == '__main__':
    unittest.main()
