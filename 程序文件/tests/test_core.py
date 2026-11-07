import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from checkin.deepseek import APIError, DeepSeek, NoBalance
from checkin.storage import History, today
from checkin.windows import Desktop


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.history = History(self.root)

    def test_pending_resume_and_confirm_are_distinct(self):
        row = self.history.reserve('弘文之星', '实践论', {})
        self.assertFalse(self.history.done_today('弘文之星'))
        restored = History(self.root)
        self.assertEqual(restored.pending('弘文之星')['id'], row['id'])
        restored.confirm(row['id'])
        self.assertTrue(History(self.root).done_today('弘文之星'))
        self.assertIsNone(History(self.root).pending('弘文之星'))

    def test_same_book_normalization_and_second_pending(self):
        self.history.reserve('弘文之星', '实 践论', {})
        with self.assertRaises(ValueError):
            self.history.reserve('弘文之星', '实践论', {})

    def test_import_old_object_does_not_block_today(self):
        self.history.import_completed('弘文之星', '矛盾论', '2020-01-01')
        self.assertIn('矛盾论', self.history.used('弘文之星'))
        self.assertFalse(self.history.done_today('弘文之星'))

    def test_independent_landmarks_preserve_unknown_result_and_block_duplicates(self):
        old = self.history.reserve('矢志之星', '2338', {})
        self.history.update(old['id'], phase='结果待核实')
        new = self.history.reserve('矢志之星', '2260', {}, independent_objects=True)
        restored = History(self.root)
        self.assertEqual(len(restored.rows), 2)
        self.assertEqual(restored.rows[0]['phase'], '结果待核实')
        self.assertEqual(restored.rows[1]['id'], new['id'])
        with self.assertRaisesRegex(ValueError, '已经使用过'):
            restored.reserve('矢志之星', '2338', {}, independent_objects=True)

    def test_default_reservation_still_blocks_a_second_pending_object(self):
        self.history.reserve('弘文之星', '实践论', {})
        with self.assertRaisesRegex(ValueError, '已有待提交'):
            self.history.reserve('弘文之星', '矛盾论', {})

    def test_independent_reservation_rolls_back_when_save_fails(self):
        self.history.reserve('矢志之星', '2338', {})
        with patch.object(self.history, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.history.reserve('矢志之星', '2260', {}, independent_objects=True)
        self.assertEqual([r['object'] for r in self.history.rows], ['2338'])
        self.assertEqual([r['object'] for r in History(self.root).rows], ['2338'])

    def test_corruption_preserves_original(self):
        self.history.path.write_text('broken json', 'utf-8')
        with self.assertRaises(ValueError):
            History(self.root)
        self.assertEqual(self.history.path.read_text('utf-8'), 'broken json')

    def test_failed_write_rolls_back_in_memory_completion(self):
        row = self.history.reserve('弘文之星', '实践论', {})
        with patch.object(self.history, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.history.confirm(row['id'])
        self.assertFalse(self.history.done_today('弘文之星'))
        self.assertEqual(row['status'], '待提交')

    def test_same_record_cannot_be_confirmed_twice(self):
        row = self.history.reserve('弘文之星', '实践论', {})
        self.history.confirm(row['id'])
        with self.assertRaises(ValueError):
            self.history.confirm(row['id'])

    def test_multiple_different_objects_can_complete_on_the_same_day(self):
        for theme in ('弘文之星', '明德之星', '求索之星', '力行之星'):
            for name in ('对象一', '对象二'):
                row = self.history.reserve(theme, name, {})
                self.history.confirm(row['id'])
            self.assertEqual(len([r for r in self.history.rows if r['theme'] == theme and r['status'] == '已完成']), 2)
            with self.assertRaises(ValueError):
                self.history.reserve(theme, '对象一', {})

    def test_backup_keeps_previous_version(self):
        row = self.history.reserve('弘文之星', '实践论', {})
        old = self.history.path.read_text('utf-8')
        self.history.update(row['id'], text='正文')
        self.assertEqual(self.history.path.with_suffix('.txt.bak').read_text('utf-8'), old)


class APIHandlingTests(unittest.TestCase):
    def setUp(self):
        transport = patch('checkin.deepseek.shutil.which', return_value=None)
        transport.start()
        self.addCleanup(transport.stop)

    def test_402_has_recharge_message_without_key(self):
        api = DeepSeek('not-a-real-key')
        failure = urllib.error.HTTPError('https://api.deepseek.com/user/balance', 402, 'error', {}, None)
        with patch('urllib.request.urlopen', side_effect=failure):
            with self.assertRaises(NoBalance) as caught:
                api.balance()
        self.assertIn('充值', str(caught.exception))
        self.assertNotIn('not-a-real-key', str(caught.exception))

    def test_unavailable_balance_is_not_silent(self):
        api = DeepSeek('test')
        with patch.object(api, 'request', return_value={'is_available': False}):
            with self.assertRaises(NoBalance):
                api.balance()

    def test_401_is_distinct_from_balance(self):
        api = DeepSeek('test')
        failure = urllib.error.HTTPError('https://api.deepseek.com/user/balance', 401, 'error', {}, None)
        with patch('urllib.request.urlopen', side_effect=failure):
            with self.assertRaises(APIError) as caught:
                api.balance()
        self.assertNotIsInstance(caught.exception, NoBalance)

    def test_minimum_chars_is_enforced(self):
        api = DeepSeek('test')
        with patch.object(api, 'json', side_effect=[{'text': '太短'}, {'text': '读书感想' * 25}]):
            self.assertGreaterEqual(len(api.reflection('弘文之星', {'name': '实践论'}, 80)), 80)


class InputGuardTests(unittest.TestCase):
    def test_publish_login_and_excluded_theme_cannot_be_clicked(self):
        desktop = Desktop.__new__(Desktop)
        for label in ('确认发布', '统一身份认证登录', '矢志之星'):
            with self.assertRaises(RuntimeError):
                desktop.click({'name': label, 'enabled': True})


if __name__ == '__main__':
    unittest.main()
