import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.engine import Engine, NeedUser
from checkin.storage import History


class CompletedCleanupTests(unittest.TestCase):
    def test_single_character_book_is_an_object_not_upload_label(self):
        action = dict(name='去打卡', kind='Button', x=900, y=100, width=80, height=30)
        title = dict(name='飘', kind='Text', x=100, y=100, width=30, height=30)
        self.assertEqual(Engine.row_label_for_action([title, action], action), '飘')

    def test_upload_control_cannot_be_reserved_as_a_book(self):
        action = dict(name='去打卡', kind='Button', x=900, y=100, width=80, height=30)
        upload = dict(name='上传封面图', kind='Text', x=100, y=100, width=100, height=30)
        self.assertEqual(Engine.row_label_for_action([upload, action], action), '')

    def test_confirmed_or_already_checked_cleans_payload_and_own_images(self):
        for result in ('success', 'already'):
            with self.subTest(result=result), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
                history = History(root)
                row = history.reserve('弘文之星', '飘', {'name': '飘'})
                folder = Path(root) / '照片' / row['id']
                folder.mkdir(parents=True)
                image = folder / '上传配图.png'
                image.write_bytes(b'old photo')
                history.update(row['id'], text='旧正文', image=str(image), status='已完成', submit_result=result)
                other = Path(root) / '照片' / ('f' * 32)
                other.mkdir()
                (other / 'keep.png').write_bytes(b'keep')
                engine = Engine.__new__(Engine)
                engine.history = history
                engine.data_root = Path(root)
                engine.desktop = Mock()
                engine.report = Mock()
                engine.on_row = Mock()
                with patch('checkin.publishing.publish', return_value=(result, row)):
                    outcome, cleaned = engine.submit(row)
                self.assertEqual(outcome, result)
                self.assertFalse(cleaned.get('image'))
                self.assertFalse(cleaned.get('text'))
                self.assertFalse(cleaned.get('fields'))
                self.assertFalse(folder.exists())
                self.assertTrue((other / 'keep.png').exists())
                self.assertIn('飘', history.used('弘文之星'))
                engine.desktop.refresh.assert_called_once()

    def test_unknown_result_keeps_materials_and_does_not_refresh(self):
        engine = Engine.__new__(Engine)
        engine.desktop = Mock()
        engine.history = Mock()
        with patch('checkin.publishing.publish', side_effect=NeedUser('结果待核实')):
            with self.assertRaises(NeedUser):
                engine.submit({'status': '待提交'})
        engine.desktop.refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
