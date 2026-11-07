from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from checkin.engine import Engine


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
    engine.wait_for_form = Mock(return_value=[
        {'kind': 'Text', 'name': '活动介绍：页面提供的说明', 'x': 80, 'y': 80, 'width': 700, 'height': 30},
        {'kind': 'Text', 'name': '注意：字数不少于 80 个字', 'x': 80, 'y': 120, 'width': 700, 'height': 30},
    ])
    return engine


class VisibleObjectSelectionTests(unittest.TestCase):
    def test_selects_first_unused_visible_row_without_ai_extraction(self):
        page = [label('实践论', 100), button(100), label('战争与和平', 160), button(160)]
        engine = engine_for_pages([page, page])

        name, fields = engine.choose('弘文之星', {'实践论'})

        self.assertEqual(name, '战争与和平')
        self.assertEqual(fields['minimum_chars'], 80)
        engine.desktop.click.assert_called_once_with(page[3])
        engine.wait_for_form.assert_called_once_with('弘文之星', '战争与和平')
        engine.api.json.assert_not_called()

    def test_scrolls_when_current_page_has_only_used_objects(self):
        first = [label('实践论', 100), button(100)]
        second = [label('实践论', 100), button(100), label('战争与和平', 160), button(160)]
        engine = engine_for_pages([first, second, second])

        name, _ = engine.choose('求索之星', {'实践论'})

        self.assertEqual(name, '战争与和平')
        engine.scroll_selector.assert_called_once_with(first)
        engine.desktop.click.assert_called_once_with(second[3])
        engine.api.json.assert_not_called()


if __name__ == '__main__':
    unittest.main()