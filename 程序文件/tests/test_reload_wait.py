"""After Ctrl+R the mini-program blanks out; navigation must wait for it."""
import unittest
from unittest.mock import Mock

from checkin.engine import Engine


def text(name, y=300):
    return {'kind': 'Text', 'name': name, 'x': 80, 'y': y, 'width': 400, 'height': 30}


BLANK = [text('工作台', 447)]
FORM = [text('填写教师/校友姓名', 300), text('明德之星打卡', 100),
        text('填写感悟、寄语等', 400), text('确认发布', 900)]
MENU = [text('弘文之星', 100), text('明德之星', 300), text('矢志之星', 500)]


def engine_for_pages(pages):
    engine = Engine.__new__(Engine)
    engine.desktop = Mock()
    engine.report = Mock()
    engine.nodes = Mock(side_effect=pages)
    return engine


class ReloadWaitTests(unittest.TestCase):
    def test_waits_until_the_form_renders_again(self):
        engine = engine_for_pages([BLANK, BLANK, FORM])

        nodes = engine.wait_for_page('明德之星')

        self.assertEqual(nodes, FORM)
        self.assertEqual(engine.desktop.pause.call_count, 2)
        self.assertFalse(any('识别较慢' in call.args[0] for call in engine.report.call_args_list))

    def test_five_star_menu_also_counts_as_rendered(self):
        engine = engine_for_pages([BLANK, MENU])

        nodes = engine.wait_for_page('明德之星')

        self.assertEqual(nodes, MENU)
        self.assertEqual(engine.desktop.pause.call_count, 1)

    def test_a_page_that_never_renders_stops_waiting_and_reports(self):
        engine = engine_for_pages([BLANK] * 12)

        nodes = engine.wait_for_page('明德之星', attempts=10)

        self.assertEqual(nodes, BLANK)
        self.assertEqual(engine.nodes.call_count, 11)
        self.assertEqual(engine.desktop.pause.call_count, 10)
        messages = ' '.join(call.args[0] for call in engine.report.call_args_list)
        self.assertIn('识别较慢', messages)


if __name__ == '__main__':
    unittest.main()
