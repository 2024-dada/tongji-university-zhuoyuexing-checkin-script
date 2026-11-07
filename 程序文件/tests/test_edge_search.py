import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.edge_search import cleanup_stale_search_profiles, render_page


class EdgeProcessTests(unittest.TestCase):
    def test_successful_search_releases_its_child_lock_after_parent_exited(self):
        locked = [True]
        remove = shutil.rmtree
        def rmtree(path, *args, **kwargs):
            if locked[0]:
                raise OSError(32, 'Another process is using the private Edge journal')
            return remove(path, *args, **kwargs)
        def run(command, **kwargs):
            if command[0] == 'taskkill':
                self.assertEqual(command, ['taskkill', '/PID', '8765', '/T', '/F'])
                locked[0] = False
            return Mock(returncode=0, stdout=b'[8765]', stderr=b'')
        with patch('checkin.edge_search.edge_executable', return_value='msedge.exe'), \
             patch('checkin.edge_search.subprocess.Popen', return_value=self.process), \
             patch('checkin.edge_search.shutil.rmtree', side_effect=rmtree), \
             patch('checkin.edge_search.subprocess.run', side_effect=run), \
             patch('checkin.edge_search.time.sleep'):
            result = render_page('https://www.bing.com/images/search?q=book', self.root, self.cancel)
        self.assertEqual(result, '<html>rendered</html>')
        self.assertEqual(list((self.root / '照片' / '.edge-search').iterdir()), [])

    def test_failed_private_edge_reports_exit_code(self):
        with tempfile.TemporaryDirectory() as root:
            process = Mock(returncode=21)
            process.communicate.return_value = (b'', b'')
            process.poll.return_value = 21
            with patch('checkin.edge_search.edge_executable', return_value='msedge.exe'), patch('checkin.edge_search.subprocess.Popen', return_value=process):
                with self.assertRaisesRegex(OSError, '21'):
                    render_page('https://www.bing.com/images/search?q=building', root)

    def setUp(self):
        driver_patch = patch('checkin.edge_search.webdriver_executable', return_value=None)
        driver_patch.start()
        self.addCleanup(driver_patch.stop)
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cancel = Mock()
        self.cancel.is_set.return_value = False
        self.process = Mock(pid=12345, returncode=0)
        self.process.poll.return_value = 0
        self.process.communicate.return_value = (b'<html>rendered</html>', None)

    def test_uses_private_profile_and_cleans_it_without_touching_existing_edge(self):
        with patch('checkin.edge_search.edge_executable', return_value='msedge.exe'), \
             patch('checkin.edge_search.subprocess.Popen', return_value=self.process) as launch, \
             patch('checkin.edge_search.subprocess.run') as terminate:
            self.assertEqual(render_page('https://www.bing.com/images/search?q=book', self.root, self.cancel), '<html>rendered</html>')
        args = launch.call_args.args[0]
        profile = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--user-data-dir=')))
        self.assertEqual(profile.parent, (self.root / '照片' / '.edge-search').resolve())
        self.assertIn('--dump-dom', args)
        self.assertFalse(any('remote-debugging' in arg or 'ignore-certificate' in arg for arg in args))
        self.assertFalse(profile.exists())
        terminate.assert_not_called()

    def test_cancel_terminates_only_the_owned_private_browser(self):
        self.cancel.is_set.side_effect = [False, True]
        self.process.poll.return_value = None
        with patch('checkin.edge_search.edge_executable', return_value='msedge.exe'), \
             patch('checkin.edge_search.subprocess.Popen', return_value=self.process), \
             patch('checkin.edge_search.subprocess.run') as terminate:
            with self.assertRaisesRegex(RuntimeError, '停止'):
                render_page('https://www.bing.com/images/search?q=book', self.root, self.cancel)
        if terminate.called:
            self.assertEqual(terminate.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
        else:
            self.process.kill.assert_called_once()
        self.assertEqual(list((self.root / '照片' / '.edge-search').iterdir()), [])

    def test_timeout_exits_and_removes_its_temporary_profile(self):
        self.process.poll.return_value = None
        self.process.communicate.side_effect = subprocess.TimeoutExpired('edge', .25)
        with patch('checkin.edge_search.edge_executable', return_value='msedge.exe'), \
             patch('checkin.edge_search.subprocess.Popen', return_value=self.process), \
             patch('checkin.edge_search.subprocess.run'), \
             patch('checkin.edge_search.time.monotonic', side_effect=[0, 0, 41]):
            with self.assertRaisesRegex(TimeoutError, '超时'):
                render_page('https://www.bing.com/images/search?q=book', self.root, self.cancel)
        self.assertEqual(list((self.root / '照片' / '.edge-search').iterdir()), [])

    def test_cleans_old_generated_profiles_but_preserves_recent_and_unrelated_folders(self):
        cache = self.root / '照片' / '.edge-search'
        old = cache / 'session-old'
        recent = cache / 'session-recent'
        unrelated = cache / 'manual-data'
        for folder in (old, recent, unrelated):
            folder.mkdir(parents=True)
            (folder / 'cache.bin').write_bytes(b'x')
        old_time = time.time() - 3600
        os.utime(old, (old_time, old_time))

        removed = cleanup_stale_search_profiles(self.root, max_age_seconds=600)

        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())

    def test_retries_profile_cleanup_after_a_transient_windows_lock(self):
        profile = self.root / '照片' / '.edge-search' / 'session-locked-once'
        profile.mkdir(parents=True)
        (profile / 'cached-image.png').write_bytes(b'private temporary image')
        old_time = time.time() - 3600
        os.utime(profile, (old_time, old_time))
        original_rmtree = shutil.rmtree
        calls = []

        def remove_with_one_transient_error(path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError('temporary browser lock')
            return original_rmtree(path)

        with patch('checkin.edge_search.shutil.rmtree', side_effect=remove_with_one_transient_error), \
             patch('checkin.edge_search.time.sleep'):
            self.assertEqual(cleanup_stale_search_profiles(self.root), 1)

        self.assertEqual(len(calls), 2)
        self.assertFalse(profile.exists())
