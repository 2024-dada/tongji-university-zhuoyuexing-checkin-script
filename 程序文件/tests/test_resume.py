"""The next run must continue where the previous one stopped."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.engine import Engine, NeedUser
from checkin.resume import ResumeMemory, ResumePoint, list_key
from checkin.shizhi import ShizhiBatch, THEME


def label(name, y):
    return {'kind': 'Text', 'name': name, 'x': 80, 'y': y, 'width': 440, 'height': 32}


def button(y):
    return {'kind': 'Button', 'name': '去打卡', 'enabled': True,
            'x': 920, 'y': y, 'width': 90, 'height': 32}


def engine_for_pages(pages):
    engine = Engine.__new__(Engine)
    engine.cancel = threading.Event()
    engine.report = Mock()
    engine.api = Mock()
    engine.config = {'minimum_chars': 80}
    engine.desktop = Mock()
    engine.nodes = Mock(side_effect=pages)
    engine.selector_visible_nodes = lambda nodes: nodes
    engine.scroll_selector = Mock()
    engine.rewind_selector = Mock()
    engine.wait_for_form = Mock(return_value=[
        {'kind': 'Text', 'name': '活动介绍：页面提供的说明', 'x': 80, 'y': 80, 'width': 700, 'height': 30},
        {'kind': 'Text', 'name': '注意：字数不少于 80 个字', 'x': 80, 'y': 120, 'width': 700, 'height': 30},
    ])
    engine.resume = None
    return engine


def nearby_control(name, x, y, kind='Text', width=500, height=30, value=''):
    return dict(name=name, value=value, x=x, y=y, kind=kind,
                width=width, height=height, enabled=True)


class NearbyDesktop:
    browser_hwnd = hwnd = 1

    def __init__(self, titles):
        self.titles, self.opened = titles, []

    def check(self): pass
    def visible_bounds(self): return (0, 90, 2000, 1250)
    def pause(self, seconds): pass
    def scroll(self, **kwargs): pass

    def snapshot(self):
        nodes = [nearby_control('', 0, 0, 'Edit',
                                value='https://star.tongji.edu.cn/app/pages-daka/shizhi')]
        for index, title in enumerate(self.titles):
            y = 300 + index * 200
            nodes.append(nearby_control(title, 40, y))
            nodes.append(nearby_control('去打卡', 900, y, 'Button', width=100))
        return nodes

    def click(self, target, **kwargs):
        self.opened.append((target['name'], target['y']))


class ResumeMemoryTests(unittest.TestCase):
    def test_remember_survives_a_restart_and_reports_where_it_stopped(self):
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            self.assertEqual(memory.describe('弘文之星'), '尚无续跑记录')
            memory.remember('弘文之星', '实践论', 3)
            restored = ResumeMemory(root)
            entry = restored.entry('弘文之星')
            self.assertEqual((entry['object'], entry['index']), ('实践论', 3))
            self.assertIn('实践论', restored.summary(['弘文之星']))
            self.assertNotIn('明德之星', restored.summary(['弘文之星']))

    def test_corrupt_memory_is_quarantined_and_never_blocks_a_run(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '打卡记录' / '续跑记忆.json'
            path.parent.mkdir()
            path.write_text('{not json', 'utf-8')
            memory = ResumeMemory(root)
            self.assertEqual(memory.entries, {})
            self.assertIn('续跑记忆', memory.warning)
            self.assertFalse(path.exists())
            self.assertEqual(len(list(path.parent.glob('续跑记忆-损坏-*.json'))), 1)
            memory.remember('明德之星', '张三', 0)
            self.assertEqual(ResumeMemory(root).entry('明德之星')['object'], '张三')

    def test_forget_only_removes_the_named_theme(self):
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            memory.remember('弘文之星', '实践论', 0)
            memory.remember('矢志之星', '同济大学大礼堂', 1, section='附近地标')
            self.assertEqual(memory.forget('弘文之星'), 1)
            self.assertIsNone(memory.entry('弘文之星'))
            self.assertEqual(memory.entry('矢志之星', '附近地标')['object'], '同济大学大礼堂')
            self.assertEqual(list_key('矢志之星', '附近地标'), '矢志之星|附近地标')

    def test_write_failure_keeps_the_run_going(self):
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            with patch('checkin.resume.atomic_write', side_effect=OSError('disk full')):
                memory.remember('求索之星', '讲座', 0)
            self.assertEqual(memory.entry('求索之星')['object'], '讲座')
            self.assertIn('续跑记忆写入失败', memory.warning)


class ResumePointTests(unittest.TestCase):
    @staticmethod
    def matches(left, right):
        return str(left).strip() == str(right).strip()

    def test_skips_rows_up_to_and_including_the_remembered_row(self):
        point = ResumePoint({'object': '乙', 'index': 1}, self.matches)
        self.assertTrue(point.before('甲'))
        self.assertTrue(point.before('乙'))
        self.assertTrue(point.done)
        self.assertFalse(point.before('丙'))
        self.assertEqual(point.skipped, 2)

    def test_index_still_skips_when_the_remembered_row_was_renamed(self):
        point = ResumePoint({'object': '旧名称', 'index': 2}, self.matches)
        results = [point.before(name) for name in ('一', '二', '三', '四')]
        self.assertEqual(results, [True, True, True, False])
        self.assertFalse(point.located)

    def test_similar_name_resolves_before_the_remembered_index(self):
        point = ResumePoint({'object': '同济大学图书馆', 'index': 5}, self.matches)
        self.assertTrue(point.before('同济大学图书馆'))
        self.assertTrue(point.done)
        self.assertEqual(point.skipped, 1)


class EngineResumeTests(unittest.TestCase):
    def test_choose_skips_rows_before_the_remembered_object(self):
        page = [label('甲书', 100), button(100), label('乙书', 160), button(160),
                label('丙书', 220), button(220)]
        engine = engine_for_pages([page, page])
        with tempfile.TemporaryDirectory() as root:
            engine.resume = ResumeMemory(root)
            engine.resume.remember('弘文之星', '乙书', 1)

            name, _ = engine.choose('弘文之星', set())

            self.assertEqual(name, '丙书')
            engine.desktop.click.assert_called_once_with(page[5])
            self.assertEqual(engine.position_hint, 2)
            messages = ' '.join(call.args[0] for call in engine.report.call_args_list)
            self.assertIn('续跑记忆', messages)
            self.assertIn('已跳过断点及之前的 2 个条目', messages)
            engine.remember_position('弘文之星', name)
            self.assertEqual(engine.resume.entry('弘文之星')['index'], 2)

    def test_stale_memory_is_cleared_and_the_list_is_rescanned(self):
        page = [label('甲书', 100), button(100), label('乙书', 160), button(160)]
        engine = engine_for_pages([page] * 6)
        with tempfile.TemporaryDirectory() as root:
            engine.resume = ResumeMemory(root)
            engine.resume.remember('弘文之星', '已经下架的书', 5)

            name, _ = engine.choose('弘文之星', set())

            self.assertEqual(name, '甲书')
            self.assertIsNone(engine.resume.entry('弘文之星'))
            engine.rewind_selector.assert_called_once()
            engine.desktop.click.assert_called_once_with(page[1])
            messages = ' '.join(call.args[0] for call in engine.report.call_args_list)
            self.assertIn('续跑记忆中的对象不在当前列表', messages)

    def test_without_memory_the_first_unused_row_is_selected(self):
        page = [label('甲书', 100), button(100), label('乙书', 160), button(160)]
        engine = engine_for_pages([page, page])

        name, _ = engine.choose('弘文之星', {'甲书'})

        self.assertEqual(name, '乙书')
        engine.desktop.click.assert_called_once_with(page[3])

    def test_a_fully_used_list_after_the_remembered_row_resets_the_cursor(self):
        page = [label('甲书', 100), button(100), label('没有更多了', 220)]
        engine = engine_for_pages([page] * 6)
        with tempfile.TemporaryDirectory() as root:
            engine.resume = ResumeMemory(root)
            engine.resume.remember('弘文之星', '甲书', 0)

            with self.assertRaises(NeedUser):
                engine.choose('弘文之星', {'甲书'})

            self.assertIsNone(engine.resume.entry('弘文之星'))
            messages = ' '.join(call.args[0] for call in engine.report.call_args_list)
            self.assertIn('重置记忆后下次从列表开头查找', messages)


    def test_stalled_list_retains_the_resume_cursor(self):
        page = [label('甲书', 100), button(100)]
        engine = engine_for_pages([page] * 6)
        with tempfile.TemporaryDirectory() as root:
            engine.resume = ResumeMemory(root)
            engine.resume.remember('弘文之星', '甲书', 0)
            with self.assertRaises(NeedUser):
                engine.choose('弘文之星', {'甲书'})
            self.assertEqual(engine.resume.entry('弘文之星')['object'], '甲书')


class ShizhiResumeTests(unittest.TestCase):
    def test_nearby_list_continues_after_the_remembered_landmark(self):
        desktop = NearbyDesktop(['同济大学四平路校区图书馆', '同济大学博物馆', '同济大学大礼堂'])
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            memory.remember(THEME, '同济大学四平路校区图书馆', 0, section='附近地标')
            with patch('checkin.shizhi.Desktop', return_value=desktop), \
                 patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None, resume=memory)
            self.assertEqual(batch.choose_nearby(set()), '同济大学博物馆')
            self.assertEqual(desktop.opened, [('去打卡', 500)])
            batch.remember_position('同济大学博物馆')
            self.assertEqual(memory.entry(THEME, '附近地标')['object'], '同济大学博物馆')
            self.assertEqual(memory.entry(THEME, '附近地标')['index'], 1)

    def test_nearby_memory_does_not_hide_a_recorded_landmark(self):
        desktop = NearbyDesktop(['同济大学四平路校区图书馆', '同济大学博物馆', '同济大学大礼堂'])
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            memory.remember(THEME, '同济大学四平路校区图书馆', 0, section='附近地标')
            with patch('checkin.shizhi.Desktop', return_value=desktop), \
                 patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None, resume=memory)
            row = batch.history.reserve(THEME, '名称:同济大学博物馆',
                                        {'title': '同济大学博物馆'},
                                        independent_objects=True)
            batch.history.update(row['id'], status='已完成')
            self.assertEqual(batch.choose_nearby(set()), '同济大学大礼堂')
            self.assertEqual(desktop.opened, [('去打卡', 700)])

    def test_nearby_list_exhausted_after_the_anchor_resets_the_memory(self):
        desktop = NearbyDesktop(['同济大学四平路校区图书馆'])
        with tempfile.TemporaryDirectory() as root:
            memory = ResumeMemory(root)
            memory.remember(THEME, '同济大学四平路校区图书馆', 0, section='附近地标')
            with patch('checkin.shizhi.Desktop', return_value=desktop), \
                 patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None, resume=memory)
            with self.assertRaises(NeedUser):
                batch.choose_nearby({'同济大学四平路校区图书馆'})
            self.assertIsNone(memory.entry(THEME, '附近地标'))


class TwoRunContinuationTests(unittest.TestCase):
    """A real second session must continue instead of rescanning the prefix."""

    def test_the_next_run_picks_the_object_after_the_last_one_used(self):
        page = [label('甲书', 100), button(100), label('乙书', 160), button(160),
                label('丙书', 220), button(220), label('丁书', 280), button(280)]
        with tempfile.TemporaryDirectory() as root:
            first_run = engine_for_pages([page] * 4)
            first_run.resume = ResumeMemory(root)
            for used in (set(), {'甲书'}):
                name, _ = first_run.choose('弘文之星', used)
                first_run.remember_position('弘文之星', name)
            self.assertEqual((first_run.position_hint, first_run.resume.entry('弘文之星')['object']),
                             (1, '乙书'))

            # Reopening the app builds a new Engine and a new ResumeMemory.
            second_run = engine_for_pages([page] * 3)
            second_run.resume = ResumeMemory(root)
            name, _ = second_run.choose('弘文之星', {'甲书', '乙书'})

            self.assertEqual(name, '丙书')
            second_run.desktop.click.assert_called_once_with(page[5])
            messages = ' '.join(call.args[0] for call in second_run.report.call_args_list)
            self.assertIn('上次停在“乙书”', messages)
            self.assertIn('已跳过断点及之前的 2 个条目', messages)


if __name__ == '__main__':
    unittest.main()
