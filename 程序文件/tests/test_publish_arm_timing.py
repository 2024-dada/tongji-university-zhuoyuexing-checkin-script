"""A slow final form read must not consume the submit watch's toast window."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.publishing import publish
from checkin.storage import History


class PublishArmTimingTests(unittest.TestCase):
    def test_slow_button_revalidation_finishes_before_watcher_is_armed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            history = History(root)
            row = history.reserve('明德之星', '教师', {})
            engine = Mock(history=history)
            state = {'armed': False, 'expired': False, 'reads': 0}
            observer = Mock()
            observer.arm.side_effect = lambda: state.update(armed=True)
            observer.process.poll.side_effect = lambda: 0 if state['expired'] else None
            observer.result.side_effect = lambda: '' if state['expired'] else '打卡成功'

            def slow_final_read(*args):
                state['reads'] += 1
                if state['reads'] == 2 and state['armed']:
                    state['expired'] = True
                return {'name': '确认发布'}

            with patch('checkin.publishing.SubmitWatch', return_value=observer), \
                    patch('checkin.publishing.find_submit_button', side_effect=slow_final_read):
                outcome, completed = publish(engine, row)
            self.assertEqual(outcome, 'success')
            self.assertEqual(completed['status'], '已完成')
            engine.desktop.click.assert_called_once()
            observer.arm.assert_called_once()
