"""List traversal must skip completed landmarks before opening a card."""
import tempfile
import threading
import unittest
from unittest.mock import patch

from checkin.shizhi import ShizhiBatch, THEME


def control(name, x, y, kind='Text', width=500, height=30, value=''):
    return dict(name=name, value=value, x=x, y=y, kind=kind,
                width=width, height=height, enabled=True)


class ListDesktop:
    browser_hwnd = hwnd = 1
    url = 'https://star.tongji.edu.cn/app/pages/index/dibiaoList?theme=18'

    def __init__(self, title):
        self.title, self.opened, self.detail = title, [], False

    def check(self): pass
    def visible_bounds(self): return (0, 90, 2000, 1250)
    def pause(self, seconds): pass
    def scroll(self, **kwargs): pass
    def back(self): self.detail = False
    def vertical_scrollbar(self, **kwargs):
        return dict(top=300, bottom=1200, x=1980, thumb_top=310, thumb_bottom=1190)

    def snapshot(self):
        url = 'https://star.tongji.edu.cn/app/pages-home/detail/dibiao?id=2349' if self.detail else self.url
        nodes = [control('', 0, 0, 'Edit', value=url)]
        if self.detail:
            return nodes + [control(self.title, 40, 200)]
        return nodes + [control('地标筛选', 40, 200),
                        control('', 40, 350, 'Image', height=150),
                        control(self.title, 40, 540),
                        control('没有更多数据', 40, 1100)]

    def click(self, target, **kwargs):
        self.opened.append(target['name'])
        self.detail = True


class ListDedupTests(unittest.TestCase):
    def test_home_carousel_skips_completed_card_without_entering(self):
        with tempfile.TemporaryDirectory() as root:
            desktop = ListDesktop('同济大学大礼堂')
            desktop.url = 'https://star.tongji.edu.cn/app/pages-home/shizhi'
            desktop.snapshot = lambda: [control('', 0, 0, 'Edit', value=desktop.url),
                control('热门地标', 40, 100), control(desktop.title, 40, 380),
                control('热门路线', 40, 550)]
            desktop.horizontal_scrollbar = lambda **kwargs: dict(left=20, right=1950, y=520, thumb_left=20, thumb_right=1950)
            desktop.drag_pointer = lambda *args: None
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
            row = batch.history.reserve(THEME, '2349', {'title': desktop.title}, independent_objects=True)
            batch.history.update(row['id'], status='已完成')
            batch.process_carousel('热门地标', desktop.url)
            self.assertEqual(desktop.opened, [])

    def test_out_of_range_record_does_not_count_as_completed(self):
        class Selected(Exception): pass
        with tempfile.TemporaryDirectory() as root:
            desktop = ListDesktop('同济大学大礼堂')
            def click(target, **kwargs):
                desktop.opened.append(target['name'])
                raise Selected()
            desktop.click = click
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
            row = batch.history.reserve(THEME, '2349', {'title': desktop.title}, independent_objects=True)
            batch.history.update(row['id'], status='超出范围')
            with self.assertRaises(Selected):
                batch.process_list(desktop.url, '路线')
            self.assertEqual(desktop.opened, [desktop.title])

    def test_selector_translated_accessibility_text_is_used_for_dedup(self):
        with tempfile.TemporaryDirectory() as root:
            desktop = ListDesktop('同济创新创业控股有限公司')
            desktop.snapshot = lambda: [control('', 0, 0, 'Edit', value=desktop.url),
                dict(control('输入地标关键词', 40, 1742), offscreen=True),
                dict(control(desktop.title, 40, 1811), offscreen=True),
                dict(control('去打卡', 900, 1834, 'Text', width=100), offscreen=True),
                dict(control('同济大学博物馆', 40, 2011), offscreen=True),
                dict(control('去打卡', 900, 2034, 'Text', width=100), offscreen=True),
                dict(control('输入地标关键词', 40, 589), id='ocr'),
                dict(control('一同刨葫刨业肢句岷司', 44, 666), id='ocr'),
                dict(control('去打卡', 900, 681, 'Text', width=100), id='ocr'),
                dict(control('同济大学博物馆', 40, 858), id='ocr'),
                dict(control('去打卡', 900, 881, 'Text', width=100), id='ocr')]
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
            row = batch.history.reserve(THEME, '名称:' + desktop.title, {'title': desktop.title}, independent_objects=True)
            batch.history.update(row['id'], status='已完成')
            self.assertEqual(batch.choose_nearby(set()), '同济大学博物馆')

    def test_nearby_list_skips_similar_old_name_and_selects_next_landmark(self):
        with tempfile.TemporaryDirectory() as root:
            desktop = ListDesktop('同济大学四平校区图书馆')
            desktop.snapshot = lambda: [control('', 0, 0, 'Edit', value=desktop.url),
                control(desktop.title, 40, 300),
                control('上海市杨浦区四平路1239号同济大学四平路校区', 40, 320),
                dict(control('一同刨葫刨业肢句岷司错误识别很长文字', 44, 304), id='ocr'),
                control('去打卡', 900, 300, 'Button', width=100),
                control('同济大学博物馆', 40, 500),
                control('去打卡', 900, 500, 'Button', width=100)]
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
            # Legacy records may retain a name object without fields.
            row = batch.history.reserve(THEME, '名称:同济大学四平路校区图书馆', {}, independent_objects=True)
            batch.history.update(row['id'], status='已完成')
            self.assertEqual(batch.choose_nearby(set()), '同济大学博物馆')
            self.assertEqual(len(desktop.opened), 1)

    def test_similar_completed_names_are_skipped_at_the_list(self):
        variants = [('同济大学“一·二九”礼堂', '同济大学一二九礼堂'),
                    ('同济大学四平路校区图书馆', '同济大学四平校区图书馆'),
                    ('同济大学建筑设计研究院（集团）有限公司', '同济大学建筑设计研究院集团有限公司'),
                    ('同济大学四平路校区图书馆（沪西分馆）', '同济大学四平路校区图书馆')]
        for previous, current in variants:
            with self.subTest(current=current), tempfile.TemporaryDirectory() as root:
                desktop = ListDesktop(current)
                desktop.click = lambda *a, **k: self.fail('Similar completed card was opened')
                with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                    batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
                row = batch.history.reserve(THEME, '名称:' + previous, {'title': previous}, independent_objects=True)
                batch.history.update(row['id'], status='已完成')
                batch.process_list(desktop.url, '路线')

    def test_completed_card_is_skipped_without_opening_detail(self):
        with tempfile.TemporaryDirectory() as root:
            desktop = ListDesktop('同济大学大礼堂')
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda _: None)
            row = batch.history.reserve(THEME, '2349', {'title': desktop.title}, independent_objects=True)
            batch.history.update(row['id'], status='已完成')
            batch.process_list(desktop.url, '同济大学百年校史')
            self.assertEqual(desktop.opened, [], 'Completed list cards must never be opened')


if __name__ == '__main__':
    unittest.main()
