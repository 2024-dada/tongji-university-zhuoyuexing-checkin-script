"""Exercise module entry through the batch boundary and a replayed desktop."""
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from checkin.shizhi import ShizhiBatch, DailyLimit
from checkin.windows import browser_window
from checkin.images import ImageFinder, ImageSearchUnavailable


def control(name, y, kind='Text', value=''):
    return dict(name=name, value=value, kind=kind, x=30, y=y,
                width=500, height=30, enabled=True)


class ModuleDesktop:
    browser_hwnd = 1
    hwnd = 1

    def __init__(self, exhausted=False):
        self.opened = False
        self.clicked = []
        self.exhausted = exhausted

    def check(self): pass
    def pause(self, seconds): pass
    def connect_browser(self): pass

    def snapshot(self):
        if self.opened:
            return [control('', 0, 'Edit', 'https://star.tongji.edu.cn/app/pages-daka/shizhi'),
                    control('今天打卡的次数已用完，明天再来吧！', 200)]
        return [control('', 0, 'Edit', 'https://star.tongji.edu.cn/app/pages/index/daka'),
                control('弘文之星 读百书以强信念', 100),
                control('今天打卡的次数已用完，明天再来吧！', 140),
                control('矢志之星 访百地以寻初心', 250),
                control('今天打卡的次数已用完，明天再来吧！' if self.exhausted else '今日可以打卡1次哦', 290),
                control('力行之星 践百行以促奋进', 400),
                control('今日可以打卡2次哦', 440)]

    def click(self, target, **kwargs):
        self.clicked.append(target['name'])
        self.opened = True


class ModuleEntryTests(unittest.TestCase):
    def test_selected_form_with_previous_text_does_not_require_placeholder(self):
        batch=ShizhiBatch.__new__(ShizhiBatch)
        nodes=[control('',0,'Edit','https://star.tongji.edu.cn/app/pages-daka/shizhi'),
               control('新地标',150),control('去选择',150),
               control('',240,'Edit','上一条尚未清空的感悟'),
               control('上传图片',500),control('确认发布',900)]
        batch.nodes=lambda:nodes;batch.desktop=Mock();batch.report=Mock()
        with patch('checkin.shizhi.time.monotonic',side_effect=[0,1,100]):
            self.assertEqual(batch.wait_form('新地标'),nodes)

    def test_confirmed_publish_my_page_opens_footer_module_entry(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        batch.desktop = Mock()
        batch.report = Mock()
        home = [control('',0,'Edit','https://star.tongji.edu.cn/app/pages/index/my')]
        home.append(control('我的',100,'Document'))
        for label,x in [('社区',400),('排行',1000),('我的',1400)]:
            item=control(label,900);item.update(x=x,width=60);home.append(item)
        form=[control('',0,'Edit','https://star.tongji.edu.cn/app/pages-daka/shizhi')]
        batch.nodes=Mock(return_value=form)
        self.assertEqual(batch.enter_module(home),form)
        batch.desktop.click.assert_called_once()
        self.assertEqual(batch.desktop.click.call_args.args[0]['name'],'打卡入口加号')

    def test_success_return_to_module_reenters_before_next_selection(self):
        from checkin.shizhi import THEME
        batch = ShizhiBatch.__new__(ShizhiBatch)
        form = [control('',0,'Edit','https://star.tongji.edu.cn/app/pages-daka/shizhi')]
        module = [control('',0,'Edit','https://star.tongji.edu.cn/app/pages/index/daka')]
        batch.enter_module = Mock(return_value=form)
        batch.nodes = Mock(side_effect=[form,[control('去打卡',300)],module,[control('去打卡',300)]])
        batch.engine = Mock()
        batch.history = Mock(rows=[])
        batch.desktop = Mock()
        batch.select_nearby_form = Mock(return_value=('新地标',form))
        batch.process_detail = Mock(side_effect=[None,DailyLimit('已用完')])
        batch.visible = lambda node:True
        with self.assertRaises(DailyLimit):batch.run_module(form)
        batch.enter_module.assert_any_call(module)
        self.assertEqual(batch.process_detail.call_count,2)

    def test_blank_form_after_selection_retries_same_landmark_once(self):
        from checkin.engine import NeedUser
        batch = ShizhiBatch.__new__(ShizhiBatch)
        blank = [control('填写地标名称',150),control('去选择',150)]
        batch.choose_nearby = Mock(return_value='新地标')
        batch.wait_form = Mock(side_effect=[NeedUser('等待超时'),['selected']])
        batch.nodes = Mock(return_value=blank)
        batch.engine = Mock()
        batch.report = Mock()
        self.assertEqual(batch.select_nearby_form({'旧地标'}),('新地标',['selected']))
        self.assertEqual(batch.choose_nearby.call_args_list[-1].kwargs, {'expected':'新地标'})
        batch.engine.open_selector.assert_called_once_with(blank,'矢志之星')

    def test_nearby_ocr_icon_is_not_part_of_landmark_name(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        title = control('；同济创新创业控股有限公司',300)
        title['id'] = 'ocr'
        action = control('去打卡',320)
        action.update(x=900,width=100)
        batch.nodes = lambda:[title,action]
        batch.engine = Mock()
        batch.engine.selector_visible_nodes.side_effect = lambda nodes:nodes
        batch.engine.unique_click_targets.side_effect = lambda nodes:nodes
        from checkin.engine import Engine
        batch.engine.row_label_for_action.side_effect = Engine.row_label_for_action
        batch.desktop = Mock()
        batch.report = lambda message:None
        self.assertEqual(batch.choose_nearby(set()), '同济创新创业控股有限公司')

    def test_form_page_does_not_supply_browser_chrome_as_introduction(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        nodes = [control('',0,'Edit','https://star.tongji.edu.cn/app/pages-daka/shizhi'),
                 control('高级语言程序设计A12026-10-14第3-4节 - 内存使用率 - 78.4 MB',10),
                 control('注意：字数不少于 80 个字，您已经输入 0 个字。',450)]
        self.assertEqual(batch.page_description(nodes,'地标名称'), '')

    def test_selected_landmark_can_be_readonly_text_beside_selector(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        text = '本条地标学习感悟。' * 14
        title = control('同济大学建筑设计研究院(集团)有限公司',150)
        selector = control('去选择',150)
        selector.update(x=900,width=100)
        nodes = [title,selector,control('',240,'Edit',text),control('确认发布',800)]
        batch.verify_form_fields('同济大学建筑设计研究院（集团）有限公司', {'text':text},nodes)

    def test_selector_row_cannot_be_mistaken_for_selected_landmark(self):
        from checkin.engine import NeedUser
        batch = ShizhiBatch.__new__(ShizhiBatch)
        text = '本条地标学习感悟。' * 14
        nodes = [control('旧地标',150),control('去选择',150),control('输入地标关键词',200),
                 control('新地标',300),control('去打卡',300),control('',500,'Edit',text)]
        with self.assertRaises(NeedUser):
            batch.verify_form_fields('新地标',{'text':text},nodes)

    def test_search_process_failure_is_retryable_instead_of_rejected_photo(self):
        with tempfile.TemporaryDirectory() as root:
            api = Mock()
            api.json.return_value = {'queries':['同济大学 建筑']}
            finder = ImageFinder(api, root, threading.Event(), lambda message:None)
            with patch('checkin.edge_search.render_page', side_effect=OSError('退出码 0，无页面')):
                with self.assertRaises(ImageSearchUnavailable):
                    finder.choose('矢志之星','同济大学建筑设计研究院','diagnostic')

    def test_selected_landmark_accepts_fullwidth_parentheses_from_accessibility(self):
        batch = ShizhiBatch.__new__(ShizhiBatch)
        text = '这是一段本条地标的感悟。' * 12
        title = control('', 150, 'Edit', '同济大学建筑设计研究院(集团)有限公司')
        body = control('', 240, 'Edit', text)
        batch.verify_form_fields('同济大学建筑设计研究院（集团）有限公司', {'text':text}, [title, body])

    def test_browser_entry_accepts_module_page_but_rejects_multiple_candidates(self):
        module = dict(hwnd=1, title='选择打卡模块 - Microsoft Edge')
        other = dict(hwnd=2, title='学生学习页面 - Microsoft Edge')
        self.assertEqual(browser_window([module, other])['hwnd'], 1)
        with self.assertRaises(RuntimeError):
            browser_window([module, dict(hwnd=3, title='矢志之星 - Microsoft Edge')])

    def run_batch(self, desktop):
        with tempfile.TemporaryDirectory() as root:
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {}, threading.Event(), lambda message: None)
                with self.assertRaises(DailyLimit):
                    batch.run()
                self.assertEqual(batch.history.rows, [])

    def test_other_theme_exhaustion_does_not_block_shizhi_entry(self):
        desktop = ModuleDesktop()
        self.run_batch(desktop)
        self.assertEqual(desktop.clicked, ['矢志之星 访百地以寻初心'])

    def test_shizhi_exhaustion_stops_before_opening_or_reserving(self):
        desktop = ModuleDesktop(exhausted=True)
        self.run_batch(desktop)
        self.assertEqual(desktop.clicked, [])

    def test_nearby_selection_skips_completed_numeric_id_by_title(self):
        class NearbyDesktop(ModuleDesktop):
            def __init__(self):
                super().__init__()
                self.state = 'form'
                self.selector_reads = 0
                self.selected_reads = 0
            def visible_bounds(self): return (0, 0, 1600, 1000)
            def window_bounds(self): return self.visible_bounds()
            def snapshot(self):
                url = control('', 0, 'Edit', 'https://star.tongji.edu.cn/app/pages-daka/shizhi')
                if self.state == 'quota':
                    self.selected_reads += 1
                    if self.selected_reads <= 3:
                        return [url, control('填写感悟、寄语等',220), control('上传图片',400)]
                    return [url, control('今天打卡的次数已用完，明天再来吧！', 200)]
                if self.state == 'form':
                    return [url, control('去选择', 150), control('去选择',150,'Custom'),
                            control('填写感悟、寄语等', 220), control('上传图片', 400)]
                self.selector_reads += 1
                if self.selector_reads <= 2:
                    return [url, control('输入地标关键词', 200)]
                rows = [url]
                for name, y in [('旧地标', 300), ('新地标', 450)]:
                    rows.append(control(name, y))
                    action = control('去打卡', y)
                    action.update(x=900, width=100)
                    rows.append(action)
                return rows
            def click(self, target, **kwargs):
                self.clicked.append((target['name'], target['y']))
                self.state = 'list' if target['name'] == '去选择' else 'quota'
        desktop = NearbyDesktop()
        with tempfile.TemporaryDirectory() as root:
            with patch('checkin.shizhi.Desktop', return_value=desktop), patch('checkin.engine.Desktop', return_value=desktop):
                batch = ShizhiBatch(root, root, {'minimum_chars':80}, threading.Event(), lambda message: None)
                old = batch.history.reserve('矢志之星', '2338', {'title':'旧地标'})
                batch.history.update(old['id'], status='已完成')
                with self.assertRaises(DailyLimit):
                    batch.run()
                self.assertEqual([r['object'] for r in batch.history.rows], ['2338'])
        self.assertEqual(desktop.clicked, [('去选择',150), ('去打卡',450)])
