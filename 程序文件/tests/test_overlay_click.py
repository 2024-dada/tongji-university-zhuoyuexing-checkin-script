import unittest
from unittest.mock import Mock
from checkin.windows import Desktop


def desktop_for_pages(pages):
    desktop = Desktop.__new__(Desktop)
    desktop.focus = Mock()
    desktop.check = Mock()
    desktop.pause = Mock()
    desktop.hwnd = 1
    desktop.user = Mock()
    desktop.ocr_exclusions = lambda: ()
    desktop.overlay_visibility = lambda value: None
    desktop.user.GetAncestor.side_effect = lambda target, kind: target
    desktop.user.WindowFromPoint.side_effect = pages
    return desktop


class OverlayClickTests(unittest.TestCase):
    def test_own_monitor_covering_button_is_hidden_only_during_click(self):
        desktop=Desktop.__new__(Desktop)
        desktop.focus=Mock();desktop.check=Mock();desktop.pause=Mock()
        desktop.hwnd=1;desktop.user=Mock()
        hidden=[]
        desktop.overlay_visibility=lambda value:hidden.append(value)
        desktop.ocr_exclusions=lambda:((100,100,300,300),)
        desktop.user.GetAncestor.side_effect=lambda target,kind:target
        desktop.user.WindowFromPoint.side_effect=lambda point:1 if hidden and hidden[-1] else 2
        desktop.click(dict(name='去打卡',x=150,y=150,width=60,height=30))
        self.assertEqual(hidden,[True,False])
        self.assertEqual(desktop.user.mouse_event.call_count,2)

    def test_a_transient_covering_window_is_waited_out_before_clicking(self):
        desktop = desktop_for_pages([2, 2, 1])

        desktop.click(dict(name='去打卡', x=150, y=150, width=60, height=30))

        # Two 0.5s waits for the covering window, then the click's own pause.
        self.assertEqual([call.args[0] for call in desktop.pause.call_args_list], [.5, .5, .35])
        self.assertEqual(desktop.user.mouse_event.call_count, 2)
        desktop.user.SetCursorPos.assert_called_once_with(180, 165)

    def test_a_persistent_covering_window_names_it_and_never_clicks(self):
        desktop = desktop_for_pages([2, 2, 2, 2])
        desktop.window_name = Mock(return_value='企业微信加载中')
        desktop.window_class = Mock(return_value='BrowserWindow')
        desktop.process = Mock(return_value=r'C:\Program Files\WXWork\WXWork.exe')

        with self.assertRaises(RuntimeError) as caught:
            desktop.click(dict(name='去打卡', x=150, y=150, width=60, height=30))

        message = str(caught.exception)
        self.assertIn('目标被其他窗口遮挡', message)
        self.assertIn('企业微信加载中', message)
        self.assertIn('WXWork.exe', message)
        self.assertIn('BrowserWindow', message)
        desktop.user.mouse_event.assert_not_called()
        desktop.user.SetCursorPos.assert_not_called()


if __name__ == '__main__':
    unittest.main()
