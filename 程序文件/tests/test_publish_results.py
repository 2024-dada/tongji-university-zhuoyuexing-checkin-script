import unittest
from checkin.publishing import classify


class PublishResultTests(unittest.TestCase):
    def test_success_toast_is_not_overridden_by_other_module_quota_text(self):
        message = '识百师以崇道德卡的次数已用完，明天再来吧！之星访百地以寻初心以打卡3次哦打卡成功'
        self.assertEqual(classify(message), 'success')

    def test_quota_without_a_success_result_stays_an_error(self):
        self.assertEqual(classify('今天打卡的次数已用完，明天再来吧！'), 'error')

    def test_explicit_publish_failure_is_not_overridden_by_success_word(self):
        self.assertEqual(classify('发布失败，请重试；未能确认打卡成功'), 'error')

    def test_duplicate_toast_is_not_overridden_by_other_module_quota(self):
        self.assertEqual(classify('今天打卡的次数已用完，该地点已打卡！'), 'already')
