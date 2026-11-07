import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.deepseek import DeepSeek, APIError, NoBalance
from checkin.engine import Engine, NeedUser
from checkin.storage import History


def response(value):
    return {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}


class TeacherRetryTests(unittest.TestCase):
    def test_no_source_still_displays_ds_followup_without_saving_a_guess(self):
        with tempfile.TemporaryDirectory() as root:
            api = DeepSeek('test-only')
            api.request = Mock(side_effect=[response({'college': ''}), response({'urls': []}),
                response({'college': '待核对学院', 'reason': '同名人物可能混淆'})])
            engine = Engine.__new__(Engine)
            engine.root, engine.config, engine.api = Path(root), {}, api
            engine.history = History(root)
            engine.read_field, engine.report, engine.on_row = Mock(return_value=''), Mock(), Mock()
            row = engine.history.reserve('明德之星', '李杰', {'college': ''})
            with patch('checkin.teachers.download', side_effect=[b'<rss/>', b'<html/>']):
                with self.assertRaisesRegex(NeedUser, '尚无可核对来源.*同名人物可能混淆'):
                    engine.complete_teacher_fields(row)
            self.assertEqual(row['fields']['college'], '')
            self.assertEqual(api.request.call_count, 3)

    def test_empty_answer_uses_normal_search_and_ds_extracts_official_college(self):
        with tempfile.TemporaryDirectory() as root:
            api = DeepSeek('test-only')
            source = 'https://faculty-civileng.tongji.edu.cn/lijie1/zh_CN/index.htm'
            evidence = '李杰，同济大学土木工程学院教授。'
            api.request = Mock(side_effect=[response({'college': ''}), response({
                'name': '李杰', 'is_teacher': True, 'college': '土木工程学院',
                'source': source, 'evidence': evidence})])
            engine = Engine.__new__(Engine)
            engine.root, engine.config, engine.api = Path(root), {}, api
            engine.history = History(root)
            engine.read_field, engine.report, engine.on_row = Mock(return_value=''), Mock(), Mock()
            row = engine.history.reserve('明德之星', '李杰', {'college': ''})
            bodies = [b'<rss/>', ('<li class="b_algo"><h2><a href="' + source + '">李杰</a></h2></li>').encode(),
                      ('<html><meta charset="utf-8">' + evidence + '</html>').encode()]
            with patch('checkin.teachers.download', side_effect=bodies):
                result = engine.complete_teacher_fields(row)
            self.assertEqual(result['fields']['college'], '土木工程学院')
            self.assertEqual(result['fields']['teacher_source'], source)
            self.assertEqual(api.request.call_count, 2)
            engine.report.assert_any_call('DeepSeek 再次询问结果：李杰 → 土木工程学院')

    def test_successful_first_answer_does_not_make_another_request(self):
        api = DeepSeek('test-only')
        api.request = Mock(return_value=response({'college': '土木工程学院'}))
        self.assertEqual(api.teacher_information('李杰')['college'], '土木工程学院')
        self.assertEqual(api.request.call_count, 1)

    def test_balance_failure_is_not_hidden_by_another_question(self):
        api = DeepSeek('test-only')
        api.request = Mock(side_effect=NoBalance('余额不足'))
        with self.assertRaises(NoBalance):
            api.teacher_information('李杰')
        self.assertEqual(api.request.call_count, 1)
