import tempfile
import unittest
from unittest.mock import Mock, patch

from checkin.deepseek import APIError, DeepSeek
from checkin.engine import Engine, NeedUser
from checkin.storage import History


class TeacherCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.history = History(temporary.name)
        self.row = self.history.reserve('明德之星', '汪品先', {'date': '2026-10-12', 'college': ''})
        self.engine = Engine.__new__(Engine)
        self.engine.root, self.engine.config = self.history.path.parent, {}
        self.engine.history = self.history
        self.engine.api = Mock()
        self.engine.api.teacher_information.return_value = dict(college='海洋与地球科学学院', description='海洋地质学')
        self.engine.read_field = Mock(return_value='')
        self.engine.report = Mock()
        self.engine.on_row = Mock()

    def test_missing_college_is_generated_saved_and_not_regenerated_on_retry(self):
        with patch('checkin.engine.today', return_value='2026-10-13'):
            row = self.engine.complete_teacher_fields(self.row)
            self.engine.complete_teacher_fields(row)
        self.assertEqual(row['fields']['college'], '海洋与地球科学学院')
        self.assertEqual(row['fields']['date'], '2026-10-13')
        self.assertEqual(row['fields']['location'], '汪品先老师的办公室')
        self.engine.api.teacher_information.assert_called_once_with('汪品先')
        self.assertEqual(History(self.history.path.parent).pending('明德之星')['fields'], row['fields'])

    def test_page_college_takes_priority_without_ai_call(self):
        self.engine.read_field.return_value = '页面学院'
        row = self.engine.complete_teacher_fields(self.row)
        self.assertEqual(row['fields']['college'], '页面学院')
        self.engine.api.teacher_information.assert_not_called()

    def test_unknown_college_is_not_invented_or_marked_ready(self):
        self.engine.api.teacher_information.return_value = {}
        with self.assertRaises(NeedUser):
            self.engine.complete_teacher_fields(self.row)
        self.assertEqual(self.row['phase'], '已选对象')

    def test_ai_cannot_use_university_name_instead_of_college(self):
        api = DeepSeek('fixture')
        api.json = Mock(return_value={'college': '同济大学'})
        with self.assertRaises(APIError):
            api.teacher_information('汪品先')

    def test_selector_overlay_is_not_a_ready_form(self):
        nodes = [{'name': '明德之星打卡'}, {'name': '填写教师所在学院'}, {'name': '确认发布'}, {'name': '去打卡'}]
        self.assertFalse(self.engine.is_checkin_form('明德之星', nodes))

    def test_date_overlay_is_not_a_ready_form_even_with_today_underneath(self):
        nodes = [{'name': '明德之星打卡'}, {'name': '2026-10-13'}, {'name': '确认'}, {'name': '取消'}]
        self.assertFalse(self.engine.is_checkin_form('明德之星', nodes))

    def test_navigation_returns_from_other_theme_before_opening_target(self):
        def node(name, y=100):
            return dict(name=name, kind='Text', x=80, y=y, width=180, height=30)
        self.engine.config = {}
        self.engine.desktop = Mock()
        target = [node('弘文之星打卡')]
        self.engine.nodes = Mock(side_effect=[[node('明德之星打卡')], [node('弘文之星')], target])
        self.assertEqual(self.engine.navigation('弘文之星'), target)
        self.engine.desktop.back.assert_called_once()
        self.engine.desktop.click.assert_called_once()

    def test_menu_navigation_does_not_stop_in_same_theme_form(self):
        def node(name, y=100):
            return dict(name=name, kind='Text', x=80, y=y, width=180, height=30)
        self.engine.config = {}
        self.engine.desktop = Mock()
        menu = [node('弘文之星'), node('明德之星', 200)]
        self.engine.nodes = Mock(side_effect=[[node('明德之星打卡')], menu])
        self.assertEqual(self.engine.navigation('明德之星', stop_at_menu=True), menu)
        self.engine.desktop.back.assert_called_once()
        self.engine.desktop.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
