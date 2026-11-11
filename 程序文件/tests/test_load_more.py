"""The object list paginates with “点击加载更多”, not page numbers."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from checkin.batch import BatchSession
from checkin.engine import Engine, ListExhausted, NeedUser
from checkin.storage import History, today


def label(name, y):
    return {'kind': 'Text', 'name': name, 'x': 80, 'y': y, 'width': 440, 'height': 32}


def button(y):
    return {'kind': 'Button', 'name': '去打卡', 'enabled': True,
            'x': 920, 'y': y, 'width': 90, 'height': 32}


def load_more(y):
    return {'kind': 'Text', 'name': '点击加载更多', 'x': 600, 'y': y,
            'width': 160, 'height': 30, 'enabled': True}


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
    engine.resume = None
    engine.wait_for_form = Mock(return_value=[
        {'kind': 'Text', 'name': '活动介绍：页面提供的说明', 'x': 80, 'y': 80, 'width': 700, 'height': 30},
        {'kind': 'Text', 'name': '注意：字数不少于 80 个字', 'x': 80, 'y': 120, 'width': 700, 'height': 30},
    ])
    return engine


USED = ('科坛趣话——科学、科学家与科学家精神', '雷雨', '飘', '你当像鸟飞往你的山', '人性的枷锁',
        '战争与和平', '南京大屠杀')


def first_batch():
    page = []
    for index, name in enumerate(USED):
        page += [label(name, 100 + index * 60), button(100 + index * 60)]
    return page + [load_more(900)]


class LoadMoreTests(unittest.TestCase):
    def test_reveals_the_next_batch_instead_of_declaring_the_list_finished(self):
        page = first_batch()
        more_page = page + [label('明朝那些事儿', 1000), button(1000)]
        engine = engine_for_pages([page, more_page, more_page])

        name, _ = engine.choose('弘文之星', set(USED))

        self.assertEqual(name, '明朝那些事儿')
        clicks = engine.desktop.click.call_args_list
        self.assertEqual(clicks[0].args[0], page[-1], '必须先点击“点击加载更多”')
        self.assertEqual(clicks[0].args[0]['name'], '点击加载更多')
        self.assertEqual(clicks[1].args[0], more_page[-1])
        engine.scroll_selector.assert_not_called()

    def test_a_load_more_button_that_changes_nothing_is_not_clicked_forever(self):
        page = first_batch()
        engine = engine_for_pages([page] * 20)

        with self.assertRaises(NeedUser) as caught:
            engine.choose('弘文之星', set(USED))

        clicks = [call.args[0] for call in engine.desktop.click.call_args_list]
        self.assertEqual([c['name'] for c in clicks].count('点击加载更多'), 1)
        self.assertNotIsInstance(caught.exception, ListExhausted)
        engine.scroll_selector.assert_not_called()
        messages = ' '.join(call.args[0] for call in engine.report.call_args_list)
        self.assertIn('列表暂时没有变化', messages)

    def test_exhausted_list_is_a_need_user_with_its_own_type(self):
        page = first_batch()[:-1] + [label('没有更多了', 900)]
        engine = engine_for_pages([page] * 20)
        with self.assertRaises(ListExhausted) as caught:
            engine.choose('弘文之星', set(USED))
        self.assertIsInstance(caught.exception, NeedUser)
        self.assertIn('没有未使用对象', str(caught.exception))


class BatchContinuesTests(unittest.TestCase):
    def test_other_stars_still_run_when_one_list_is_exhausted(self):
        import app as application

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as root:
            instance = application.App.__new__(application.App)
            instance.history = History(root)
            instance.batch = BatchSession(today(), ['弘文之星', '明德之星'])
            instance.batch.refresh({'弘文之星': 1, '明德之星': 1})
            instance.events = Mock()
            instance.report = Mock()
            instance.row = None
            instance.busy = False
            instance.clearing = False
            instance.uninstalling = False
            instance.image_unloading = False
            instance.confirm_button = Mock()
            instance.fill_button = Mock()
            instance.continue_button = Mock()
            instance.start_button = Mock()
            instance.clear_button = Mock()
            instance.image_uninstall_button = Mock()
            instance.background = Mock()
            instance.show_row = Mock()
            instance.batch_status = Mock()

            class SequentialEngine:
                desktop = Mock()

                def read_remaining(self, themes):
                    return {'弘文之星': 1, '明德之星': 1}

                def prepare(self, theme):
                    if theme == '弘文之星':
                        raise ListExhausted('弘文之星没有未使用对象。')
                    return instance.history.reserve(theme, '郑时龄', {})

                def fill(self, row):
                    pass

                def submit(self, row):
                    completed = instance.history.update(row['id'], status='已完成', phase='系统确认自动提交成功')
                    return 'success', completed

            instance.engine = Mock(return_value=SequentialEngine())
            instance.batch_step()

            completed = [row['object'] for row in instance.history.rows if row['status'] == '已完成']
            self.assertEqual(completed, ['郑时龄'])
            self.assertEqual(instance.batch.remaining['弘文之星'], 1)
            self.assertTrue(any('剩余次数保留' in str(c) for c in instance.report.call_args_list))


if __name__ == '__main__':
    unittest.main()
