"""Replay the carousel and route-grid failure reported on 2026-10-12."""
from types import SimpleNamespace
import unittest

from checkin.shizhi import ShizhiBatch
from checkin.scrollbars import locate_scrollbar
from PIL import Image, ImageDraw


def node(name, x, y, width=200, height=30, kind='Text'):
    return dict(name=name, x=x, y=y, width=width, height=height, kind=kind, enabled=True)


class TraversalReproductionTests(unittest.TestCase):
    def batch(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.check = lambda: None
        batch.report = lambda line: None
        return batch

    def carousel(self, offset=0):
        batch = self.batch()
        position = [offset]
        processed = []
        def snapshot():
            return [node('热门地标', 20, 100), node('热门路线', 20, 550)] + [
                node(f'地标{i:02d}', 30 + j * 240, 380) for j, i in enumerate(range(position[0], min(20, position[0] + 8)))]
        def drag(start, end):
            position[0] = 0 if end[0] < start[0] else min(12, position[0] + 4)
        batch.nodes = snapshot
        batch.desktop = SimpleNamespace(scroll=lambda **kw: None, pause=lambda _: None,
            horizontal_scrollbar=lambda **kw: dict(left=20, right=1950, y=525,
                thumb_left=20 + position[0] * 1450/12, thumb_right=500 + position[0] * 1450/12),
            drag_pointer=drag)
        batch.click_label = lambda name, *args, **kw: ([], 'detail')
        batch.process_detail = lambda nodes, url, name, section: processed.append(name)
        batch.back_to = lambda url: snapshot()
        batch.process_carousel('热门地标', 'home')
        self.assertEqual(processed, [f'地标{i:02d}' for i in range(20)])

    def test_wheel_ignored_carousel_must_drag_and_reach_twentieth_card(self):
        self.carousel()

    def test_initial_right_offset_does_not_skip_left_cards(self):
        self.carousel(offset=4)

    def test_route_grid_actual_url_and_all_rows_are_processed(self):
        batch = self.batch()
        url = 'https://star.tongji.edu.cn/app/pages/index/dibiaoList?theme=18&type=17'
        names = ['同济大学博物馆', '同济大学大礼堂', '同济大学文远楼', '同济大学四平路校区图书馆']
        snapshot = [node('地标筛选', 40, 200), node('同济大学百年校史', 40, 245),
                    dict(value=url)] + [node(name, 40 + (i % 2) * 1000, 520 + (i // 2) * 400) for i, name in enumerate(names)]
        processed = []
        batch.nodes = lambda: snapshot
        batch.desktop = SimpleNamespace(scroll=lambda **kw: None, pause=lambda _: None,
            vertical_scrollbar=lambda **kw: dict(top=300, bottom=1200, x=1980, thumb_top=310, thumb_bottom=1190))
        batch.click_label = lambda name, *args, **kw: ([], 'detail')
        batch.process_detail = lambda nodes, detail, name, route: processed.append(name)
        batch.back_to = lambda _: snapshot
        batch.process_list(url, '同济大学百年校史')
        self.assertEqual(processed, names)

    def test_route_card_click_uses_image_above_title(self):
        batch = self.batch()
        url = 'https://star.tongji.edu.cn/app/pages/index/dibiaoList?theme=18'
        title = node('同济大学博物馆', 50, 540)
        image = node('', 35, 345, 960, 165, 'Image')
        nodes = [dict(value=url), node('地标筛选', 50, 220), image, title]
        clicked = []
        batch.desktop = SimpleNamespace(click=clicked.append)
        batch.wait_url_change = lambda _: ([], 'detail')
        batch.click_label(title['name'], nodes=nodes)
        self.assertEqual(clicked[0]['kind'], 'Image')
        self.assertLess(clicked[0]['y'], title['y'])

    def test_carousel_no_movement_away_from_end_must_pause(self):
        batch = self.batch()
        nodes = [node('热门地标', 20, 100), node('热门路线', 20, 550), node('首张地标', 50, 380)]
        batch.nodes = lambda: nodes
        batch.desktop = SimpleNamespace(horizontal_scrollbar=lambda **kw: dict(left=20, right=1900, y=520,
            thumb_left=30, thumb_right=500), drag_pointer=lambda *args: None)
        batch.click_label = lambda *args, **kw: ([], 'detail')
        batch.process_detail = lambda *args: None
        batch.back_to = lambda _: nodes
        with self.assertRaisesRegex(Exception, '未把当前数量当作全部'):
            batch.process_carousel('热门地标', 'home')

    def test_route_scrolls_then_loads_and_processes_new_items_before_exit(self):
        batch = self.batch()
        url = 'https://star.tongji.edu.cn/app/pages/index/dibiaoList?theme=18'
        page = [0]
        actions = []
        def snapshot():
            names = ('第一处地标', '第二处地标') if page[0] == 0 else ('第三处地标', '第四处地标')
            result = [dict(value=url), node('地标筛选', 40, 200)] + [node(name, 40+i*1000, 540) for i, name in enumerate(names)]
            if page[0] == 1:
                result.append(node('点击加载更多', 900, 1100))
            if page[0] == 2:
                result += [node('第五处地标', 40, 940), node('没有更多数据啦～', 700, 1200)]
            return result
        def scroll(**kw):
            actions.append('scroll')
            if page[0] == 0:
                page[0] = 1
        def click(target):
            actions.append('load')
            page[0] = 2
        batch.nodes = snapshot
        batch.desktop = SimpleNamespace(scroll=scroll, click=click, pause=lambda _: None,
            vertical_scrollbar=lambda **kw: dict(top=300, bottom=1250, x=1900, thumb_top=310 if page[0]==0 else 600, thumb_bottom=1240))
        batch.click_label = lambda name, **kw: ([], 'detail')
        batch.process_detail = lambda nodes, detail, name, route: actions.append(name)
        batch.back_to = lambda _: snapshot()
        batch.process_list(url, '路线名')
        self.assertEqual([name for name in actions if name.endswith('地标')],
            ['第一处地标', '第二处地标', '第三处地标', '第四处地标', '第五处地标'])
        self.assertLess(actions.index('第二处地标'), actions.index('scroll'))
        self.assertLess(actions.index('第四处地标'), actions.index('load'))
        self.assertLess(actions.index('load'), actions.index('第五处地标'))

    def test_grid_does_not_promote_address_or_filter_name_to_landmark(self):
        batch = self.batch()
        names = ['上海市杨浦高级中学', '同济大学四平路校区图书馆']
        nodes = [node('地标筛选', 40, 200), node('路线名', 40, 245)]
        for i, name in enumerate(names):
            x = 40 + i*1000
            nodes += [node('同济文化元素', x, 350), node(name, x, 540),
                      node('浙江省金华市义乌市分水塘村', x, 635), node('路线名', x, 670)]
        self.assertEqual(batch.list_titles(nodes, '路线名'), names)

    def test_fresh_second_row_image_overrides_previous_row_badge(self):
        batch = self.batch()
        nodes = [node('地标筛选', 40, 200), node('同济文化元素', 40, 350),
                 node('第一处地标', 40, 540), node('', 35, 745, 960, 165, 'Image'),
                 node('第二处地标', 40, 940)]
        self.assertEqual(batch.list_titles(nodes, '路线名'), ['第一处地标', '第二处地标'])


class ScrollbarPictureTests(unittest.TestCase):
    def test_horizontal_track_excludes_purple_margins(self):
        picture = Image.new('RGB', (1000, 100), '#8980ff')
        draw = ImageDraw.Draw(picture)
        draw.rectangle((20, 76, 949, 90), fill='white')
        draw.rectangle((100, 80, 350, 86), fill='#888888')
        bar = locate_scrollbar(picture, (0, 70, 990, 95))
        self.assertEqual((bar['left'], bar['right']), (20, 950))
        self.assertEqual((bar['thumb_left'], bar['thumb_right']), (100, 351))

    def test_vertical_arrows_exclude_white_sticky_header(self):
        picture = Image.new('RGB', (1000, 800), 'white')
        draw = ImageDraw.Draw(picture)
        draw.rectangle((986, 170, 994, 400), fill='#888888')
        draw.rectangle((987, 150, 993, 153), fill='#888888')
        draw.rectangle((987, 720, 993, 723), fill='#888888')
        bar = locate_scrollbar(picture, (975, 100, 1000, 750), False)
        self.assertEqual((bar['top'], bar['bottom']), (142, 732))
        self.assertLess(bar['thumb_top'] - bar['top'], 38)


if __name__ == '__main__':
    unittest.main()
