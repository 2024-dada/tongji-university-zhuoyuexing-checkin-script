import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch, Mock
import app
from checkin.credentials import load_key

class MainSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(app, 'ROOT', Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.window = tk.Tk()
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.obj = app.App.__new__(app.App)
        self.obj.window = self.window
        self.obj.busy = False
        self.obj.key_status = tk.StringVar(self.window)
        self.obj.status = tk.StringVar(self.window)

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_dialog_confirm_encrypts_key_and_refreshes_status(self):
        self.obj.refresh_key_status()
        self.assertIn('未配置', self.obj.key_status.get())
        self.obj.api_key_dialog()
        dialog = next(w for w in self.window.winfo_children() if isinstance(w, tk.Toplevel))
        children = list(self.widgets(dialog))
        entry = next(w for w in children if isinstance(w, ttk.Entry))
        self.assertTrue(entry.cget('show'))
        key = 'local-ui-test-not-a-real-api-key'
        entry.insert(0, key)
        next(w for w in children if isinstance(w, ttk.Button) and w.cget('text') == '确定').invoke()
        self.assertEqual(load_key(app.ROOT), key)
        self.assertNotIn(key.encode(), (app.ROOT / '.private/deepseek.dpapi').read_bytes())
        self.assertEqual(self.obj.key_status.get(), 'API key 已配置')
        self.assertFalse(self.window.winfo_children())

    def test_empty_key_does_not_close_dialog_or_create_credentials(self):
        self.obj.api_key_dialog()
        dialog = self.window.winfo_children()[0]
        with patch.object(app.messagebox, 'showerror') as error:
            next(w for w in self.widgets(dialog) if isinstance(w, ttk.Button) and w.cget('text') == '确定').invoke()
            error.assert_called_once()
        self.assertTrue(dialog.winfo_exists())
        self.assertFalse((app.ROOT / '.private').exists())

    def test_busy_operation_blocks_key_edit(self):
        self.obj.busy = True
        with patch.object(app.messagebox, 'showinfo') as notice:
            self.obj.api_key_dialog()
            notice.assert_called_once()
        self.assertFalse(self.window.winfo_children())

    def test_missing_key_start_opens_configuration_before_batch_or_navigation(self):
        self.obj.api_key_dialog = Mock()
        self.obj.start()
        self.obj.api_key_dialog.assert_called_once()
        self.assertFalse(hasattr(self.obj, 'batch'))

if __name__ == '__main__': unittest.main()
