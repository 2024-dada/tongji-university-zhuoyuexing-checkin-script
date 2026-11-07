import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
from checkin.batch import ALL_THEMES, PAGE_THEMES, THEMES
from checkin.storage import History, ThemeHistory
from checkin.engine import Engine, DailyLimit
from checkin.shizhi import ShizhiBatch

class UnifiedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_migrate_both_sources_and_keep_five_separate_files(self):
        old = History(self.root)
        old.import_completed('弘文之星', '实践论', '2026-10-28')
        legacy = History(self.root / '矢志之星数据', '矢志之星打卡记录.txt')
        legacy.import_completed('矢志之星', '同济大学校史馆', '2026-10-28')
        current = ThemeHistory(self.root)
        self.assertEqual(len(current.rows), 2)
        for theme in PAGE_THEMES:
            path = current.theme_path(theme)
            self.assertTrue(path.exists())
            self.assertTrue(all(json.loads(line)['theme'] == theme
                                for line in path.read_text('utf-8').splitlines()))
        current.remove_completed(current.rows[0]['id'])
        self.assertEqual(len(ThemeHistory(self.root).rows), 1)
        self.assertEqual(len(old.rows), 1)  # Original migration source untouched.

    def test_reload_reads_external_changes_and_rejects_corruption_atomically(self):
        current = ThemeHistory(self.root)
        other = History(current.directory, current.theme_path('明德之星').name)
        other.import_completed('明德之星', '汪品先', '2026-10-28')
        current.reload()
        self.assertIn('汪品先', current.used('明德之星'))
        current.theme_path('力行之星').write_text('bad json', 'utf-8')
        with self.assertRaises(ValueError): current.reload()
        self.assertEqual(len(current.rows), 1)

    def test_clear_does_not_reimport_legacy_on_restart(self):
        History(self.root).import_completed('求索之星', '旧讲座', '2026-10-28')
        current = ThemeHistory(self.root)
        self.assertIsNotNone(current.clear_all())
        self.assertEqual(ThemeHistory(self.root).rows, [])

    def test_list_selection_skips_disk_record_before_click_or_generation(self):
        current = ThemeHistory(self.root)
        external = History(current.directory, current.theme_path('弘文之星').name)
        external.import_completed('弘文之星', '实践论', '2026-10-28')
        engine = Engine.__new__(Engine)
        engine.history = current
        engine.report = Mock()
        engine.desktop = Mock()
        engine.desktop.check.side_effect = RuntimeError('stop after reading')
        # Existing selector tests cover actual row/button matching; here exercise
        # the real reload boundary with a stale in-memory history.
        engine.nodes = Mock(side_effect=RuntimeError('list boundary'))
        with self.assertRaisesRegex(RuntimeError, 'list boundary'):
            engine.choose('弘文之星', set())
        self.assertIn('实践论', current.used('弘文之星'))
        engine.desktop.click.assert_not_called()

    def test_each_regular_star_skips_completed_list_button_after_restart(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                history = ThemeHistory(self.root)
                if not history.used(theme):
                    history.import_completed(theme, '已完成目标', '2026-10-28')
                engine = Engine.__new__(Engine)
                engine.history = ThemeHistory(self.root)
                engine.report, engine.desktop = Mock(), Mock()
                page = []
                for name, y in [('已完成目标', 100), ('未使用目标', 200)]:
                    page += [dict(name=name, kind='Text', x=80, y=y, width=400, height=30),
                             dict(name='去打卡', kind='Button', x=920, y=y, width=80, height=30)]
                engine.nodes = Mock(return_value=page)
                engine.selector_visible_nodes = lambda nodes: nodes
                engine.details = Mock(return_value={})
                engine.wait_for_form = Mock(return_value=[])
                engine.teacher_fields = Mock(return_value={})
                name, _ = engine.choose(theme, set())
                self.assertEqual(name, '未使用目标')
                engine.desktop.click.assert_called_once_with(page[3])

    def test_shizhi_list_reloads_and_skips_before_opening(self):
        current = ThemeHistory(self.root)
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.history, batch.report = current, Mock()
        external = History(current.directory, current.theme_path('矢志之星').name)
        external.import_completed('矢志之星', '同济大学校史馆', '2026-10-28')
        self.assertTrue(batch.skip_list_landmark('同济大学校史馆'))
        self.assertFalse(batch.skip_list_landmark('同济大学文远楼'))

class UnifiedDispatchTests(unittest.TestCase):
    def instance(self):
        obj = app.App.__new__(app.App)
        obj.cancel = threading.Event()
        obj.config = {}
        obj.events = queue.Queue()
        obj.history, obj.report, obj.handoff = Mock(), Mock(), Mock()
        obj.batch_step = Mock()
        obj.with_monitor = Mock()
        return obj

    def test_every_single_choice_and_all_share_one_dispatch(self):
        for selected in (ALL_THEMES,) + PAGE_THEMES:
            with self.subTest(selected=selected), patch('checkin.shizhi.ShizhiBatch') as factory:
                obj = self.instance()
                obj.run_selected(selected)
                self.assertEqual(obj.batch_step.call_count, 2 if selected == ALL_THEMES else int(selected != '矢志之星'))
                self.assertEqual(factory.call_count, int(selected in (ALL_THEMES, '矢志之星')))
                if factory.called:
                    self.assertIs(factory.return_value.history, obj.history)
                    self.assertIs(factory.return_value.engine.history, obj.history)
                    factory.return_value.run.assert_called_once_with(module_only=True)

    def test_shizhi_runs_before_lixing(self):
        obj = self.instance()
        order = []
        obj.batch_step.side_effect = lambda scope: order.extend(scope)
        with patch('checkin.shizhi.ShizhiBatch') as factory:
            factory.return_value.run.side_effect = lambda **kwargs: order.append('矢志之星')
            obj.run_selected(('力行之星', '矢志之星'))
        self.assertEqual(order, ['矢志之星', '力行之星'])

    def test_cancel_prevents_switching_to_browser(self):
        obj = self.instance()
        obj.batch_step.side_effect = lambda scope: obj.cancel.set()
        with patch('checkin.shizhi.ShizhiBatch') as factory:
            obj.run_selected(ALL_THEMES)
            factory.assert_not_called()

    def test_shizhi_quota_exhaustion_is_normal_completion(self):
        obj = self.instance()
        with patch('checkin.shizhi.ShizhiBatch') as factory:
            factory.return_value.run.side_effect = DailyLimit('次数用完')
            obj.run_selected('矢志之星')
        obj.history.reload.assert_called_once()
        self.assertEqual(obj.events.get_nowait()[0], 'completed')

if __name__ == '__main__': unittest.main()
