import unittest

from data_analysis.h_index_calculator import calculate_h_index_from_stars


class TestCalculateHIndex(unittest.TestCase):

    def test_empty_list(self):
        star_counts: list[int] = []
        self.assertEqual(calculate_h_index_from_stars(star_counts), 0)

    def test_single_repo_zero_stars(self):
        star_counts = [0]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 0)

    def test_single_repo_one_star(self):
        star_counts = [1]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 1)

    def test_single_repo_two_stars(self):
        star_counts = [2]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 1)

    def test_classic_h_index_3(self):
        star_counts = [10, 8, 5, 3]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 3)

    def test_h_index_0_due_to_low_stars(self):
        star_counts = [0, 0]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 0)

    def test_all_repos_high_stars(self):
        star_counts = [100, 90, 80]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 3)

    def test_exact_match_at_h(self):
        star_counts = [7, 6, 5, 4, 3]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 4)

    def test_unsorted_input(self):
        star_counts = [1, 5, 3]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 2)

    def test_large_h_index(self):
        star_counts = [10 - i for i in range(10)]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 5)

    def test_all_zero_stars(self):
        star_counts = [0, 0, 0]
        self.assertEqual(calculate_h_index_from_stars(star_counts), 0)

    def test_negative_star_count_raises_value_error(self):
        star_counts = [5, -3, 2]
        with self.assertRaises(ValueError) as cm:
            calculate_h_index_from_stars(star_counts)
        error_msg = str(cm.exception)
        self.assertIn("negative", error_msg.lower())
        self.assertIn("count" or "star", error_msg.lower())


if __name__ == "__main__":
    unittest.main()
