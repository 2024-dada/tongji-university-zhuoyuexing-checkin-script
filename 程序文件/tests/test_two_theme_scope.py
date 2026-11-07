import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import app
from checkin.batch import BatchSession, THEMES, remaining_counts
from checkin.engine import Engine
from checkin.storage import History, today

ACTIVE = ('弘文之星', '明德之星', '求索之星', '力行之星')
REMOVED = ('矢志之星',)


class TwoThemeScopeTests(unittest.TestCase):
    def test_default_and_explicit_scope_allow_only_first_two(self):
        self.assertEqual(THEMES, ACTIVE)
        self.assertEqual(BatchSession(today()).themes, ACTIVE)
        for theme in REMOVED:
            with self.subTest(theme=theme), self.assertRaises(ValueError):
                BatchSession(today(), [theme])

    def test_other_cards_bound_quota_rows_without_requiring_their_counts(self):
        page = []
        for i, name in enumerate(ACTIVE + REMOVED):
            page.append(dict(name=name, y=100*i, height=20))
            if name in ACTIVE:
                page.append(dict(name='今日可以打卡1次哦', y=100*i+30))
        self.assertEqual(remaining_counts(page), dict(dict.fromkeys(ACTIVE, 1), 矢志之星=None))

    def test_removed_prepare_and_navigation_reject_before_model_or_ui(self):
        engine = Engine.__new__(Engine)
        engine.api, engine.desktop, engine.history = Mock(), Mock(), Mock()
        engine.config, engine.report = {}, Mock()
        for theme in REMOVED:
            for method in (engine.prepare, engine.navigation):
                with self.subTest(theme=theme, method=method.__name__), self.assertRaises(ValueError):
                    method(theme)
        engine.api.balance.assert_not_called()
        engine.desktop.connect.assert_not_called()
        engine.history.reload.assert_not_called()

    def test_complete_batch_skips_disabled_pending_and_reads_quota_once(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            history = History(root)
            old = history.reserve('矢志之星', '旧活动结果待核实', {})
            history.update(old['id'], status='结果待核实')
            instance = app.App.__new__(app.App)
            instance.history, instance.batch = history, BatchSession(today())
            instance.events, instance.report = Mock(), Mock()
            engine = Mock()
            engine.read_remaining.return_value = dict.fromkeys(ACTIVE, 1)
            chosen = []

            def prepare(theme):
                self.assertIn(theme, ACTIVE)
                chosen.append(theme)
                return history.reserve(theme, theme + str(len(chosen)), {})

            def submit(row):
                result = 'already' if len(chosen) == 1 else 'success'
                return result, history.update(row['id'], status='已完成', submit_result=result)

            engine.prepare.side_effect, engine.submit.side_effect = prepare, submit
            instance.engine = Mock(return_value=engine)
            instance.batch_step()
            self.assertEqual(chosen, ['弘文之星', '弘文之星', '明德之星', '求索之星', '力行之星'])
            self.assertEqual(instance.batch.remaining, dict.fromkeys(ACTIVE, 0))
            engine.read_remaining.assert_called_once_with(ACTIVE)
            self.assertEqual(history.rows[0]['status'], '结果待核实')
            self.assertFalse(instance.batch.active)


if __name__ == '__main__':
    unittest.main()
