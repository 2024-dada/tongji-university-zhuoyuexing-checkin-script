import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from checkin.engine import Engine, NeedUser
from checkin.storage import History


def node(name, y, height=35, value='', kind='Edit'):
    return dict(name=name, value=value, kind=kind, id='msaa', x=100, y=y, width=600, height=height)


def activity(organizer='同济大学图书馆沪西分馆', body='填写感想、寄语等'):
    return [node('悦读分享时',100), dict(node('去搜索',100,kind='Text'),x=850),
            node(organizer,180),node(body,260,300),node('上传图片',600,kind='Text')]


class FieldVerificationTests(unittest.TestCase):
    def setUp(self):
        self.engine=Engine.__new__(Engine)
        self.engine.desktop=Mock();self.engine.nodes=Mock();self.engine.on_row=Mock()

    def test_prefilled_organizer_is_valid_even_when_extracted_fact_is_empty(self):
        self.engine.nodes.return_value=activity()
        self.assertTrue(self.engine.fill_field(('填写主办方','主办方'),''))
        self.assertEqual(self.engine.read_field(('填写主办方','主办方')),'同济大学图书馆沪西分馆')
        self.engine.desktop.paste.assert_not_called()

    def test_actual_missing_organizer_remains_missing_even_when_body_mentions_it(self):
        self.engine.nodes.return_value=activity('填写主办方','感想正文提到了主办方的工作')
        self.assertFalse(self.engine.fill_field(('填写主办方','主办方'),''))
        self.assertEqual(self.engine.read_field(('填写主办方','主办方')),'')
        self.engine.desktop.paste.assert_not_called()

    def test_name_changes_after_paste_and_value_stays_empty(self):
        before=activity('填写主办方')
        after=activity('同济大学图书馆沪西分馆')
        self.engine.nodes.side_effect=[before,after]
        self.assertTrue(self.engine.fill_field(('填写主办方','主办方'),'同济大学图书馆沪西分馆'))
        self.engine.desktop.click.assert_called_once_with(before[2])

    def test_body_readback_can_use_name_with_linebreaks(self):
        before=activity()
        after=activity(body='真实感想\n第二段')
        self.engine.nodes.side_effect=[before,after]
        self.assertTrue(self.engine.fill_field(('填写感想、寄语等','感想'),'真实感想第二段'))
        self.engine.desktop.click.assert_called_once_with(before[3])

    def test_selected_activity_title_is_read_after_placeholder_disappears(self):
        self.engine.nodes.return_value = activity()
        self.assertEqual(self.engine.read_field(('填写活动主题', '活动主题')), '悦读分享时')

    def test_selected_lecture_title_is_read_from_search_row(self):
        nodes = activity()
        nodes[0]['name'] = '同济大学高等讲座'
        self.engine.nodes.return_value = nodes
        self.assertEqual(self.engine.read_field(('填写讲座名称', '讲座名称')), '同济大学高等讲座')

    def test_submission_checks_current_body_matches_prepared_material(self):
        nodes = activity(body='旧材料内容' * 30)
        self.engine.nodes.return_value = nodes
        row = dict(theme='力行之星', text='本条准备的感想' * 30, fields={'minimum_chars': 80})
        self.assertIn('正文与本条准备材料不一致', self.engine.submission_gaps(row))

    def test_materials_are_not_pasted_into_a_different_selected_activity(self):
        self.engine.nodes.return_value = activity()
        row = dict(theme='力行之星', object='另一个活动', text='本条感想' * 30, fields={})
        with self.assertRaises(NeedUser):
            self.engine.fill(row)
        self.engine.desktop.paste.assert_not_called()

    def test_selected_book_title_is_read_from_search_row(self):
        nodes = activity()
        nodes[0]['name'] = '科坛趣话'
        self.engine.nodes.return_value = nodes
        self.assertEqual(self.engine.read_field(('填写书籍名称', '书籍名称', '书名')), '科坛趣话')

    def test_delayed_value_readback_is_retried_without_second_paste(self):
        before=activity('填写主办方')
        after=activity('')
        after[2]['value']='实际主办方'
        self.engine.nodes.side_effect=[before,before,after]
        self.assertTrue(self.engine.fill_field(('填写主办方','主办方'),'实际主办方'))
        self.engine.desktop.paste.assert_called_once_with('实际主办方')

    def test_matching_text_in_wrong_field_is_not_success(self):
        before=activity('填写主办方')
        after=activity('填写主办方',body='实际主办方')
        self.engine.nodes.side_effect=[before,after,after,after]
        self.assertFalse(self.engine.fill_field(('填写主办方','主办方'),'实际主办方'))

    def test_ambiguous_organizer_rows_stop_instead_of_selecting_random_field(self):
        values=activity()
        values.insert(3,node('另一个无名短字段',220))
        self.engine.nodes.return_value=values
        self.assertFalse(self.engine.fill_field(('填写主办方','主办方'),''))
        self.engine.desktop.click.assert_not_called()

    def test_fill_saves_prefilled_organizer_and_does_not_handoff(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as root:
            history=History(root)
            row=history.reserve('力行之星','悦读分享时',{'organizer':''})
            history.update(row['id'],text='已经填好的真实感想',image='image.png')
            self.engine.history=history
            self.engine.nodes.return_value=activity(body=row['text'])
            self.engine.verify_object=Mock();self.engine.report=Mock();self.engine.handoff=Mock();self.engine.upload=Mock()
            self.engine.submission_gaps=Mock(return_value=[])
            self.engine.fill(row)
            self.assertEqual(history.pending('力行之星')['fields']['organizer'],'同济大学图书馆沪西分馆')
            self.engine.handoff.assert_not_called()
            self.engine.desktop.paste.assert_not_called()
            self.engine.upload.assert_called_once_with('image.png')
