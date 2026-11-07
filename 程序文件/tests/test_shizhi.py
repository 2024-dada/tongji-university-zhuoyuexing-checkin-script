import tempfile
import unittest
from pathlib import Path

from checkin.shizhi import NeedUser, ShizhiBatch
from checkin.storage import History


class ShizhiHistoryTests(unittest.TestCase):
    def test_actual_duplicate_toast_is_completed_without_reposting(self):
        self.assertEqual(ShizhiBatch.submit_result(['该地点已打卡！']), 'already')
        self.assertEqual(ShizhiBatch.submit_result(['发布成功']), 'success')
        self.assertIsNone(ShizhiBatch.submit_result(['正在打卡...']))

    def test_form_verification_accepts_same_input_from_uia_and_msaa(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        title, text = '同济大学大礼堂', '本条地标的正文内容。' * 12
        fields = [dict(kind='Edit', name='', value=title, x=20, y=100, width=800, height=30),
                  dict(kind='Edit', name='', value=text, x=20, y=150, width=800, height=200)]
        duplicates = [dict(field, name=field['value'], value='', id='msaa') for field in fields]
        batch.verify_form_fields(title, {'text': text}, fields + duplicates)
        batch.verify_form_fields(title, {'text': text}, duplicates)
        with self.assertRaises(NeedUser):
            batch.verify_form_fields(title, {'text': '另外一段正文' * 20}, fields + duplicates)
        with self.assertRaises(NeedUser):
            batch.verify_form_fields(title, {'text': text}, fields +
                                     [dict(fields[1], x=1200)])

    def test_carousel_click_uses_picture_not_inert_title(self):
        from types import SimpleNamespace
        batch = ShizhiBatch.__new__(ShizhiBatch)
        title = dict(name='同济大学大礼堂', kind='Text', x=50, y=650,
                     width=200, height=30, enabled=True)
        nodes = [dict(name='热门地标', x=40, y=200, height=30), title,
                 dict(name='热门路线', x=40, y=850, height=30),
                 dict(value='https://star.tongji.edu.cn/app/pages-home/shizhi')]
        clicked = []
        batch.desktop = SimpleNamespace(click=clicked.append)
        batch.report = lambda message: None
        batch.wait_url_change = lambda before: ([], before + '/changed')
        batch.click_label(title['name'], '热门地标', nodes=nodes)
        self.assertEqual(len(clicked), 1)
        self.assertEqual(clicked[0]['kind'], 'Image')
        self.assertAlmostEqual(clicked[0]['x'], 150)
        self.assertAlmostEqual(clicked[0]['y'], 440)
        self.assertLess(clicked[0]['y'], title['y'])

    def test_current_card_processed_before_any_horizontal_scan(self):
        from types import SimpleNamespace
        batch = ShizhiBatch.__new__(ShizhiBatch)
        title = dict(name='同济大学大礼堂', kind='Text', x=50, y=650,
                     width=200, height=30, enabled=True)
        nodes = [dict(name='热门地标', x=40, y=200, height=30), title,
                 dict(name='热门路线', x=40, y=850, height=30)]
        events = []
        batch.nodes = lambda: nodes
        batch.check = lambda: None
        batch.report = lambda message: None
        batch.desktop = SimpleNamespace(scroll=lambda **kwargs: events.append('scroll'),
            horizontal_scrollbar=lambda **kwargs: dict(left=20, right=1000, y=800, thumb_left=40, thumb_right=980),
            drag_pointer=lambda *args: events.append('scroll'))
        batch.click_label = lambda *args, **kwargs: (events.append('click') or [], 'detail')
        batch.process_detail = lambda *args: events.append('process')
        batch.back_to = lambda *args: nodes
        batch.process_carousel('热门地标', 'home', nodes)
        self.assertEqual(events[:2], ['click', 'process'])
        self.assertEqual(events.count('click'), 1)
        self.assertIn('scroll', events)

    def test_duplicate_layers_select_the_same_card_text(self):
        text = dict(name='同济大学大礼堂', kind='Text', x=50, y=650,
                    width=200, height=30, enabled=True)
        parent = dict(text, kind='Custom', x=35, y=370, width=450, height=420)
        duplicate = dict(text, id='msaa')
        selected = ShizhiBatch.unique_label_target([parent, duplicate, text])
        self.assertEqual(tuple(selected[key] for key in ('x', 'y', 'width', 'height')),
                         (50, 650, 200, 30))

    def test_same_named_cards_in_separate_locations_stay_ambiguous(self):
        first = dict(name='同济大学大礼堂', x=50, y=650, width=200, height=30)
        second = dict(first, x=500)
        with self.assertRaises(NeedUser):
            ShizhiBatch.unique_label_target([first, second])
        with self.assertRaises(NeedUser):
            ShizhiBatch.unique_label_target([dict(first, width=0)])

    def test_campus_library_title_is_not_filtered_as_an_address(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        name = '同济大学四平路校区图书馆'
        nodes = [dict(name='热门地标', x=40, y=200, height=30),
                 dict(name=name, x=50, y=650, width=300, height=30),
                 dict(name='上海市杨浦区四平路1239号', x=50, y=700, height=30),
                 dict(name='热门路线', x=40, y=850, height=30)]
        self.assertEqual([title for title, _ in batch.card_titles(nodes, '热门地标')], [name])

    def test_history_is_separate_and_skipped_items_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = History(root)
            regular.reserve('弘文之星', '旧主题对象', {})
            special = History(root, '矢志之星打卡记录.txt')

            row = special.reserve('矢志之星', '2349', {'title': '同济大学大礼堂'})
            special.update(row['id'], status='超出范围')
            self.assertNotIn('2349', special.used('矢志之星'))

            row = special.reserve('矢志之星', '2349', {'title': '同济大学大礼堂'})
            special.update(row['id'], status='已跳过')
            self.assertNotIn('2349', special.used('矢志之星'))

            row = special.reserve('矢志之星', '2349', {'title': '同济大学大礼堂'})
            special.update(row['id'], status='已完成')
            self.assertIn('2349', special.used('矢志之星'))
            self.assertTrue((root / '打卡记录.txt').exists())
            self.assertTrue((root / '矢志之星打卡记录.txt').exists())
            self.assertEqual(len(History(root).rows), 1)
            self.assertEqual(len(History(root, '矢志之星打卡记录.txt').rows), 3)

    def test_temporary_image_cleanup_removes_only_the_current_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = ShizhiBatch.__new__(ShizhiBatch)
            batch.temp_root = Path(temporary) / '.shizhi-temp'
            identifier = '1' * 32
            image_dir = batch.temp_root / '照片' / identifier
            image_dir.mkdir(parents=True)
            image = image_dir / '上传配图.png'
            image.write_bytes(b'temporary image')
            keep = batch.temp_root / 'keep.txt'
            keep.write_text('not an image', encoding='utf-8')

            batch.safe_cleanup(identifier)

            self.assertFalse(image.exists())
            self.assertTrue(keep.exists())

    def test_startup_cleanup_removes_stale_item_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = ShizhiBatch.__new__(ShizhiBatch)
            batch.temp_root = Path(temporary) / '.shizhi-temp'
            batch.history = History(Path(temporary), '矢志之星打卡记录.txt')
            identifier = 'a' * 32
            stale = batch.temp_root / '照片' / identifier
            stale.mkdir(parents=True)
            (stale / 'review.json').write_text('{}', encoding='utf-8')
            unrelated = batch.temp_root / 'notes'
            unrelated.mkdir()
            pending = batch.history.reserve('矢志之星', '名称:图书馆', {'title': '图书馆'})
            image = batch.temp_root / '照片' / pending['id'] / '上传配图.png'
            image.parent.mkdir()
            image.write_bytes(b'approved image')
            batch.history.update(pending['id'], image=str(image))

            batch.clear_stale_images()

            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(image.is_file())

    def test_location_recognition_requires_explicit_site_state(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        self.assertEqual(batch.location_result([{'name': '去打卡'}]), '可打卡')
        self.assertEqual(batch.location_result([{'name': '去打卡', 'enabled': False}]), '定位中')
        self.assertEqual(batch.location_result([{'name': '去打卡'}, {'name': '正在定位'}]), '定位中')
        self.assertEqual(batch.location_result([{'name': '可打卡'}]), '可打卡')
        self.assertEqual(batch.location_result([{'name': '可打卡', 'enabled': False}]), '定位中')
        self.assertEqual(batch.location_result([{'name': '去打卡'}, {'name': '超出打卡范围'}]), '超范围')
        self.assertEqual(batch.location_result([{'name': '超出打卡范围'}]), '超范围')
        self.assertEqual(batch.location_result([{'name': '定位失败，请刷新重试'}]), '定位失败')
        self.assertEqual(batch.location_result([{'name': '页面加载中'}]), '未识别')

    def test_site_url_is_read_from_the_address_bar_value(self):
        address = 'https://star.tongji.edu.cn/app/pages-home/shizhi'
        self.assertEqual(ShizhiBatch.page_url([{'value': address}]), address)
        self.assertEqual(ShizhiBatch.page_url([{'name': address}]), address)

    def test_waits_for_stable_ready_state_after_location_loading(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.config = {}
        states = iter(['去打卡', '正在定位', '可打卡', '可打卡'])
        reads, logs, refreshed = [], [], []
        clock = [0]
        def nodes():
            label = next(states)
            reads.append(label)
            return [dict(value='https://star.tongji.edu.cn/app/pages-home/detail/dibiao?id=2349'),
                    dict(name=label, enabled=True)]
        batch.nodes = nodes
        batch.check = lambda: None
        batch.report = logs.append
        batch.desktop = SimpleNamespace(
            pause=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            refresh=lambda: refreshed.append(True))
        with patch('checkin.shizhi.time.monotonic', side_effect=lambda: clock[0]):
            result, final = batch.wait_for_location('2349')
        self.assertEqual(result, '可打卡')
        self.assertEqual(reads, ['去打卡', '正在定位', '可打卡', '可打卡'])
        self.assertFalse(refreshed)
        self.assertEqual(final[-1]['name'], '可打卡')

    def test_pending_location_times_out_without_claiming_out_of_range(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.config = {'location_wait_seconds': 10}
        batch.nodes = lambda: [dict(value='https://star.tongji.edu.cn/app/pages-home/detail/dibiao?id=2349'),
                              dict(name='去打卡', enabled=False)]
        batch.check = lambda: None
        batch.report = lambda message: None
        clock, refreshed = [0], []
        batch.desktop = SimpleNamespace(
            pause=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            refresh=lambda: refreshed.append(True))
        with patch('checkin.shizhi.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(NeedUser, '没有把它记作超范围'):
                batch.wait_for_location('2349')
        self.assertEqual(len(refreshed), 1)

    def test_go_checkin_label_does_not_wait_for_a_text_change(self):
        from types import SimpleNamespace
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.config = {}
        batch.check = lambda: None
        batch.report = lambda message: None
        pauses = []
        batch.nodes = lambda: [dict(value='https://star.tongji.edu.cn/app/pages-home/detail/dibiao?id=2349'),
                              dict(name='去打卡', enabled=True)]
        batch.desktop = SimpleNamespace(pause=pauses.append)
        result, _ = batch.wait_for_location('2349')
        self.assertEqual(result, '可打卡')
        self.assertEqual(len(pauses), 1)

    def test_website_out_of_range_after_entry_does_not_wait_for_form(self):
        from checkin.shizhi import OutOfRange
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.nodes = lambda: [dict(value='https://star.tongji.edu.cn/app/pages-home/detail/dibiao?id=2349'),
                              dict(name='超出打卡范围')]
        with self.assertRaises(OutOfRange):
            batch.wait_form('同济大学大礼堂')

    def test_ambiguous_submit_is_never_retried_on_restart(self):
        class Clicker:
            def __init__(self):
                self.clicks = 0
                self.connected = 0

            def click(self, node, allow_protected=False):
                self.clicks += 1

            def pause(self, seconds):
                pass

            def connect_browser(self):
                self.connected += 1

        with tempfile.TemporaryDirectory() as temporary:
            batch = ShizhiBatch.__new__(ShizhiBatch)
            batch.history = History(temporary, '矢志之星打卡记录.txt')
            row = batch.history.reserve('矢志之星', '2349', {'title': '同济大学大礼堂'})
            batch.desktop = Clicker()
            batch.report = lambda message: None
            batch.nodes = lambda: []
            batch.temp_root = Path(temporary) / '.shizhi-temp'
            batch.clear_stale_images = lambda: None

            with self.assertRaises(NeedUser):
                batch.submit_once([{'name': '确认发布', 'enabled': True}], row)
            self.assertEqual(batch.desktop.clicks, 1)
            self.assertEqual(batch.history.rows[0]['phase'], '结果待核实')

            batch.go_home = lambda: ([], 'home')
            sections = []
            batch.process_carousel = lambda section, url, nodes: sections.append(section) or []
            batch.run()
            self.assertEqual(batch.desktop.clicks, 1)
            self.assertEqual(batch.desktop.connected, 1)
            self.assertEqual(sections, ['热门地标', '热门路线'])
            self.assertEqual(batch.history.rows[0]['phase'], '结果待核实')


if __name__ == '__main__':
    unittest.main()
