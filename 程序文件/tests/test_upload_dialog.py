import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from checkin.engine import Engine, NeedUser


class UploadDialogTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='矢志 上传 ')
        self.addCleanup(folder.cleanup)
        self.image = Path(folder.name) / '照片' / '3291b5d2ff6f4ba383649f9304881a16' / '上传配图.png'
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b'picture')
        self.desktop = Mock(browser_mode=True, hwnd=1, browser_hwnd=1)
        self.engine = Engine.__new__(Engine)
        self.engine.desktop = self.desktop
        self.engine.report = Mock()
        self.engine.handoff = Mock(side_effect=NeedUser)
        self.dialog_open = False
        self.filename = ''
        self.thumbnail = False
        self.desktop.windows.side_effect = lambda: [{'hwnd': 2, 'title': '打开'}] if self.dialog_open else []
        self.desktop.snapshot.side_effect = self.snapshot
        self.desktop.click.side_effect = self.click
        self.desktop.set_dialog_filename.return_value = False
        self.desktop.paste.side_effect = self.paste
        self.desktop.key.side_effect = self.confirm

    def snapshot(self):
        if self.desktop.hwnd == 2:
            return [dict(name='文件名:', id='1148', kind='Edit', value=self.filename)]
        self.assertFalse(self.dialog_open, '文件框打开时不能扫描网页')
        nodes = [dict(name='', kind='Edit', value='https://star.tongji.edu.cn/app/pages-daka/shizhi',
                      x=50, y=20, width=400, height=20),
                 dict(name='上传图片', kind='Text', x=30, y=400, width=80, height=20)]
        if self.thumbnail:
            nodes.append(dict(name='', id='msaa', kind='Image', x=120, y=400, width=80, height=80))
        return nodes

    def click(self, target):
        if target['name'] == '上传图片':
            self.dialog_open = True

    def paste(self, filename):
        self.assertEqual(self.desktop.hwnd, 2)
        self.filename = filename

    def confirm(self, key):
        self.assertEqual(key, 0x0D)
        self.assertEqual(self.desktop.hwnd, 2)
        self.assertEqual(self.filename, str(self.image))
        self.assertTrue(Path(self.filename).is_file())
        self.dialog_open = False
        self.thumbnail = True

    def test_edge_upload_reads_native_dialog_then_returns_to_guarded_page(self):
        self.engine.upload(self.image)
        self.assertTrue(self.thumbnail)
        self.assertEqual(self.desktop.hwnd, 1)

    def test_truncated_filename_is_never_confirmed(self):
        self.desktop.paste.side_effect = lambda _: self.paste(self.image.parent.name)
        with self.assertRaisesRegex(NeedUser, '完整路径'):
            self.engine.upload(self.image)
        self.desktop.key.assert_not_called()
        self.assertEqual(self.desktop.hwnd, 1)

    def test_still_open_dialog_does_not_trigger_page_scan(self):
        self.desktop.key.side_effect = None
        with self.assertRaisesRegex(NeedUser, '文件选择框仍未关闭'):
            self.engine.upload(self.image)
        self.assertEqual(self.desktop.hwnd, 1)
