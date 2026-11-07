import unittest

from checkin.storage import same_object_by_keyword


class SameObjectKeywordTests(unittest.TestCase):
    def test_normalizes_punctuation_and_full_width_forms(self):
        self.assertTrue(same_object_by_keyword('《世界通史》', '世界通史（第二版）'))

    def test_short_names_only_match_when_the_normalized_names_are_equal(self):
        self.assertTrue(same_object_by_keyword('飘', '《飘》'))
        self.assertFalse(same_object_by_keyword('实践论', '矛盾论'))

    def test_landmark_keyword_threshold_avoids_shared_university_prefix(self):
        self.assertTrue(same_object_by_keyword(
            '同济大学四平路校区图书馆（沪西分馆）',
            '同济大学四平路校区图书馆', minimum_containment=6))
        self.assertFalse(same_object_by_keyword(
            '同济大学图书馆', '同济大学博物馆', minimum_containment=6))


if __name__ == '__main__':
    unittest.main()
