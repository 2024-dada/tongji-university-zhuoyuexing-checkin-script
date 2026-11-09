import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.engine import Engine, MaterialUnavailable, NeedUser
from checkin.storage import History
from checkin.images import ImageSelectionRequired, ImageSearchUnavailable
from checkin.batch import BatchSession
from checkin.deepseek import TeacherInformationMissing, NoBalance
from checkin.storage import today


class FreshListHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.history = History(self.root)
        self.engine = Engine.__new__(Engine)
        engine = self.engine
        engine.root = engine.data_root = self.root
        engine.config = {}
        engine.history = self.history
        engine.cancel = threading.Event()
        engine.report = engine.on_row = Mock()
        engine.api = Mock()
        engine.api.balance.return_value = '1 CNY'
        engine.api.reflection.return_value = '活动感想' * 30
        engine.desktop = Mock()
        engine.navigation = Mock(return_value=[])
        engine.open_selector = Mock()
        engine.clear_selector_query = Mock()
        engine.selector_visible_nodes = lambda nodes: nodes
        engine.details = Mock(side_effect=lambda theme, name: {'name': name, 'minimum_chars': 80})
        self.page = []
        for name, y in [('实践论', 100), ('天演论', 200)]:
            self.page += [dict(name=name, kind='Text', x=80, y=y, width=400, height=30),
                          dict(name='去打卡', kind='Button', x=920, y=y, width=80, height=30)]
        engine.nodes = Mock(return_value=self.page)

    def prepare(self):
        finder = Mock()
        finder.choose.return_value = str(self.root / 'picture.png')
        finder.warning.return_value = ''
        with patch('checkin.engine.ImageFinder', return_value=finder):
            return self.engine.prepare('弘文之星')

    def test_new_disk_completion_is_skipped_in_list_before_click_or_generation(self):
        # Simulate a completion/import written after the assistant was started.
        other = History(self.root)
        other.import_completed('弘文之星', '实践论', '2026-10-21')
        row = self.prepare()
        self.assertEqual(row['object'], '天演论')
        self.engine.desktop.click.assert_called_once_with(self.page[3])
        self.engine.api.reflection.assert_called_once_with(
            '弘文之星', {'name': '天演论', 'minimum_chars': 80}, 80)
        self.assertIn('实践论', self.history.used('弘文之星'))

    def test_damaged_fresh_history_stops_before_network_and_navigation(self):
        self.history.path.write_text('broken json', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '记录第 1 行损坏'):
            self.prepare()
        self.engine.api.balance.assert_not_called()
        self.engine.navigation.assert_not_called()
        self.assertEqual(self.history.path.read_text('utf-8'), 'broken json')

    def test_reload_is_atomic_when_a_later_line_is_bad(self):
        self.history.import_completed('弘文之星', '原有活动', '2026-10-21')
        old = [row.copy() for row in self.history.rows]
        with self.history.path.open('a', encoding='utf-8') as stream:
            stream.write('broken json\n')
        with self.assertRaisesRegex(ValueError, '记录第 2 行损坏'):
            self.history.reload()
        self.assertEqual(self.history.rows, old)

    def test_direct_list_selection_refreshes_a_stale_history_snapshot(self):
        other = History(self.root)
        other.import_completed('弘文之星', '实践论', '2026-10-21')
        name, _ = self.engine.choose('弘文之星', set())
        self.assertEqual(name, '天演论')
        self.engine.desktop.click.assert_called_once_with(self.page[3])
        self.engine.api.reflection.assert_not_called()
        messages = [str(c.args[0]) for c in self.engine.report.call_args_list]
        self.assertTrue(any('点击前跳过：实践论' in text for text in messages))

    def test_reload_respects_explicit_clear_and_does_not_import_backups(self):
        self.history.import_completed('弘文之星', '实践论', '2026-10-21')
        self.history.clear_all()
        self.history.reload()
        self.assertEqual(self.history.used('弘文之星'), set())

    def test_reopened_history_skips_completed_name_before_click(self):
        self.history.import_completed('弘文之星', '实践论', '2026-10-21')
        self.engine.history = History(self.root)
        row = self.prepare()
        self.assertEqual(row['object'], '天演论')
        self.engine.desktop.click.assert_called_once_with(self.page[3])

    def test_search_exhaustion_is_saved_as_skipped_not_pending(self):
        finder = Mock()
        finder.choose.side_effect = ImageSelectionRequired('没有可用图片')
        with patch('checkin.engine.ImageFinder', return_value=finder):
            with self.assertRaises(MaterialUnavailable):
                self.engine.prepare('弘文之星')
        restarted = History(self.root)
        self.assertEqual(restarted.rows[-1]['status'], '已跳过')
        self.assertIsNone(restarted.pending('弘文之星'))
        self.engine.history = restarted
        name, _ = self.engine.choose('弘文之星', set())
        self.assertEqual(name, '天演论')

    def test_transport_failure_preserves_pending_object_and_stops_batch(self):
        finder = Mock()
        finder.choose.side_effect = ImageSearchUnavailable('代理 DNS 配置导致下载失败')
        with patch('checkin.engine.ImageFinder', return_value=finder):
            with self.assertRaises(NeedUser):
                self.engine.prepare('弘文之星')
        self.assertEqual(self.history.rows[-1]['status'], '待提交')
        self.assertIsNotNone(self.history.pending('弘文之星'))

    def test_batch_advances_after_search_failure_without_submitting_or_spending_quota(self):
        import app
        instance = app.App.__new__(app.App)
        instance.history = self.history
        instance.events = Mock()
        instance.report = Mock()
        instance.batch = BatchSession(today(), ['弘文之星'])
        instance.engine = Mock(return_value=self.engine)
        self.engine.read_remaining = Mock(side_effect=[{'弘文之星': 1}, {'弘文之星': 1}, {'弘文之星': 0}])
        self.engine.fill = Mock()
        def submit(row):
            return 'success', self.history.update(row['id'], status='已完成')
        self.engine.submit = Mock(side_effect=submit)
        finder = Mock()
        finder.choose.side_effect = [ImageSelectionRequired('没有可用图片'), str(self.root / 'picture.png')]
        finder.warning.return_value = ''
        with patch('checkin.engine.ImageFinder', return_value=finder):
            instance.batch_step()
        self.assertEqual([r['status'] for r in History(self.root).rows], ['已跳过', '已完成'])
        self.assertEqual(self.engine.fill.call_count, 1)
        self.assertEqual(self.engine.submit.call_args.args[0]['object'], '天演论')
        self.assertEqual(instance.batch.confirmed['弘文之星'], 1)

    def test_missing_teacher_college_is_skipped_and_survives_restart(self):
        self.page[0]['name'] = '李同保'
        self.page[2]['name'] = '新教师'
        self.engine.wait_for_form = Mock(return_value=[])
        self.engine.teacher_fields = lambda name, facts, nodes: {'name': name}
        self.engine.read_field = Mock(return_value='')
        self.engine.api.teacher_information.side_effect = TeacherInformationMissing('未知学院')
        self.engine.api.teacher_followup.return_value = {'college': '', 'reason': '没有资料'}
        directory = Mock()
        directory.known.return_value = None
        directory.lookup.return_value = None
        with patch('checkin.teachers.TeacherDirectory', return_value=directory):
            with self.assertRaises(MaterialUnavailable):
                self.engine.prepare('明德之星')
        self.engine.history = History(self.root)
        name, _ = self.engine.choose('明德之星', set())
        self.assertEqual(name, '新教师')
        self.assertEqual(self.engine.history.rows[0]['status'], '已跳过')

    def test_balance_or_cancel_is_not_saved_as_search_skip(self):
        for error in [NoBalance('余额不足'), RuntimeError('操作已停止')]:
            with self.subTest(error=type(error).__name__):
                finder = Mock()
                finder.choose.side_effect = error
                with patch('checkin.engine.ImageFinder', return_value=finder):
                    with self.assertRaises(type(error)):
                        self.engine.prepare('弘文之星')
                self.assertEqual(self.history.rows[-1]['status'], '待提交')


class SelectorQueryResetTests(unittest.TestCase):
    def test_distinct_compact_search_inputs_remain_ambiguous(self):
        engine = Engine.__new__(Engine)
        nodes = [dict(kind='Edit', name='', value='甲', x=100, y=100, width=200, height=40),
                 dict(kind='Edit', name='', value='乙', x=400, y=100, width=200, height=40),
                 dict(kind='Text', name='搜索', x=800, y=110, width=60, height=20),
                 dict(kind='Text', name='去打卡', x=800, y=200, width=60, height=20)]
        self.assertIsNone(engine.selector_search_controls(nodes))

    def test_popup_search_ignores_form_textarea_visible_under_the_popup(self):
        # Real MSAA geometry: the underlying textarea covers the search row.
        nodes = [
            dict(kind='Edit', name='', value='战争与和平', x=150, y=201, width=2568, height=46),
            dict(kind='Edit', name='', value='', x=150, y=304, width=2700, height=200),
            dict(kind='Edit', name='', value='战争与和平', x=176, y=366, width=2514, height=65),
            dict(kind='Text', name='搜 索', value='', x=2767, y=384, width=55, height=28),
            dict(kind='Text', name='去打卡', value='', x=2752, y=510, width=72, height=28),
        ]
        engine = Engine.__new__(Engine)
        engine.desktop = Mock()
        engine.desktop.ocr_nodes.return_value = []
        engine.nodes = Mock(return_value=nodes)
        engine.report = Mock()
        engine.clear_selector_query(nodes)
        self.assertEqual(engine.desktop.click.call_count, 2)
        self.assertEqual(engine.desktop.click.call_args_list[0].args[0]['y'], 366)
        engine.desktop.paste.assert_called_once_with('')
        self.assertEqual(engine.desktop.click.call_args_list[1].args[0]['name'], '搜 索')


if __name__ == '__main__':
    unittest.main()
