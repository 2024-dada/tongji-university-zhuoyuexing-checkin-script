import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from checkin.engine import Engine, NeedUser
from checkin.teachers import TeacherDirectory, teacher_name


def selected_date_state(value='2026-10-09'):
    year, month, day = map(int, value.split('-'))
    return {'selected': {'年': {'number': year}, '月': {'number': month}, '日': {'number': day}},
            'confirm': dict(name='确认', kind='Button', x=300, y=200, width=50, height=24)}


class TeacherSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        facts = {name: dict(name=name, school="同济大学", college="学院", source="https://faculty.tongji.edu.cn/teacher", description="真实教师") for name in ("杜建忠", "林思劼")}
        (self.root / "教师资料.json").write_text(json.dumps(facts, ensure_ascii=False), "utf-8")
        self.engine = Engine.__new__(Engine)
        self.engine.root, self.engine.config = self.root, {}
        self.engine.api, self.engine.report = Mock(), Mock()
        self.engine.desktop = Mock()
        self.engine.desktop.visible_bounds.return_value = (0, 0, 1000, 900)
        self.engine.selector_visible_nodes = Mock(side_effect=lambda nodes: nodes)
        self.engine.verify_object = Mock()
        self.engine.details = Mock(return_value={"name": "林思劼", "minimum_chars":80})
        self.engine.select_list_item, self.engine.select_existing = Mock(), Mock()
        self.nodes = []
        for i, name in enumerate(("杜建忠", "林思劼")):
            self.nodes.extend([dict(name=name, kind="Text", x=80, y=100+i*100, width=70, height=30),
                               dict(name="去打卡",kind="Text",x=600,y=100+i*100,width=70,height=30)])
        self.engine.nodes = Mock(return_value=self.nodes)
        self.engine.wait_for_form = Mock(return_value=[
            dict(name='填写教师所在学院', kind='Text', x=80, y=300, width=200, height=30),
            dict(name='材料科学与工程学院', value='材料科学与工程学院', kind='Edit', x=80, y=300, width=400, height=30),
            dict(name='注意：字数不少于 80 个字', kind='Text', x=80, y=600, width=500, height=30),
        ])

    def test_name_only_list_selects_unused_without_ai_or_scrolling(self):
        name, fields = self.engine.choose("明德之星", {"杜建忠"})
        self.assertEqual(name, "林思劼")
        self.assertEqual(fields["location"], "林思劼老师的办公室")
        self.assertEqual(fields["college"], "材料科学与工程学院")
        self.engine.api.json.assert_not_called()
        self.engine.desktop.scroll.assert_not_called()
        self.engine.select_list_item.assert_called_once()

    def test_team_history_excludes_same_individual(self):
        name, fields = self.engine.choose("明德之星", {"学院：研究团队（杜建忠教授）"})
        self.assertEqual(name, "林思劼")

    def test_book_keyword_duplicate_is_skipped_before_selecting_from_list(self):
        self.engine.nodes.return_value = [
            dict(name='世界通史', kind='Text', x=80, y=100, width=120, height=30),
            dict(name='去打卡', kind='Text', x=600, y=100, width=70, height=30),
            dict(name='天演论', kind='Text', x=80, y=200, width=70, height=30),
            dict(name='去打卡', kind='Text', x=600, y=200, width=70, height=30),
        ]
        name, _ = self.engine.choose('弘文之星', {'世界通史（第二版）'})
        self.assertEqual(name, '天演论')
        self.engine.select_list_item.assert_called_once()

    def test_known_teacher_is_selected_from_current_list_without_web_lookup(self):
        self.engine.nodes.return_value = []
        self.engine.nodes.return_value = self.nodes
        with patch.object(TeacherDirectory, 'lookup', side_effect=AssertionError('web lookup must not block selection')):
            name, fields = self.engine.choose("明德之星", {"杜建忠"})
        self.assertEqual(name, "林思劼")
        self.engine.select_list_item.assert_called_once()
        self.engine.desktop.scroll.assert_not_called()

    def test_all_used_stops_without_false_selection(self):
        with self.assertRaises(NeedUser):
            self.engine.choose("明德之星", {"杜建忠", "林思劼"})
        self.engine.select_list_item.assert_not_called()
        self.engine.api.json.assert_not_called()

    def test_teacher_selection_scrolls_after_current_page_is_used(self):
        def row(name, y):
            return [dict(name=name, kind="Text", x=80, y=y, width=70, height=30),
                    dict(name="去打卡", kind="Text", x=600, y=y, width=70, height=30)]
        first = row("杜建忠", 100) + row("林思劼", 200)
        second = row("张三", 100) + row("李四", 200)
        self.engine.nodes.side_effect = [first, second, second, second]
        name, fields = self.engine.choose("明德之星", {"杜建忠", "林思劼"})
        self.assertEqual(name, "张三")
        self.engine.desktop.scroll.assert_called_once()
        self.engine.select_list_item.assert_called_once()
        self.engine.api.json.assert_not_called()

    def test_foreign_source_is_not_known_teacher(self):
        directory = TeacherDirectory(self.root, Mock())
        directory.facts["杜建忠"]["source"] = "https://tongji.edu.cn.example.com/name"
        self.assertIsNone(directory.known("杜建忠"))

    def test_existing_selector_is_not_opened_again(self):
        self.engine.handoff = Mock()
        self.engine.open_selector(self.nodes, "明德之星")
        self.engine.desktop.click.assert_not_called()
        self.engine.handoff.assert_not_called()

    def test_teacher_name_extracts_person_from_old_team(self):
        self.assertEqual(teacher_name("土木工程学院：团队（黄宏伟教授）"), "黄宏伟")
        self.assertEqual(teacher_name("地下设施管控团队"), "")

    def test_existing_location_is_found_by_row_without_placeholder(self):
        def node(name, kind, y, height=30):
            return dict(name=name, kind=kind, id="msaa", x=80, y=y, width=400, height=height)
        fields = [node("明德之星打卡", "Text", 0), node("", "Edit", 100),
                  node("老师名", "Edit", 200), dict(node("去搜索", "Text", 200),x=600),
                  node("", "Edit", 300), node("", "Edit", 400,200)]
        fields[1]["value"] = "新办公室"
        self.engine.nodes.return_value = fields
        self.assertTrue(self.engine.fill_field(("填写地点", "地点"), "新办公室"))
        self.engine.desktop.click.assert_not_called()
        self.engine.desktop.paste.assert_not_called()

    def test_teacher_location_uses_adjacent_row_and_name_only_values(self):
        def field(name, y, height=30):
            return dict(name=name, kind='Edit', x=80, y=y, width=400, height=height)
        nodes = [dict(name='上传图片', kind='Text', x=80, y=700, width=100, height=30),
                 field('2026-10-11', 40), field('林思劼老师的办公室', 100),
                 field('林思劼', 200),
                 dict(name='去搜索',kind='Text',x=600,y=200,width=80,height=30),
                 field('环境科学与工程学院', 300), field('正文内容' * 30, 400,200)]
        self.engine.nodes.return_value = nodes
        self.assertTrue(self.engine.fill_field(('填写地点', '地点'), '林思劼老师的办公室'))
        self.assertTrue(self.engine.fill_field(('填写教师所在学院', '教师所在学院', '学院'),
                                               '环境科学与工程学院'))
        self.engine.desktop.click.assert_not_called()
        self.engine.desktop.paste.assert_not_called()

    def test_conflicting_duplicate_values_are_not_accepted(self):
        nodes = [dict(name='', value='地点甲',kind='Edit',x=80,y=100,width=400,height=30),
                 dict(name='', value='地点乙',kind='Edit',x=80,y=100,width=400,height=30)]
        self.assertEqual(len(Engine.field_controls(nodes)), 2)
        name_nodes = [dict(n, name=n['value'], value='') for n in nodes]
        self.assertEqual(len(Engine.field_controls(name_nodes)), 2)

    def test_post_paste_conflicting_reads_do_not_claim_success(self):
        initial = dict(name='填写地点',value='',kind='Edit',x=80,y=100,width=400,height=30)
        after = [dict(initial, value='新办公室'), dict(initial, value='旧办公室', id='msaa')]
        self.engine.nodes.side_effect = [[initial], after, after, after]
        self.assertFalse(self.engine.fill_field(('填写地点', '地点'), '新办公室'))

    def test_populated_teacher_fields_do_not_request_manual_reentry_with_duplicate_controls(self):
        def field(value, y, height=30):
            return dict(name=value, value=value, kind='Edit', x=80, y=y,
                        width=400, height=height, id='msaa')
        nodes = [dict(name='上传图片', kind='Text', x=80, y=700, width=100, height=30),
                 field('林思劼老师的办公室', 100), field('林思劼', 200),
                 dict(name='去搜索',kind='Text',x=600,y=200,width=80,height=30),
                 field('环境科学与工程学院', 300), field('正文内容' * 30, 400,200)]
        duplicate_fields = [dict(n, id='uia') for n in nodes if n['kind'] == 'Edit']
        search_copy = dict(nodes[3], kind='Pane', id='uia')
        self.engine.nodes.return_value = nodes + duplicate_fields + [search_copy]
        self.assertTrue(self.engine.fill_field(('填写地点', '地点'), '林思劼老师的办公室'))
        self.assertTrue(self.engine.fill_field(('填写教师所在学院', '教师所在学院', '学院'),
                                               '环境科学与工程学院'))
        self.engine.desktop.click.assert_not_called()
        self.engine.desktop.paste.assert_not_called()

    def test_date_does_not_paste_into_location_and_confirms_aligned_today(self):
        def node(name,y):
            return dict(name=name,kind="Text",x=0,y=y,width=40,height=20)
        page=[node("时间",50), node("去选择",50)]
        picker=[node("2026年",400),node("10月",400),node("09日",400),node("确认",200)]
        self.engine.nodes.side_effect=[page,picker,[node("2026-10-09",50)]]
        self.engine.desktop.ocr_nodes.return_value=[]
        self.engine.desktop.screenshot.return_value=(b'',(0,0))
        self.engine.fill_field=Mock()
        self.engine.handoff=Mock()
        with patch('checkin.engine.picker_rows', return_value=selected_date_state()):
            self.engine.fill_date("2026-10-09")
        self.engine.fill_field.assert_not_called()
        self.engine.handoff.assert_not_called()
        self.assertEqual(self.engine.desktop.click.call_count,2)

    def test_today_centered_in_picker_is_confirmed_when_highlight_ocr_fails(self):
        def node(name, x, y, width=60, height=20, kind='Text'):
            return dict(name=name, kind=kind, x=x, y=y, width=width, height=height)

        confirm = node('确认', 900, 200, 50, 24, 'Button')
        cancel = node('取消', 80, 200, 50, 24, 'Button')
        picker = [cancel, confirm, node('2026年', 180, 500),
                  node('10月', 480, 500), node('13日', 780, 500)]
        form = [node('2026-10-13', 900, 50)]
        self.engine.nodes.side_effect = [picker, picker, picker, picker, form]
        self.engine.desktop.ocr_nodes.return_value = []
        self.engine.desktop.screenshot.return_value = (b'', (0, 0))
        self.engine.fill_field = Mock(return_value=False)
        self.engine.handoff = Mock()
        with patch('checkin.engine.picker_rows', return_value=None), \
             patch('checkin.engine.centered_picker_confirm', create=True,
                   return_value=confirm) as centered:
            self.engine.fill_date('2026-10-13')

        centered.assert_called_once()
        self.engine.desktop.click.assert_called_once_with(confirm)
        self.engine.handoff.assert_not_called()

    def test_centered_date_fallback_stops_if_form_readback_disagrees(self):
        def node(name, x, y, width=60, height=20, kind='Text'):
            return dict(name=name, kind=kind, x=x, y=y, width=width, height=height)

        confirm = node('确认', 900, 200, 50, 24, 'Button')
        picker = [node('取消', 80, 200, 50, 24, 'Button'), confirm,
                  node('2026年', 180, 500), node('10月', 480, 500), node('13日', 780, 500)]
        wrong = [node('2026-10-12', 900, 50)]
        self.engine.nodes.side_effect = [picker] * 4 + [wrong] * 5
        self.engine.desktop.ocr_nodes.return_value = []
        self.engine.desktop.screenshot.return_value = (b'', (0, 0))
        self.engine.fill_field = Mock(return_value=False)
        self.engine.handoff = Mock()
        with patch('checkin.engine.picker_rows', return_value=None), \
             patch('checkin.engine.centered_picker_confirm', create=True,
                   return_value=confirm):
            with self.assertRaisesRegex(NeedUser, '日期仍未核实'):
                self.engine.fill_date('2026-10-13')

        self.engine.desktop.click.assert_called_once_with(confirm)
        self.engine.handoff.assert_called_once()

    def test_wrong_rendered_date_is_not_reported_success(self):
        def node(name,y):
            return dict(name=name,kind="Text",x=0,y=y,width=40,height=20)
        page=[node("时间",50),node("去选择",50)]
        picker=[node("2026年",400),node("10月",400),node("09日",400),node("确认",200)]
        wrong=[node("2026-10-08",50)]
        self.engine.nodes.side_effect=[page,picker]+[wrong]*5
        self.engine.desktop.ocr_nodes.return_value=[]
        self.engine.desktop.screenshot.return_value=(b'',(0,0))
        self.engine.fill_field=Mock()
        self.engine.handoff=Mock()
        with patch('checkin.engine.picker_rows', return_value=selected_date_state()):
            with self.assertRaisesRegex(NeedUser, '日期仍未核实'):
                self.engine.fill_date("2026-10-09")
        self.engine.handoff.assert_called_once()

    def test_open_date_picker_is_confirmed_even_if_underlying_form_has_today(self):
        def node(name, y):
            return dict(name=name, kind='Text', x=0, y=y, width=40, height=20)
        overlay = [node('2026-10-09', 50), node('确认', 200), node('取消', 200),
                   node('2026年', 400), node('10月', 400), node('09日', 400)]
        self.engine.nodes.side_effect = [overlay, overlay, [node('2026-10-09', 50)]]
        self.engine.desktop.ocr_nodes.return_value = []
        self.engine.desktop.screenshot.return_value = (b'', (0, 0))
        self.engine.fill_field = Mock()
        self.engine.handoff = Mock()
        with patch('checkin.engine.picker_rows', return_value=selected_date_state()):
            self.engine.fill_date('2026-10-09')
        self.engine.desktop.click.assert_called_once()
        self.engine.handoff.assert_not_called()

    def test_date_overlay_without_accessible_bounds_uses_local_ocr(self):
        def node(name,y,height=20):
            return dict(name=name,kind="Text",x=0,y=y,width=40,height=height)
        page=[node("时间",50),node("去选择",50)]
        picker=[node("2026年",400),node("10月",403),node("09日",399),node("确认",200)]
        self.engine.nodes.side_effect=[page,picker,[node("2026-10-09",50)]]
        self.engine.desktop.ocr_nodes.return_value=picker
        self.engine.desktop.screenshot.return_value=(b'',(0,0))
        self.engine.fill_field=Mock()
        self.engine.handoff=Mock()
        with patch('checkin.engine.picker_rows', return_value=selected_date_state()):
            self.engine.fill_date("2026-10-09")
        self.engine.desktop.ocr_nodes.assert_called_once()
        self.engine.handoff.assert_not_called()
        self.assertEqual(self.engine.desktop.click.call_count,2)

    def test_upload_scrolls_before_clicking_label_hidden_by_publish_footer(self):
        image=self.root / "image.png"
        image.write_bytes(b"test")
        def node(name,y):
            return dict(name=name,kind="Text",x=80,y=y,width=90,height=20)
        before=[node("上传图片",690),node("存草稿",700)]
        visible=[node("上传图片",650),node("存草稿",700)]
        self.engine.nodes.side_effect=[before,visible]
        self.engine.desktop.windows.return_value=[]
        self.engine.handoff=Mock()
        self.engine.upload(image)
        self.engine.desktop.scroll.assert_called_once()
        self.engine.desktop.click.assert_called_once_with(visible[0])
