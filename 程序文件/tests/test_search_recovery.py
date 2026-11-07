import tempfile
import queue
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.edge_search import image_results
from checkin.engine import Engine
from checkin.storage import History
from app import App


CARD = '<a class="iusc" m="{&quot;murl&quot;:&quot;https://example.com/book.png&quot;}"></a>'


class SearchRecoveryTests(unittest.TestCase):
    def test_page_address_is_not_selected_as_book_title(self):
        action = dict(name='去打卡', kind='Button', x=800, y=100, width=80, height=30)
        address = dict(name='https://star.tongji.edu.cn/app/pages-daka/hongwen', kind='Hyperlink', x=20, y=100, width=500, height=30)
        title = dict(name='飘', kind='Text', x=50, y=100, width=30, height=30)
        self.assertEqual(Engine.row_label_for_action([address, title], action), '飘')

    def test_loading_page_is_retried_before_reporting_no_image(self):
        ready = '<input id="sb_form_q" value="book cover">' + CARD
        with patch('checkin.edge_search.render_page', side_effect=['<html>loading</html>', ready, ready]):
            self.assertEqual(len(image_results('book cover', '.')), 1)

    def test_query_spacing_and_quotes_do_not_become_wrong_topic(self):
        ready = '<input id="sb_form_q" value="book  cover">' + CARD
        with patch('checkin.edge_search.render_page', return_value=ready):
            self.assertEqual(len(image_results('"book" cover', '.')), 1)


class CommittedCompletionTests(unittest.TestCase):
    def run_case(self, failure):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            history = History(root)
            row = history.reserve('弘文之星', '书名', {})
            history.update(row['id'], status='已完成', text='正文', image='previous.png')
            engine = Engine.__new__(Engine)
            engine.history, engine.data_root = history, Path(root)
            engine.desktop, engine.report, engine.on_row = Mock(), Mock(), Mock()
            if failure == 'refresh':
                engine.desktop.refresh.side_effect = OSError('refresh failed')
            with patch('checkin.publishing.publish', return_value=('success', row)), \
                 patch('checkin.cleanup.clear_record_photos', side_effect=OSError('locked') if failure == 'photo' else None):
                result, completed = engine.submit(row)
            self.assertEqual(result, 'success')
            self.assertEqual(completed['status'], '已完成')
            self.assertIn('书名', history.used('弘文之星'))
            self.assertFalse(completed.get('image'))
            engine.on_row.assert_called()

    def test_locked_old_photo_does_not_revoke_confirmed_success(self):
        self.run_case('photo')

    def test_refresh_failure_does_not_hide_confirmed_success(self):
        self.run_case('refresh')


class OperationVisibilityTests(unittest.TestCase):
    def test_handoff_shows_hidden_window_and_resume_hides_before_worker_continues(self):
        window = tk.Tk()
        try:
            window.withdraw()
            assistant = App.__new__(App)
            assistant.window = window
            assistant.events = queue.Queue()
            assistant.uninstalling = False
            assistant.run_state = tk.StringVar(window)
            assistant.status = tk.StringVar(window)
            assistant.continue_button = Mock()
            assistant.started_at = None
            assistant.fit_monitor = Mock()
            assistant.monitor_suspended = True
            assistant.continuation = threading.Event()
            assistant.events.put(('handoff', 'input needed'))
            assistant.poll()
            window.update_idletasks()
            self.assertNotEqual(window.state(), 'withdrawn')
            self.assertFalse(assistant.continuation.is_set())
            assistant.resume()
            self.assertEqual(window.state(), 'withdrawn')
            self.assertTrue(assistant.continuation.is_set())
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main()
