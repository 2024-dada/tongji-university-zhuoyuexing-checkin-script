"""Exercise the batch entry point against a deterministic browser boundary."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from checkin.shizhi import ShizhiBatch, THEME
from checkin.storage import History, today


BASE = 'https://star.tongji.edu.cn/app/'
HOME = BASE + 'pages-home/shizhi'
ROUTE = BASE + 'pages/index/dibiaoList?theme=18'
DETAIL = BASE + 'pages-home/detail/dibiao?id=1'


def text(name, x, y, width=200, height=30, kind='Text'):
    return dict(name=name, x=x, y=y, width=width, height=height, kind=kind)


class BrowserBoundary:
    """Only the external desktop boundary is replaced; batch logic is real."""
    hwnd = 1
    browser_hwnd = None

    def __init__(self):
        self.url = HOME
        self.previous = []
        self.opened_routes = []

    def connect_browser(self): pass
    def check(self): pass
    def pause(self, seconds): pass
    def scroll(self, **options): pass
    def drag_pointer(self, start, end): pass

    def snapshot(self, **options):
        nodes = [dict(value=self.url)]
        if self.url == HOME:
            return nodes + [text('热门地标', 20, 100), text('已完成地标', 50, 380),
                text('热门路线', 20, 550), text('测试路线', 50, 800), text('最新活动', 20, 1000)]
        if self.url == ROUTE:
            return nodes + [text('地标筛选', 20, 150), text('测试路线', 20, 200),
                text('', 40, 300, 300, 120, 'Image'), text('已完成地标', 50, 460),
                text('没有更多数据啦～', 50, 1000)]
        return nodes + [text('已完成地标', 50, 150)]

    def click(self, target, **options):
        self.previous.append(self.url)
        if target['name'] == '测试路线':
            self.url = ROUTE
            self.opened_routes.append('测试路线')
        else:
            self.url = DETAIL

    def back(self):
        self.url = self.previous.pop()

    def horizontal_scrollbar(self, top, **options):
        # Landmark drag fails before the right edge; the route is fully visible.
        end = 500 if top < 500 else 1900
        return dict(left=20, right=1900, y=top+40, thumb_left=30, thumb_right=end)

    def vertical_scrollbar(self, **options):
        return dict(top=200, bottom=1100, x=1900, thumb_top=210, thumb_bottom=1090)


class SectionProgressTests(unittest.TestCase):
    def test_user_can_start_routes_without_rescanning_landmarks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            History(root, '矢志之星打卡记录.txt').import_completed(THEME, '1', today())
            desktop, messages = BrowserBoundary(), []
            with patch('checkin.shizhi.Desktop', return_value=desktop), \
                 patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), messages.append)
                batch.run(start_section='热门路线')
            self.assertEqual(desktop.opened_routes, ['测试路线'])
            self.assertNotIn('扫描首页热门地标……', messages)

    def test_blocked_landmark_carousel_still_processes_routes_without_reposting_completed_landmark(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            history = History(root, '矢志之星打卡记录.txt')
            history.import_completed(THEME, '1', today())
            desktop = BrowserBoundary()
            messages = []
            with patch('checkin.shizhi.Desktop', return_value=desktop), \
                 patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), messages.append)
                batch.run()
            self.assertEqual(desktop.opened_routes, ['测试路线'])
            self.assertEqual([(r['object'], r['status']) for r in batch.history.rows], [('1', '已完成')])
            self.assertNotIn('热门地标与热门路线已全部遍历。', messages)


if __name__ == '__main__':
    unittest.main()
