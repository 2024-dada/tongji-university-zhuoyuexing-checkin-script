import importlib.machinery
import importlib.util
from pathlib import Path
import queue
import threading
import tempfile
from checkin.storage import History
import unittest
from unittest.mock import Mock, patch

from checkin.engine import DailyLimit
from checkin.shizhi import ShizhiBatch, SubmitRejected

QUOTA = '今天打卡的次数已用完，明天再来吧！'
URL = 'https://star.tongji.edu.cn/app/pages-daka/shizhi?id=2264'


class QuotaTests(unittest.TestCase):
    def test_wait_form_ends_immediately_at_quota_instead_of_waiting(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.desktop = Mock()
        batch.engine = Mock()
        batch.engine.nodes.return_value = [dict(value=URL), dict(name=QUOTA)]
        batch.check = lambda: None
        with patch('checkin.shizhi.time.monotonic', side_effect=[0, 0, 30]):
            with self.assertRaises(DailyLimit):
                batch.wait_form('同济大学经济与管理学院')
        batch.desktop.click.assert_not_called()
        batch.desktop.pause.assert_not_called()

    def test_unrelated_site_text_cannot_trigger_quota_exit(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.engine = Mock()
        batch.engine.nodes.return_value = [dict(value='https://example.com'), dict(name=QUOTA)]
        batch.check = lambda: None
        self.assertEqual(batch.nodes()[-1]['name'], QUOTA)

    def test_focus_error_readonly_probe_detects_quota(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.check = lambda: None
        batch.engine = Mock()
        batch.engine.nodes.side_effect = RuntimeError('目标窗口未能获得焦点，已停止，避免误点其他窗口。')
        batch.desktop = Mock()
        batch.desktop.snapshot.return_value = [dict(value=URL), dict(name=QUOTA)]
        with self.assertRaises(DailyLimit):
            batch.nodes()
        batch.desktop.snapshot.assert_called_once_with(activate=False)
        batch.desktop.click.assert_not_called()

    def test_quota_during_submit_does_not_mark_result_uncertain(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.desktop = Mock()
        batch.history = Mock()
        observer = Mock()
        observer.result.return_value = QUOTA
        with self.assertRaises(DailyLimit):
            batch.wait_submit_result({'id': 'row'}, observer)
        batch.history.update.assert_not_called()
        batch.history.confirm.assert_not_called()

    def test_quota_removes_only_current_pending_and_cleans_images(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = ShizhiBatch.__new__(ShizhiBatch)
            batch.history = History(directory)
            completed = batch.history.reserve('矢志之星', 'old', {'title': '已完成地标'})
            batch.history.update(completed['id'], status='已完成')
            batch.detail_id = lambda url: 'current'
            batch.detail_name = lambda nodes, name: name
            batch.seen_this_run = set()
            batch.report = Mock()
            button = dict(name='去打卡', enabled=True)
            batch.wait_for_location = Mock(return_value=('可打卡', [button]))
            batch.page_address = Mock(return_value='')
            batch.page_description = Mock(return_value='')
            batch.unique_label_target = Mock(return_value=button)
            batch.desktop = Mock()
            batch.wait_form = Mock(side_effect=DailyLimit(QUOTA))
            batch.safe_cleanup = Mock()
            with self.assertRaises(DailyLimit):
                batch.process_detail([], URL, '当前地标', '热门地标')
            self.assertEqual([r['object'] for r in History(directory).rows], ['old'])
            self.assertEqual(batch.history.rows[0]['status'], '已完成')
            batch.safe_cleanup.assert_called_once()

    def test_explicit_rejection_retries_and_rechecks_form(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.desktop = Mock()
        batch.history = Mock()
        batch.report = Mock()
        batch.nodes = Mock(return_value=['fresh'])
        batch.verify_filled = Mock()
        batch.submit_attempt = Mock(side_effect=[SubmitRejected('发布失败'), None])
        row = dict(id='row', fields=dict(title='地标'))
        batch.submit_once([], row)
        self.assertEqual(batch.submit_attempt.call_count, 2)
        batch.verify_filled.assert_called_once_with('地标', row, ['fresh'])

    def test_unknown_result_is_not_retried(self):
        from checkin.engine import NeedUser
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.submit_attempt = Mock(side_effect=NeedUser('结果待核实'))
        with self.assertRaises(NeedUser):
            batch.submit_once([], dict(id='row'))
        self.assertEqual(batch.submit_attempt.call_count, 1)

    def test_explicit_rejections_stop_after_three_attempts(self):
        from checkin.engine import NeedUser
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.desktop = Mock()
        batch.history = Mock()
        batch.report = Mock()
        batch.nodes = Mock(return_value=[])
        batch.verify_filled = Mock()
        batch.submit_attempt = Mock(side_effect=SubmitRejected('发布失败'))
        with self.assertRaisesRegex(NeedUser, '连续3次'):
            batch.submit_once([], dict(id='row', fields=dict(title='地标')))
        self.assertEqual(batch.submit_attempt.call_count, 3)

    def test_uncertain_record_does_not_block_entire_scan(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.history = Mock()
        batch.history.rows = [dict(theme='矢志之星', object='2338', status='待提交',
                                   phase='结果待核实', fields=dict(title='数字城市研究院'))]
        batch.clear_stale_images = Mock()
        batch.report = Mock()
        batch.desktop = Mock()
        batch.go_home = Mock(return_value=([], 'home'))
        batch.process_carousel = Mock(return_value=[])
        batch.run()
        self.assertEqual(batch.process_carousel.call_count, 2)
        self.assertEqual(batch.history.rows[0]['phase'], '结果待核实')

    def test_uncertain_landmark_is_skipped_without_republishing(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.history = Mock()
        batch.history.rows = [dict(theme='矢志之星', object='2338', status='待提交',
                                   phase='结果待核实')]
        batch.seen_this_run = set()
        batch.detail_id = lambda url: '2338'
        batch.detail_name = lambda nodes, name: name
        batch.report = Mock()
        batch.wait_for_location = Mock(side_effect=AssertionError('不应重新尝试待核实地标'))
        self.assertEqual(batch.process_detail([], 'detail', '数字城市研究院', '热门地标'), '待核实')
        batch.wait_for_location.assert_not_called()

    def test_observer_timeout_checks_live_quota_page(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.engine = Mock()
        batch.engine.nodes.return_value = [dict(value=URL),dict(name=QUOTA)]
        batch.desktop = Mock()
        batch.history = Mock()
        batch.check = lambda: None
        with patch('checkin.shizhi.time.monotonic', side_effect=[0,50]):
            with self.assertRaises(DailyLimit):
                batch.wait_submit_result(dict(id='row'), Mock())

    def test_old_unverified_row_does_not_block_reserving_another_landmark(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = ShizhiBatch.__new__(ShizhiBatch)
            batch.history = History(directory)
            old = batch.history.reserve('矢志之星', '2338', {'title': '数字城市研究院'})
            batch.history.update(old['id'], phase='结果待核实')
            batch.detail_id = lambda url: '2260'
            batch.detail_name = lambda nodes, title: title
            batch.report = Mock()
            batch.seen_this_run = set()
            batch.wait_for_location = Mock(return_value=('超范围', []))
            self.assertEqual(batch.process_detail([], 'detail', '杨浦高级中学', '热门地标'), '超范围')
            saved = History(directory).rows
            self.assertEqual(saved[0]['phase'], '结果待核实')
            self.assertEqual(saved[1]['object'], '2260')
            self.assertEqual(saved[1]['status'], '超出范围')

    def test_focus_error_without_quota_keeps_original_pause(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.check = lambda: None
        batch.engine = Mock()
        batch.engine.nodes.side_effect = RuntimeError('目标窗口未能获得焦点，已停止。')
        batch.desktop = Mock()
        batch.desktop.snapshot.return_value = [dict(value=URL), dict(name='填写感悟、寄语等')]
        with self.assertRaisesRegex(RuntimeError, '未能获得焦点'):
            batch.nodes()


@unittest.skipUnless((Path(__file__).resolve().parents[1] / 'shizhi_app.pyw').is_file(),
                     'Standalone legacy window is not included in the unified package')
class QuotaWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = importlib.machinery.SourceFileLoader('shizhi_quota_test', str(Path(__file__).resolve().parents[1] / 'shizhi_app.pyw'))
        cls.module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(cls.module)

    def test_quota_event_closes_window_only_after_worker_finishes(self):
        window = self.module.Window.__new__(self.module.Window)
        window.events = queue.Queue()
        window.events.put(('limit', QUOTA))
        window.cancel = threading.Event()
        window.close_requested = False
        window.uninstall_requested = False
        window.started_at = None
        for name in ('worker', 'root', 'write', 'start_button', 'routes_button', 'stop_button', 'state'):
            setattr(window, name, Mock())
        window.worker.is_alive.return_value = True
        window.poll()
        self.assertTrue(window.cancel.is_set())
        self.assertTrue(window.close_requested)
        window.root.destroy.assert_not_called()
        window.worker.is_alive.return_value = False
        window.poll()
        window.root.destroy.assert_called_once()
