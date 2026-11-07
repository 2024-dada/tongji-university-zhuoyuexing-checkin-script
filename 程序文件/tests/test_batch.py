import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.batch import BatchSession, THEMES, remaining_counts


def node(name, y):
    return dict(name=name, kind='Text', x=10, y=y, width=200, height=25)


class BatchTests(unittest.TestCase):
    def test_cards_keep_own_quota_and_ignore_excluded_star(self):
        values=[]
        stars=['弘文之星','明德之星','矢志之星','求索之星','力行之星']
        for i, (theme, count) in enumerate(zip(stars, [1,2,99,0,3])):
            values += [node(theme, i*150), node('今日可以打卡'+str(count)+'次哦', i*150+40)]
        # Excluded card must not make the preceding quota ambiguous.
        self.assertEqual(remaining_counts(values), dict(zip(THEMES,[1,2,0,3]), 矢志之星=99))

    def test_exhausted_and_unknown_are_distinct(self):
        values=[node('弘文之星',0),node('今天打卡的次数已用完',40),node('明德之星',150)]
        counts=remaining_counts(values)
        self.assertEqual(counts['弘文之星'],0)
        self.assertIsNone(counts['明德之星'])
        session=BatchSession('2026-10-10')
        with self.assertRaises(ValueError):
            session.refresh(counts)

    def test_confirmed_items_decrement_and_stale_page_cannot_restore_budget(self):
        session=BatchSession('2026-10-10')
        counts=dict(zip(THEMES,[1,2,0,1]))
        session.refresh(counts)
        self.assertEqual(session.next_theme(),THEMES[0])
        session.confirm(THEMES[0])
        session.refresh(counts)
        self.assertEqual(session.remaining[THEMES[0]],0)
        self.assertEqual(session.next_theme(),THEMES[1])
        session.confirm(THEMES[1]);session.confirm(THEMES[1])
        session.refresh(counts)
        self.assertEqual(session.next_theme(),'力行之星')
        session.confirm('力行之星')
        self.assertIsNone(session.next_theme())
        self.assertEqual(sum(session.confirmed.values()),4)

    def test_pending_priority_and_stop(self):
        session=BatchSession('2026-10-10')
        session.refresh({t:2 for t in THEMES})
        self.assertEqual(session.next_theme(['明德之星', '力行之星']),'明德之星')
        session.active=False
        self.assertIsNone(session.next_theme())

    def test_one_theme_scope_does_not_require_other_quota(self):
        session=BatchSession('2026-10-10',['明德之星'])
        session.refresh({'明德之星':3})
        self.assertEqual(session.next_theme(),'明德之星')
        self.assertEqual(session.remaining,{'明德之星':3})


class AppBatchTests(unittest.TestCase):
    def setUp(self):
        import app
        from checkin.storage import History, today
        self.module=app
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.app=app.App.__new__(app.App)
        self.app.history=History(self.tmp.name)
        self.app.row=self.app.history.reserve('明德之星','老师',{})
        self.app.busy=False
        self.app.batch=BatchSession(today(),['明德之星'])
        self.app.batch.refresh({'明德之星':2})
        self.app.show_row=Mock();self.app.report=Mock()
        self.app.window=Mock();self.app.batch_status=Mock();self.app.background=Mock()
        self.app.theme=Mock();self.app.theme.get.return_value='明德之星'
        self.app.continuation=__import__('threading').Event()
        self.app.text=Mock();self.app.log=Mock();self.app.object_label=Mock()
        self.app.image_label=Mock();self.app.status=Mock();self.app.run_state=Mock();self.app.elapsed=Mock()
        self.app.confirm_button=Mock();self.app.fill_button=Mock();self.app.continue_button=Mock()
        self.app.clearing=False;self.app.uninstalling=False;self.app.image_unloading=False

    def test_declined_confirmation_never_updates_history_or_advances(self):
        with patch.object(self.module.messagebox,'askyesno',return_value=False):
            self.app.confirm()
        self.assertEqual(self.app.row['status'],'待提交')
        self.assertEqual(self.app.batch.remaining['明德之星'],2)
        self.app.window.after.assert_not_called()

    def test_success_confirmation_saves_before_scheduling_next_item(self):
        with patch.object(self.module.messagebox,'askyesno',return_value=True):
            self.app.confirm()
        self.assertEqual(self.app.history.rows[-1]['status'],'已完成')
        self.assertEqual(self.app.batch.remaining['明德之星'],1)
        callback=self.app.window.after.call_args.args[1]
        callback()
        self.app.background.assert_called_once()
        self.assertTrue(callable(self.app.background.call_args.args[0]))

    def test_already_checked_object_is_recorded_and_next_target_runs(self):
        from checkin.storage import today
        counts = iter((1, 1, 0))
        chosen = iter(("老师甲", "老师乙"))
        app = self.app

        class SequentialEngine:
            desktop = Mock()

            def read_remaining(self, themes):
                return {"明德之星": next(counts)}

            def prepare(self, theme):
                return app.history.reserve(theme, next(chosen), {})

            def fill(self, row):
                pass

            def submit(self, row):
                result = "already" if row["object"] == "老师甲" else "success"
                phase = "系统提示此前已打卡，下次跳过" if result == "already" else "系统确认自动提交成功"
                completed = app.history.update(row["id"], status="已完成", phase=phase)
                return result, completed

        self.app.row = None
        self.app.history.update(self.app.history.rows[0]["id"], status="已放弃")
        self.app.events = Mock()
        self.app.engine = Mock(return_value=SequentialEngine())
        self.app.batch = BatchSession(today(), ["明德之星"])
        self.app.batch_step()

        completed = [row["object"] for row in self.app.history.rows if row["status"] == "已完成"]
        self.assertEqual(completed, ["老师甲", "老师乙"])
        self.assertEqual(self.app.batch.remaining["明德之星"], 0)
        self.app.report.assert_any_call("系统提示该对象已打卡，已记入 TXT；继续选择下一个未使用对象。")

    def test_cross_day_stops_batch_before_accessing_apps(self):
        self.app.batch.date='2000-01-01'
        self.app.engine=Mock()
        with self.assertRaisesRegex(ValueError,'日期'):
            self.app.batch_step()
        self.assertFalse(self.app.batch.active)
        self.app.engine.assert_not_called()

    def test_confirming_after_failed_initial_scan_still_records_success(self):
        from checkin.storage import today
        self.app.batch=BatchSession(today(),['明德之星'])
        with patch.object(self.module.messagebox,'askyesno',return_value=True):
            self.app.confirm()
        self.assertEqual(self.app.history.rows[-1]['status'],'已完成')
        self.assertTrue(self.app.batch.active)
        self.app.window.after.assert_called_once()
        callback=self.app.window.after.call_args.args[1]
        callback()
        self.app.background.assert_called_once()
        self.assertTrue(callable(self.app.background.call_args.args[0]))

    def test_zero_quota_menu_returns_without_navigation_or_ai(self):
        from checkin.engine import Engine
        engine=Engine.__new__(Engine)
        engine.config={};engine.desktop=Mock();engine.report=Mock();engine.handoff=Mock();engine.navigation=Mock()
        values=[]
        for i,t in enumerate(THEMES):
            values += [node(t,i*150),node('今日可以打卡0次哦',i*150+40)]
        engine.nodes=Mock(return_value=values)
        self.assertEqual(engine.read_remaining(),{t:0 for t in THEMES})
        engine.navigation.assert_not_called()
        engine.desktop.back.assert_not_called()
        engine.handoff.assert_not_called()

    def test_missing_plus_icon_uses_neighboring_navigation_labels(self):
        from checkin.engine import Engine
        engine=Engine.__new__(Engine)
        engine.config={};engine.desktop=Mock();engine.report=Mock();engine.handoff=Mock()
        home=[dict(node('社区',600),x=200),dict(node('排行',600),x=600),dict(node('我的',600),x=800)]
        form=[node('弘文之星打卡',100)]
        engine.nodes=Mock(side_effect=[home,form])
        engine.navigation('弘文之星')
        target=engine.desktop.click.call_args.args[0]
        self.assertEqual(target['name'],'打卡入口加号')
        self.assertAlmostEqual(target['x']+target['width']/2,500)
        engine.handoff.assert_not_called()

    def test_quota_navigation_stops_at_menu_without_selecting_a_star(self):
        from checkin.engine import Engine
        engine=Engine.__new__(Engine)
        engine.config={};engine.desktop=Mock();engine.report=Mock();engine.handoff=Mock()
        menu=[]
        for i,t in enumerate(THEMES):
            menu += [node(t,i*150),node('今日可以打卡2次哦',i*150+40)]
        engine.nodes=Mock(return_value=menu)
        self.assertEqual(engine.navigation(THEMES[0],stop_at_menu=True),menu)
        engine.desktop.click.assert_not_called()

    def test_overlapping_images_and_history_labels_still_use_navbar_plus(self):
        from checkin.engine import Engine
        engine=Engine.__new__(Engine)
        engine.config={};engine.desktop=Mock();engine.report=Mock();engine.handoff=Mock()
        home=[dict(node('社区',600),x=200),dict(node('排行',600),x=600),dict(node('我的',600),x=800),node('明德之星',300)]
        home += [dict(node('',y),kind='Image',x=475,width=50) for y in (555,580)]
        engine.nodes=Mock(side_effect=[home,[node('明德之星打卡',100)]])
        engine.navigation('明德之星')
        self.assertEqual(engine.desktop.click.call_args.args[0]['name'],'打卡入口加号')
        engine.handoff.assert_not_called()

