import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checkin.storage import History


class RejectedRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.history=History(self.root)
        self.first=self.history.reserve('弘文之星','当呼吸化为空气',{})
        self.history.update(self.first['id'],text='原文',image='old-photo.png')
        self.history.confirm(self.first['id'])
        self.second=self.history.reserve('力行之星','悦读分享 当呼吸化为空气',{})
        self.history.confirm(self.second['id'])
        self.pending=self.history.reserve('求索之星','当呼吸化为空气相关讲座',{})

    def test_search_returns_only_matching_completed_in_selected_scope(self):
        all_rows=self.history.completed_matches('当呼吸')
        self.assertEqual({r['id'] for r in all_rows},{self.first['id'],self.second['id']})
        scoped=self.history.completed_matches('当 呼吸','弘文之星')
        self.assertEqual([r['id'] for r in scoped],[self.first['id']])
        with self.assertRaises(ValueError):
            self.history.completed_matches('   ')

    def test_remove_one_match_keeps_others_and_writes_restorable_backup(self):
        snapshot=self.history.path.read_text('utf-8')
        removed,backup=self.history.remove_completed(self.first['id'])
        self.assertEqual(removed['id'],self.first['id'])
        self.assertEqual(backup.read_text('utf-8'),snapshot)
        self.assertEqual([r['id'] for r in History(self.root).rows],[self.second['id'],self.pending['id']])
        self.assertNotIn('当呼吸化为空气',self.history.used('弘文之星'))
        self.assertEqual(removed['text'],'原文')
        self.assertEqual(removed['image'],'old-photo.png')

    def test_pending_or_unknown_id_cannot_be_removed(self):
        original=self.history.path.read_bytes()
        for identifier in (self.pending['id'],'unknown'):
            with self.assertRaises(ValueError):
                self.history.remove_completed(identifier)
        self.assertEqual(self.history.path.read_bytes(),original)
        self.assertFalse((self.root/'记录备份').exists())

    def test_backup_failure_prevents_any_removal(self):
        original=self.history.path.read_bytes()
        with patch('checkin.storage.atomic_write',side_effect=OSError('backup blocked')):
            with self.assertRaises(OSError):
                self.history.remove_completed(self.first['id'])
        self.assertEqual(self.history.path.read_bytes(),original)
        self.assertEqual(self.history.rows[0]['id'],self.first['id'])

    def test_write_failure_restores_memory_and_original_txt(self):
        original=self.history.path.read_bytes()
        with patch.object(self.history,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.history.remove_completed(self.first['id'])
        self.assertEqual(self.history.path.read_bytes(),original)
        self.assertEqual(self.history.rows[0]['id'],self.first['id'])
        self.assertEqual(len(list((self.root/'记录备份').glob('*.txt'))),1)

    def test_each_removal_retains_independent_backup(self):
        _,first_backup=self.history.remove_completed(self.first['id'])
        _,second_backup=self.history.remove_completed(self.second['id'])
        self.assertNotEqual(first_backup,second_backup)
        self.assertIn(self.first['id'],first_backup.read_text('utf-8'))
        self.assertIn(self.second['id'],second_backup.read_text('utf-8'))
        self.assertEqual([r['id'] for r in self.history.rows],[self.pending['id']])
