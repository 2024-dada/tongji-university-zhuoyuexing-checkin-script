import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.storage import History


class ClearHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.history=History(self.root)
        first=self.history.reserve('弘文之星','已完成对象',{})
        self.history.confirm(first['id'])
        self.history.reserve('明德之星','待提交对象',{})
        abandoned=self.history.reserve('求索之星','已放弃对象',{})
        self.history.update(abandoned['id'],status='已放弃')

    def test_all_statuses_clear_to_zero_bytes_with_full_backup(self):
        snapshot=self.history.path.read_text('utf-8')
        backup=self.history.clear_all()
        self.assertEqual(backup.read_text('utf-8'),snapshot)
        self.assertEqual(self.history.path.read_bytes(),b'')
        self.assertEqual(self.history.rows,[])
        self.assertEqual(History(self.root).rows,[])
        self.assertFalse(self.history.used('弘文之星'))
        self.assertIsNone(self.history.pending('明德之星'))

    def test_backup_failure_leaves_all_original_records(self):
        snapshot=self.history.path.read_bytes();rows=self.history.rows[:]
        with patch('checkin.storage.atomic_write',side_effect=OSError('backup blocked')):
            with self.assertRaises(OSError):self.history.clear_all()
        self.assertEqual(self.history.path.read_bytes(),snapshot)
        self.assertEqual(self.history.rows,rows)

    def test_write_failure_restores_memory_and_original_file(self):
        snapshot=self.history.path.read_bytes();rows=self.history.rows[:]
        with patch.object(self.history,'_save',side_effect=OSError('write failed')):
            with self.assertRaises(OSError):self.history.clear_all()
        self.assertEqual(self.history.path.read_bytes(),snapshot)
        self.assertEqual(self.history.rows,rows)
        self.assertEqual(len(list((self.root/'记录备份').glob('清空记录-*.txt'))),1)

    def test_configs_secrets_and_photos_are_retained(self):
        files=[self.root/'config.json',self.root/'.private'/'key.dpapi',self.root/'照片'/'photo.png']
        for path in files:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        self.history.clear_all()
        for path in files:self.assertEqual(path.read_bytes(),b'fixture')

    def test_empty_new_history_can_be_cleared_without_creating_backup(self):
        empty=History(self.root/'new')
        self.assertIsNone(empty.clear_all())
        self.assertEqual(empty.path.read_bytes(),b'')
        self.assertFalse((empty.path.parent/'记录备份').exists())


class ClearAppTests(unittest.TestCase):
    def setUp(self):
        import app
        self.module=app
        self.app=app.App.__new__(app.App)
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.app.history=History(self.temp.name)
        self.app.row=self.app.history.reserve('明德之星','待提交老师',{})
        self.app.batch=Mock();self.app.busy=False;self.app.clearing=False;self.app.awaiting=True
        self.app.uninstalling=False;self.app.image_unloading=False;self.app.started_at=None
        self.app.cancel=threading.Event();self.app.continuation=threading.Event();self.app.events=queue.Queue()
        for name in ('window','clear_button','start_button','confirm_button','fill_button','continue_button',
                     'text','log','theme','theme_picker','status','batch_status','image_label','object_label',
                     'image_uninstall_button','run_state','elapsed'):
            setattr(self.app,name,Mock())
        self.app.refresh_pending=Mock();self.app.show_row=Mock()
        self.app.fit_monitor=Mock()

    def test_canceled_confirmation_has_no_effect(self):
        snapshot=self.app.history.path.read_bytes()
        with patch.object(self.module.messagebox,'askyesno',return_value=False):self.app.clear_all()
        self.assertEqual(self.app.history.path.read_bytes(),snapshot)
        self.assertFalse(self.app.cancel.is_set());self.assertFalse(self.app.clearing)
        self.app.text.delete.assert_not_called()

    def test_clear_runtime_log_preserves_history_materials_and_running_task(self):
        snapshot = self.app.history.path.read_bytes()
        row, batch = self.app.row, self.app.batch
        self.app.busy = True
        self.app.events.put(('log', '后续进度'))
        self.app.clear_runtime_log()
        self.app.log.delete.assert_called_once_with('1.0', 'end')
        self.assertEqual(self.app.history.path.read_bytes(), snapshot)
        self.assertIs(self.app.row, row)
        self.assertIs(self.app.batch, batch)
        self.assertTrue(self.app.busy)
        self.assertFalse(self.app.cancel.is_set())
        self.assertFalse(self.app.clearing)
        self.assertEqual(self.app.events.qsize(), 1)
        self.app.text.delete.assert_not_called()
        self.app.theme.set.assert_not_called()

    def test_success_clears_page_undo_log_row_and_batch(self):
        with patch.object(self.module.messagebox,'askyesno',return_value=True):self.app.clear_all()
        self.assertEqual(self.app.history.path.read_bytes(),b'')
        self.assertIsNone(self.app.row);self.assertIsNone(self.app.batch);self.assertFalse(self.app.awaiting)
        self.app.text.delete.assert_called_once_with('1.0','end');self.app.text.edit_reset.assert_called_once()
        self.app.log.delete.assert_called_once_with('1.0','end')
        self.app.theme.set.assert_called_once_with(self.module.ALL_THEMES)
        self.assertFalse(self.app.clearing)

    def test_running_operation_stops_then_clears_only_on_idle(self):
        self.app.busy=True
        snapshot=self.app.history.path.read_bytes()
        with patch.object(self.module.messagebox,'askyesno',return_value=True):self.app.clear_all()
        self.assertTrue(self.app.cancel.is_set());self.assertTrue(self.app.continuation.is_set())
        self.assertTrue(self.app.clearing);self.assertEqual(self.app.history.path.read_bytes(),snapshot)
        self.app.text.delete.assert_not_called()
        self.app.events.put(('idle',None));self.app.events.put(('row',{'old':'late row'}));self.app.events.put(('log','late log'))
        self.app.poll()
        self.assertEqual(self.app.history.path.read_bytes(),b'')
        self.assertFalse(self.app.clearing);self.assertFalse(self.app.busy)
        self.app.show_row.assert_not_called();self.app.log.insert.assert_not_called()

    def test_clear_in_progress_blocks_new_background_work(self):
        self.app.clearing=True;task=Mock()
        self.app.background(task)
        task.assert_not_called();self.assertFalse(self.app.busy)

    def test_storage_failure_keeps_existing_page_and_records(self):
        snapshot=self.app.history.path.read_bytes();row=self.app.row
        with patch.object(self.module.messagebox,'askyesno',return_value=True),patch.object(self.module.messagebox,'showerror'),patch.object(self.app.history,'clear_all',side_effect=OSError('disk full')):
            self.app.clear_all()
        self.assertEqual(self.app.history.path.read_bytes(),snapshot)
        self.assertIs(self.app.row,row);self.app.text.delete.assert_not_called()
        self.assertFalse(self.app.clearing)
