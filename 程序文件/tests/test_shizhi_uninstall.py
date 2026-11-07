import base64
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from checkin.shizhi_uninstall import FILES, owned_command, targets
from checkin.uninstall import ensure_install_marker


class UninstallScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / '内部文件' / '卓越星助手工程'
        for name in FILES + ('app.py', 'launcher.pyw', 'config.json', 'checkin/storage.py',
                              'checkin/credentials.py', 'automation/uninstall.ps1'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', 'utf-8')
        ensure_install_marker(self.root, self.root)
        self.desktop = self.base / 'desktop'
        self.desktop.mkdir()

    def command(self, path, root=None):
        text = "$appDir = '" + str(root or self.root) + "'; $entry = 'shizhi_launcher.pyw'"
        encoded = base64.b64encode(text.encode('utf-16le')).decode()
        path.write_text('powershell.exe -EncodedCommand ' + encoded, 'utf-8')

    def test_only_owned_files_and_launchers_are_planned(self):
        launchers = [self.base / '矢志之星.cmd', self.desktop / '矢志之星.cmd']
        for path in launchers:
            self.command(path)
        plan = targets(self.root, self.desktop)
        for path in launchers:
            self.assertIn(path, plan)
        self.assertIn(self.root / '矢志之星数据', plan)
        for name in ('app.py', 'config.json', '.private', '照片', 'checkin/engine.py', 'checkin/storage.py'):
            self.assertNotIn(self.root / name, plan)
        self.assertNotIn(self.root, plan)

    def test_unrelated_launcher_with_same_filename_is_retained(self):
        path = self.desktop / '矢志之星.cmd'
        self.command(path, Path(str(self.root) + '-other'))
        self.assertFalse(owned_command(path, self.root))
        self.assertNotIn(path, targets(self.root, self.desktop))

    def test_package_root_launcher_is_removed_only_when_owned(self):
        package_launcher = self.root.parent / '矢志之星.cmd'
        package_launcher.parent.mkdir(parents=True, exist_ok=True)
        self.command(package_launcher)
        self.assertIn(package_launcher, targets(self.root, self.desktop))

    def test_invalid_marker_blocks_uninstall(self):
        (self.root / '.zhuoyuex-checkin-installation').write_text('wrong', 'utf-8')
        with self.assertRaises(ValueError):
            targets(self.root, self.desktop)

    def test_junction_data_directory_is_rejected(self):
        data = self.root / '矢志之星数据'
        with patch.object(Path, 'is_junction', lambda path: path == data):
            with self.assertRaisesRegex(ValueError, '链接'):
                targets(self.root, self.desktop)


@unittest.skipUnless((Path(__file__).resolve().parents[1] / 'shizhi_app.pyw').is_file(),
                     'Standalone legacy window is not included in the unified package')
class UninstallWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        file = Path(__file__).resolve().parents[1] / 'shizhi_app.pyw'
        loader = importlib.machinery.SourceFileLoader('shizhi_gui_uninstall_test', str(file))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.module = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.module)

    def window(self, running=False):
        window = self.module.Window.__new__(self.module.Window)
        for name in ('root', 'cancel', 'worker', 'start_button', 'stop_button', 'uninstall_button', 'state', 'write'):
            setattr(window, name, Mock())
        window.worker.is_alive.return_value = running
        window.uninstall_requested = False
        window.finish_uninstall = Mock()
        return window

    def test_second_confirmation_cancel_does_not_stop_or_delete(self):
        window = self.window()
        with patch('checkin.uninstall.ensure_install_marker'), patch('checkin.shizhi_uninstall.targets', return_value=[]), patch('checkin.shizhi_uninstall.desktop_directory'), patch.object(self.module.messagebox, 'askyesno', side_effect=[True, False]):
            window.uninstall()
        window.cancel.set.assert_not_called()
        window.finish_uninstall.assert_not_called()

    def test_confirmed_running_task_waits_for_worker(self):
        window = self.window(running=True)
        with patch('checkin.uninstall.ensure_install_marker'), patch('checkin.shizhi_uninstall.targets', return_value=[]), patch('checkin.shizhi_uninstall.desktop_directory'), patch.object(self.module.messagebox, 'askyesno', return_value=True):
            window.uninstall()
        self.assertTrue(window.uninstall_requested)
        window.cancel.set.assert_called_once()
        window.finish_uninstall.assert_not_called()
        window.start()
        window.cancel.clear.assert_not_called()

    def test_confirmed_idle_task_can_launch_removal(self):
        window = self.window()
        with patch('checkin.uninstall.ensure_install_marker'), patch('checkin.shizhi_uninstall.targets', return_value=[]), patch('checkin.shizhi_uninstall.desktop_directory'), patch.object(self.module.messagebox, 'askyesno', return_value=True):
            window.uninstall()
        window.finish_uninstall.assert_called_once()
