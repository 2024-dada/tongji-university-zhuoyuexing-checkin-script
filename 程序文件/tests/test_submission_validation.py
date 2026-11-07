import tempfile
import unittest
from unittest.mock import Mock, patch
from checkin.engine import Engine, NeedUser
from checkin.publishing import publish
from checkin.storage import History, today


def make_fill_engine(history, row, college=''):
    engine = Engine.__new__(Engine)
    engine.root, engine.config = history.path.parent, {}
    engine.desktop = Mock()
    engine.desktop.ocr_nodes.return_value = []
    engine.history = history
    engine.nodes = Mock(return_value=[])
    engine.is_checkin_form = Mock(return_value=True)
    engine.on_row = Mock()
    engine.report = Mock()
    engine.api = Mock()
    engine.api.teacher_information.return_value = {}
    engine.handoff = Mock()
    engine.read_field = Mock(side_effect=lambda labels, nodes=None:
        college if any('学院' in label for label in labels) else
        row['object'] if any('教师/校友姓名' in label for label in labels) else
        row.get('fields', {}).get('location', '') if any('地点' in label for label in labels) else
        row['text'])
    engine.fill_date = Mock(return_value=None)
    def fill_field(labels, value):
        if any('学院' in label for label in labels):
            return bool(college)
        if any('地点' in label for label in labels):
            return bool(value)
        return True
    engine.fill_field = Mock(side_effect=fill_field)
    engine.upload = Mock()
    return engine


class RequiredCollegeSubmissionGuard(unittest.TestCase):
    def _row(self, history, fields=None):
        row = history.reserve('明德之星', '汪品先', fields or {'location': '汪品先老师的办公室', 'college': '', 'minimum_chars': 80})
        return history.update(row['id'], text='真实感想内容' * 30)

    def test_missing_required_college_stops_before_fill_completion(self):
        history = History(tempfile.mkdtemp())
        row = self._row(history)
        engine = make_fill_engine(history, row)
        with self.assertRaises(NeedUser):
            engine._fill_visible(row)
        self.assertNotEqual(history.pending('明德之星')['phase'], '已填写，等待系统提交结果')

    def test_unresolved_field_after_continue_stops_instead_of_marking_ready(self):
        history = History(tempfile.mkdtemp())
        row = self._row(history, {'location': '', 'college': '土木工程学院', 'minimum_chars': 80})
        engine = make_fill_engine(history, row, college='土木工程学院')
        engine.fill_field.side_effect = lambda labels, value: not any('地点' in label for label in labels)
        with self.assertRaises(NeedUser):
            engine._fill_visible(row)
        self.assertNotEqual(history.pending('明德之星')['phase'], '已填写，等待系统提交结果')
        self.assertEqual(engine.handoff.call_count, 1)

    def test_submit_gate_rejects_blank_required_college_even_with_publish_button(self):
        engine = Engine.__new__(Engine)
        engine.desktop = Mock()
        engine.nodes = Mock(return_value=[
            dict(name='填写教师所在学院（部门）/校友曾就读学院', value='', kind='Edit', id='msaa', x=100, y=100, width=600, height=35),
            dict(name='确认发布', value='', kind='Button', id='msaa', x=100, y=900, width=300, height=50, enabled=True),
        ])
        engine.field_controls = Engine.field_controls
        engine.unique_click_targets = Engine.unique_click_targets
        engine.field_target = Engine.field_target.__get__(engine, Engine)
        engine.read_field = Engine.read_field.__get__(engine, Engine)
        row = {'id': 'row', 'theme': '明德之星', 'image': '', 'fields': {'college': ''}, 'object': '汪品先', 'status': '待提交'}
        with self.assertRaises(NeedUser):
            publish(engine, row)
        engine.desktop.click.assert_not_called()

    def test_manual_completion_after_handoff_is_verified_before_ready(self):
        history = History(tempfile.mkdtemp())
        row = self._row(history)
        engine = make_fill_engine(history, row)
        state = {'college': ''}
        body = row['text']
        engine.fill_field.side_effect = lambda labels, value: (
            bool(state['college']) if any('学院' in label for label in labels) else True)
        engine.api.teacher_information.return_value = {'college': '海洋与地球科学学院'}
        engine.read_field.side_effect = lambda labels, nodes=None: (
            state['college'] if any('学院' in label for label in labels) else
            '汪品先老师的办公室' if any('地点' in label for label in labels) else
            '汪品先' if any('教师' in label for label in labels) else body)
        engine.nodes.return_value = [dict(name=today(), kind='Text', value='', x=0, y=0, width=150, height=30)]
        engine.handoff.side_effect = lambda _message: state.update(college='海洋与地球科学学院')
        engine._fill_visible(row)
        saved = history.pending('明德之星')
        self.assertEqual(saved['phase'], '已填写，等待系统提交结果')
        self.assertEqual(saved['fields']['college'], '海洋与地球科学学院')

    def test_page_change_while_watcher_starts_prevents_click_and_status_change(self):
        engine = Engine.__new__(Engine)
        engine.desktop = Mock()
        engine.history = Mock()
        row = dict(id='fixture', status='待提交', theme='明德之星')
        observer = Mock()
        with patch('checkin.publishing.find_submit_button', side_effect=[{}, NeedUser('页面已变化')]), \
             patch('checkin.publishing.SubmitWatch', return_value=observer):
            with self.assertRaises(NeedUser):
                publish(engine, row)
        observer.arm.assert_not_called()
        observer.close.assert_called_once()
        engine.desktop.click.assert_not_called()
        engine.history.update.assert_not_called()

if __name__ == '__main__':
    unittest.main()
