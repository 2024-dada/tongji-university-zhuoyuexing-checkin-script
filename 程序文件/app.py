import argparse
import ctypes
from ctypes import wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import tempfile
import time
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from checkin.credentials import load_key, save_key
from checkin.deepseek import DeepSeek, NoBalance
from checkin.engine import DailyLimit, Engine, ListExhausted, MaterialUnavailable, THEMES
from checkin.batch import ALL_THEMES, PAGE_THEMES, BatchSession
from checkin.storage import History, ThemeHistory, atomic_write, today
from checkin.resume import ResumeMemory
from checkin.uninstall import ensure_install_marker, get_install_root, validate_install_root
from checkin.cleanup import clear_photo_directory, validate_photo_directory

ROOT = Path(__file__).resolve().parent
INSTALL_ROOT = get_install_root(ROOT)


class App:
    def __init__(self, window, theme):
        self.window, self.row = window, None
        self.batch = None
        self.clearing = False
        self.uninstalling = False
        self.image_unloading = False
        self.monitor_bounds = None
        self.monitor_suspended = False
        self.monitor_suspensions = 0
        self.operation_window_state = None
        self.started_at = None
        self.task_failed = False
        ensure_install_marker(INSTALL_ROOT, ROOT)
        (ROOT / "照片").mkdir(exist_ok=True)
        self.config = json.loads((ROOT / 'config.json').read_text('utf-8-sig'))
        self.history = ThemeHistory(ROOT)
        self.memory = ResumeMemory(ROOT)
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.continuation, self.busy, self.awaiting = threading.Event(), False, False
        window.title('卓越星打卡助手 · 自动提交')
        screen_width, screen_height = window.winfo_screenwidth(), window.winfo_screenheight()
        # Tk geometry uses device pixels; fonts follow Windows DPI. Scale the
        # window too so action buttons remain visible on 150/200% displays.
        scale = max(1.0, window.winfo_fpixels('1i') / 96)
        width, height = min(int(950*scale), screen_width - int(80*scale)), min(int(850*scale), screen_height - int(100*scale))
        left, top_position = max(0, (screen_width - width) // 2), max(0, (screen_height - height) // 2)
        window.geometry(f'{width}x{height}+{left}+{top_position}')
        def show_on_top():
            window.deiconify()
            window.lift()
            window.attributes('-topmost', True)
            window.focus_force()
            window.after(800, lambda: window.attributes('-topmost', False))
        window.after(100, show_on_top)
        window.minsize(min(int(650*scale), width), min(int(560*scale), height))
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Escape>', lambda _: self.stop())
        top = ttk.Frame(window, padding=12)
        top.pack(fill='x')
        self.theme = tk.StringVar(value=theme or ALL_THEMES)
        choices = ttk.Frame(window, padding=(12, 0))
        choices.pack(fill='x')
        self.theme_checks = {}
        self.theme_buttons = []
        for name in PAGE_THEMES:
            checked = tk.BooleanVar(value=theme is None or theme == name)
            self.theme_checks[name] = checked
            button = ttk.Checkbutton(choices, text=name, variable=checked, command=self.sync_choices)
            button.pack(side='left', padx=(0, 5))
            self.theme_buttons.append(button)
        all_button = ttk.Button(choices, text='全选', command=self.select_all)
        all_button.pack(side='left')
        self.theme_buttons.append(all_button)
        self.start_button = ttk.Button(top, text='继续运行', command=self.start)
        self.start_button.pack(side='left', padx=8)
        self.stop_button = ttk.Button(top, text='停止（Esc）', command=self.stop)
        self.stop_button.pack(side='left')
        self.settings_button = ttk.Button(top, text='更多设置', command=self.settings)
        self.settings_button.pack(side='right')
        setup = ttk.Frame(window, padding=(12, 0, 12, 5))
        setup.pack(fill='x')
        self.use_edge = tk.BooleanVar(value=False)
        browser_button = ttk.Checkbutton(setup, text='在 Edge 打卡', variable=self.use_edge)
        browser_button.pack(side='left', padx=(0, 8))
        self.theme_buttons.append(browser_button)
        self.key_button = ttk.Button(setup, text='填写 / 修改 API key', command=self.api_key_dialog)
        self.key_button.pack(side='left')
        self.key_status = tk.StringVar()
        self.refresh_key_status()
        ttk.Label(setup, textvariable=self.key_status).pack(side='left', padx=10)
        ttk.Button(setup, text='查看打卡记录', command=self.open_history).pack(side='right')
        ttk.Button(setup, text='使用说明', command=self.show_help).pack(side='right', padx=8)
        self.monitor_controls = ttk.Frame(window)
        self.monitor_controls.pack(fill='x')
        self.status = tk.StringVar(value='五种星可单选或全部运行；每种星独立 TXT，列表查重后跳过已用对象。矢志使用已登录的 Edge。')
        ttk.Label(window, textvariable=self.status, wraplength=700, padding=12).pack(fill='x')
        self.batch_status = tk.StringVar(value='本次任务尚未开始')
        ttk.Label(window, textvariable=self.batch_status, wraplength=850, padding=(12, 0)).pack(fill='x')
        self.memory_status = tk.StringVar()
        ttk.Label(window, textvariable=self.memory_status, wraplength=850, padding=(12, 0),
                  foreground='#555555').pack(fill='x')
        self.refresh_memory()
        self.object_label = tk.StringVar(value='尚未选择打卡对象')
        ttk.Label(window, textvariable=self.object_label, padding=(12, 3), font=('Microsoft YaHei', 11, 'bold')).pack(fill='x')
        self.text = tk.Text(window, wrap='word', height=12, font=('Microsoft YaHei', 11), undo=True)
        self.text.pack(fill='both', expand=True, padx=12)
        self.image_label = tk.StringVar(value='配图尚未准备')
        ttk.Label(window, textvariable=self.image_label, wraplength=700, padding=12).pack(fill='x')
        actions = ttk.Frame(window, padding=12)
        actions.pack(fill='x')
        self.fill_button = ttk.Button(actions, text='填写已有材料', command=self.fill_saved, state='disabled')
        self.continue_button = ttk.Button(actions, text='继续当前步骤', command=self.resume, state='disabled')
        self.continue_button.pack(side='left', padx=6)
        self.confirm_button = ttk.Button(actions, text='确认已提交', command=self.confirm, state='disabled')
        self.confirm_button.pack(side='left', padx=6)
        history_tools = ttk.Frame(window, padding=(12, 0))
        history_tools.pack(fill='x')
        ttk.Button(history_tools, text='中止当前待提交', command=self.abandon).pack(side='left', padx=6)
        ttk.Button(history_tools, text='审核打回 / 移除完成记录', command=self.review_rejected).pack(side='left', padx=6)
        self.uninstall_button = tk.Button(
            history_tools, text='卸载卓越星助手', command=self.uninstall,
            bg='#c62828', fg='white', activebackground='#a61e1e',
            activeforeground='white', relief='flat', padx=10, pady=3,
        )
        self.uninstall_button.pack(side='right', padx=6)
        self.image_uninstall_button = tk.Button(
            history_tools, text='卸载全部图片', command=self.unload_all_images,
            bg='#ed6c02', fg='white', activebackground='#c45600',
            activeforeground='white', relief='flat', padx=10, pady=3,
        )
        self.image_uninstall_button.pack(side='right', padx=6)
        ttk.Button(history_tools, text='清除运行日志', command=self.clear_runtime_log).pack(side='right', padx=6)
        self.clear_button = ttk.Button(history_tools, text='清空当前页面', command=self.clear_page)
        self.clear_button.pack(side='right', padx=6)
        self.log = tk.Text(window, height=7, wrap='word', state='disabled', font=('Microsoft YaHei', 9))
        self.log.pack(fill='x', padx=12, pady=10)
        if self.memory.warning:
            self.report(self.memory.warning)
        self.create_monitor(scale)
        self.refresh_pending()
        window.after(100, self.poll)

    def create_monitor(self, scale):
        self.monitor_visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.monitor_controls, text='显示置顶进度框（按住标题拖动）',
                        variable=self.monitor_visible, command=self.toggle_monitor).pack(
                            anchor='w', padx=12, pady=(0, 6))
        self.run_state = tk.StringVar(value='等待开始')
        self.elapsed = tk.StringVar(value='本条运行时间：00:00')
        self.monitor = tk.Toplevel(self.window)
        self.monitor.withdraw()
        self.monitor.overrideredirect(True)
        self.monitor.attributes('-topmost', True)
        self.monitor.attributes('-alpha', .92)
        frame = self.monitor_frame = tk.Frame(self.monitor, bg='#182434', padx=14, pady=12)
        frame.pack(fill='both', expand=True)
        for variable, color in ((self.run_state, '#75d9ac'), (self.object_label, '#eeeeee'),
                                (self.status, '#eeeeee'), (self.batch_status, '#aab9cc'),
                                (self.elapsed, '#aab9cc')):
            tk.Label(frame, textvariable=variable, bg='#182434', fg=color,
                     font=('Microsoft YaHei', 9), wraplength=int(360*scale),
                     justify='left').pack(anchor='w', fill='x', pady=(5, 0))
        # A separate interactive title lets the progress body stay click-through.
        self.monitor_title = tk.Toplevel(self.window)
        self.monitor_title.withdraw()
        self.monitor_title.overrideredirect(True)
        self.monitor_title.attributes('-topmost', True)
        self.monitor_title.attributes('-alpha', .92)
        self.monitor_handle = tk.Label(
            self.monitor_title, text='卓越星助手 · 任务进度  ↔ 拖动',
            bg='#182434', fg='white', font=('Microsoft YaHei', 11, 'bold'),
            padx=14, pady=8, anchor='w', cursor='fleur')
        self.monitor_handle.pack(fill='both', expand=True)
        self.monitor_handle.bind('<ButtonPress-1>', self.begin_monitor_drag)
        self.monitor_handle.bind('<B1-Motion>', self.drag_monitor)
        self.monitor_handle.bind('<ButtonRelease-1>', self.end_monitor_drag)
        self.monitor_drag = None
        saved = self.config.get('monitor_position')
        self.monitor_position = tuple(saved) if (isinstance(saved, list) and len(saved) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in saved)) else None
        self.monitor.update_idletasks()
        self.monitor_title.update_idletasks()
        self.monitor_width = min(int(395*scale), self.window.winfo_screenwidth()-30)
        self.monitor_bottom_margin = int(85*scale)
        self.monitor_geometry = None
        self.fit_monitor()
        for surface in (self.monitor, self.monitor_title):
            for event in ('<Configure>', '<Map>', '<Unmap>'):
                surface.bind(event, self.update_monitor_bounds)
        self.toggle_monitor()

    def fit_monitor(self):
        if not self.monitor_visible.get() or self.monitor_suspended:
            return
        height = self.monitor_frame.winfo_reqheight()
        header = self.monitor_handle.winfo_reqheight()
        width = self.monitor_width
        screen_w, screen_h = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        x, y = self.monitor_position or (screen_w-width-20,
                                       screen_h-height-header-self.monitor_bottom_margin)
        x = max(0, min(x, max(0, screen_w-width)))
        y = max(0, min(y, max(0, screen_h-height-header)))
        geometry = (width, height, header, x, y)
        if geometry != self.monitor_geometry:
            self.monitor_title.geometry(f'{width}x{header}+{x}+{y}')
            self.monitor.geometry(f'{width}x{height}+{x}+{y+header}')
            self.monitor_geometry = geometry

    def begin_monitor_drag(self, event):
        self.monitor_drag = (event.x_root, event.y_root,
                             self.monitor_title.winfo_x(), self.monitor_title.winfo_y())

    def drag_monitor(self, event):
        if self.monitor_drag is None:
            return
        start_x, start_y, x, y = self.monitor_drag
        self.monitor_position = (x+event.x_root-start_x, y+event.y_root-start_y)
        self.fit_monitor()

    def end_monitor_drag(self, event):
        if self.monitor_drag is None:
            return
        self.drag_monitor(event)
        self.monitor_drag = None
        self.monitor_position = self.monitor_geometry[-2:]
        self.config['monitor_position'] = list(self.monitor_position)
        try:
            atomic_write(ROOT / 'config.json', json.dumps(self.config, ensure_ascii=False, indent=2))
        except OSError:
            self.report('进度框位置已移动，本次未能保存位置，请检查文件夹写入权限。')

    def update_monitor_bounds(self, event=None):
        if self.monitor_visible.get() and not self.monitor_suspended and self.monitor.winfo_viewable() and self.monitor_title.winfo_viewable():
            x, y = self.monitor_title.winfo_rootx(), self.monitor_title.winfo_rooty()
            right = max(x+self.monitor_title.winfo_width(),
                        self.monitor.winfo_rootx()+self.monitor.winfo_width())
            bottom = self.monitor.winfo_rooty()+self.monitor.winfo_height()
            self.monitor_bounds = (x, y, right, bottom)
        else:
            self.monitor_bounds = None

    def toggle_monitor(self):
        if not self.monitor_visible.get() or self.monitor_suspended:
            self.monitor.withdraw()
            self.monitor_title.withdraw()
            self.monitor_drag = None
            self.monitor_bounds = None
            return
        self.fit_monitor()
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongW.restype = ctypes.c_long
        user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user.SetWindowLongW.restype = ctypes.c_long
        for surface, click_through in ((self.monitor, True), (self.monitor_title, False)):
            surface.deiconify()
            surface.update_idletasks()
            handle = user.GetAncestor(surface.winfo_id(), 2)
            style = user.GetWindowLongW(handle, -20) | 0x80000 | 0x08000000
            style = (style | 0x20) if click_through else (style & ~0x20)
            user.SetWindowLongW(handle, -20, style)
        self.update_monitor_bounds()

    def suspend_monitor(self, hidden):
        finished = threading.Event()
        self.events.put(('monitor_suspend', (hidden, finished)))
        while not finished.wait(.1):
            if self.cancel.is_set():
                if hidden:
                    raise RuntimeError('操作已停止；待提交内容保留。')
                return

    def with_monitor(self, engine):
        # Worker reads a plain tuple; it never calls Tk from the worker thread.
        engine.desktop.overlay_visibility = self.suspend_monitor
        engine.desktop.ocr_exclusions = lambda: ((self.monitor_bounds,) if self.monitor_bounds else ())
        return engine

    def reset_current_view(self):
        """Clear the displayed task only; completed TXT records stay intact."""
        self.row = None
        self.awaiting = False
        self.continuation.clear()
        self.text.delete('1.0', 'end')
        self.text.edit_reset()
        self.log.config(state='normal')
        self.log.delete('1.0', 'end')
        self.log.config(state='disabled')
        self.object_label.set('尚未选择打卡对象')
        self.image_label.set('配图尚未准备')
        self.status.set('上一条已提交；正在准备下一条任务。')
        self.run_state.set('切换下一条任务')
        self.elapsed.set('本条运行时间：00:00')
        self.confirm_button.config(state='disabled')
        self.fill_button.config(state='disabled')
        self.continue_button.config(state='disabled')

    def report(self, text):
        self.events.put(('log', text))

    def show_row(self, row):
        self.row = row
        self.object_label.set(row['theme'] + ' · ' + row['object'] + ' · ' + row['status'])
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', row.get('text', ''))
        self.image_label.set('明德之星无需照片，已跳过配图。' if row['theme'] == '明德之星' else
                             '配图：' + (row.get('image') or '尚未准备，可手动选择') + ('\n' + row['image_warning'] if row.get('image_warning') else ''))
        self.confirm_button.config(state='normal' if row['status'] in ('待提交', '结果待核实') and not self.busy else 'disabled')
        self.fill_button.config(state='normal' if row['status'] == '待提交' and row.get('text') and (row['theme'] == '明德之星' or row.get('image')) and not self.busy else 'disabled')

    def theme_changed(self, _=None):
        if self.batch:
            self.batch.active = False
        self.refresh_memory()
        self.refresh_pending()

    def refresh_memory(self):
        """Show where each selected star will continue from."""
        if not hasattr(self, 'memory_status'):
            return
        memory = getattr(self, 'memory', None)
        if memory is None:
            self.memory_status.set('续跑记忆：尚未读取')
            return
        text = memory.summary(self.selected_themes())
        hint = '（可在“更多设置”里清除）' if memory.has_entries() else ''
        self.memory_status.set('续跑记忆：' + text + hint)

    def refresh_pending(self):
        if self.batch and self.batch.active and self.row and self.row['status'] == '待提交':
            pending = self.history.pending(self.row['theme'])
            if pending and pending['id'] == self.row['id']:
                self.show_row(pending)
                return
        selected = self.theme.get()
        pending = next((row for t in self.selected_themes() if (row := self.history.pending(t))), None)
        if pending:
            self.show_row(pending)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.status.set(value)
                    self.log.config(state='normal')
                    self.log.insert('end', value + '\n')
                    self.log.see('end')
                    self.log.config(state='disabled')
                elif kind == 'monitor_suspend':
                    hidden, finished = value
                    try:
                        if hidden and self.monitor_suspensions == 0:
                            self.operation_window_state = self.window.state()
                            self.window.withdraw()
                        self.monitor_suspensions = max(0, self.monitor_suspensions + (1 if hidden else -1))
                        self.monitor_suspended = self.monitor_suspensions > 0
                        self.toggle_monitor()
                        if not self.monitor_suspended and self.operation_window_state is not None:
                            previous_state = self.operation_window_state
                            self.operation_window_state = None
                            if previous_state != 'withdrawn':
                                self.window.state(previous_state)
                    finally:
                        finished.set()
                elif kind == 'task_started':
                    self.started_at = time.monotonic()
                    self.elapsed.set('本条运行时间：00:00')
                    self.run_state.set('正在运行')
                elif kind in ('completed', 'skipped'):
                    self.reset_current_view()
                    self.status.set(value)
                elif kind == 'row':
                    self.show_row(value)
                elif kind == 'batch':
                    self.batch_status.set(value)
                elif kind == 'handoff':
                    if not self.uninstalling:
                        self.awaiting = True
                        self.run_state.set('等待手动步骤')
                        self.status.set(value)
                        self.continue_button.config(state='normal')
                        self.window.deiconify()
                        self.window.lift()
                        self.continue_button.focus_set()
                elif kind == 'error':
                    self.task_failed = True
                    self.run_state.set('已暂停')
                    self.status.set(value)
                    self.window.deiconify()
                    self.window.lift()
                elif kind == 'images_cleared':
                    removed, errors, rows_changed = value
                    if self.row:
                        current = next((row for row in self.history.rows if row['id'] == self.row['id']), None)
                        if current:
                            self.show_row(current)
                    if errors:
                        details = '\n'.join(errors[:8])
                        message = (f'已清理 {removed} 项图片文件/目录，更新 {rows_changed} 条记录。'
                                   f'\n有 {len(errors)} 项无法删除，请关闭占用它们的程序后重试。'
                                   f'\n\n' + details)
                        self.status.set('图片清理未完全完成，请查看提示并重试。')
                        messagebox.showwarning('图片清理未完全完成', message, parent=self.window)
                    else:
                        message = f'已清理 {removed} 项图片文件/目录，并更新 {rows_changed} 条记录。'
                        self.status.set(message + '文字记录、配置和 API key 已保留。')
                        self.log.config(state='normal')
                        self.log.insert('end', message + '\n')
                        self.log.see('end')
                        self.log.config(state='disabled')
                elif kind == 'balance':
                    messagebox.showinfo('DeepSeek 余额', value, parent=self.window)
                elif kind == 'idle':
                    self.busy = False
                    self.awaiting = False
                    self.start_button.config(state='normal')
                    self.set_choices_state('normal')
                    self.continue_button.config(state='disabled')
                    if self.uninstalling:
                        self.window.after(0, self.finish_uninstall)
                    elif self.clearing:
                        self.finish_clear_page()
                    else:
                        self.image_unloading = False
                        self.image_uninstall_button.config(state='normal')
                        self.clear_button.config(state='normal')
                        self.refresh_memory()
                        self.refresh_pending()
                        if self.cancel.is_set():
                            self.run_state.set('已停止')
                        elif self.task_failed:
                            self.run_state.set('已暂停，请处理提示')
                        elif self.row and self.row['status'] == '待提交':
                            self.run_state.set('已暂停，材料保留')
                        else:
                            self.run_state.set('本次任务已结束')
        except queue.Empty:
            pass
        if self.started_at is not None and self.busy:
            seconds = int(time.monotonic()-self.started_at)
            self.elapsed.set(f'本条运行时间：{seconds//60:02d}:{seconds%60:02d}')
        self.fit_monitor()
        self.window.after(100, self.poll)

    def handoff(self, text):
        self.continuation.clear()
        self.events.put(('handoff', text))
        while not self.continuation.wait(.15):
            if self.cancel.is_set():
                raise RuntimeError('操作已停止，待提交内容保留。')
        if self.cancel.is_set():
            raise RuntimeError('操作已停止，待提交内容保留。')

    def resume(self):
        self.run_state.set('正在运行')
        self.awaiting = False
        self.continue_button.config(state='disabled')
        if self.monitor_suspended:
            self.window.withdraw()
        self.continuation.set()

    def stop(self):
        self.run_state.set('正在停止' if self.busy else '已停止')
        if self.batch:
            self.batch.active = False
        self.cancel.set()
        self.continuation.set()
        self.report('停止请求已发送；正在进行的网络请求结束后停止。不会继续点击。')

    def background(self, task):
        if self.busy or getattr(self, 'clearing', False) or getattr(self, 'uninstalling', False):
            return
        self.cancel.clear()
        self.task_failed = False
        self.started_at = time.monotonic()
        self.elapsed.set('本条运行时间：00:00')
        self.run_state.set('正在运行')
        self.busy = True
        self.start_button.config(state='disabled')
        self.set_choices_state('disabled')
        self.confirm_button.config(state='disabled')
        self.fill_button.config(state='disabled')
        self.image_uninstall_button.config(state='disabled')

        def worker():
            try:
                task()
            except NoBalance as error:
                self.report(str(error))
                self.events.put(('error', str(error)))
            except DailyLimit as error:
                self.report(str(error))
            except Exception as error:
                # API 客户端不会将 key 或 HTTP 请求内容放进异常。
                self.report(str(error))
                self.events.put(('error', str(error)))
            finally:
                self.events.put(('idle', None))

        threading.Thread(target=worker, daemon=True).start()

    def engine(self):
        api = DeepSeek(load_key(ROOT), self.config.get('model', 'deepseek-flash'), self.cancel)
        return self.with_monitor(Engine(ROOT, self.config, self.history, api, self.cancel, self.report, self.handoff, lambda row: self.events.put(('row', row)), browser=getattr(self, 'batch_browser', False), resume=getattr(self, 'memory', None)))

    def preview_counts(self):
        selected = self.theme.get()
        if selected == '矢志之星':
            self.report('矢志会在 Edge 地点流程内读取网站次数；点击继续运行开始。')
            return
        themes = tuple(theme for theme in THEMES if theme in self.selected_themes())
        browser = self.use_edge.get()
        def task():
            engine = self.with_monitor(Engine(ROOT, self.config, self.history, None, self.cancel, self.report, self.handoff, browser=browser, resume=getattr(self, 'memory', None)))
            counts = engine.read_remaining(themes)
            self.events.put(('batch', '页面次数：' + '；'.join(t + str(counts[t]) + '次' for t in themes)))
            self.report('次数已读取。点击开始后逐条填写并自动提交。')
        self.background(task)

    def batch_step(self, scope=None):
        session = self.batch
        if not session or not session.active:
            return
        themes = session.themes if scope is None else tuple(t for t in scope if t in session.themes)
        self.history.reload()
        unresolved = [r for r in self.history.rows if r['theme'] in themes and r['status'] == '结果待核实']
        if unresolved:
            self.events.put(('row', unresolved[0].copy()))
            raise ValueError('存在发布结果待核实的对象：' + unresolved[0]['object'] + '。请先核对小程序；确已成功时可点击确认已提交。不会自动重提。')
        if session.date != today():
            session.active = False
            raise ValueError('日期已变化，请重新开始读取当天次数。')
        engine = self.engine()
        if any(count is None for count in session.remaining.values()):
            session.refresh(engine.read_remaining(session.themes))
        self.events.put(('batch', session.summary()))
        exhausted = set()
        for _ in range(200):
            engine.desktop.check()
            if not session.active:
                return
            if session.date != today():
                session.active = False
                raise ValueError('日期已变化，请重新开始读取当天次数。')
            theme = next((t for t in themes if t not in exhausted and (session.remaining[t] or 0) > 0), None)
            if not theme:
                if exhausted and not any((session.remaining[t] or 0) > 0 for t in session.themes if t not in exhausted):
                    session.active = False
                    self.report('可用列表已处理完；有剩余次数的星没有未使用对象，未把次数清零。')
                    return
                if scope is None or not session.next_theme():
                    session.active = False
                    self.report('本次页面可用次数已处理完。历史记录保留。')
                return
            self.events.put(('task_started', None))
            self.report('准备下一条：' + theme)
            try:
                row = engine.prepare(theme)
            except MaterialUnavailable as error:
                self.report('资料搜不到，已记录跳过，继续下一个；未提交、未扣次数：' + str(error))
                self.events.put(('skipped', '资料搜不到，继续下一项：' + str(error)))
                continue
            except ListExhausted as error:
                exhausted.add(theme)
                self.report(theme + '：' + str(error) + ' 跳过本星，继续其他星；剩余次数保留。')
                self.events.put(('batch', session.summary()))
                continue
            self.events.put(('row', row.copy()))
            engine.fill(row)
            result, completed = engine.submit(row)
            if result == 'success':
                session.confirm(theme)
            elif result == 'already':
                # An "already checked in" response is a completed skip: the
                # object is now in history.used(), so the next loop selects a
                # different list entry without consuming an extra quota.
                self.report('系统提示该对象已打卡，已记入 TXT；继续选择下一个未使用对象。')
            self.events.put(('completed', ('已打卡目标已记录，跳过：' if result == 'already' else '自动提交成功：') + completed['object']))
            self.events.put(('batch', session.summary()))
        session.active = False
        raise ValueError('连续处理达到本轮上限，记录已保留；请核对页面后重新开始。')

    def select_all(self):
        for checked in self.theme_checks.values():
            checked.set(True)
        self.sync_choices()

    def sync_choices(self):
        selected = tuple(name for name, checked in self.theme_checks.items() if checked.get())
        self.theme.set(selected[0] if len(selected) == 1 else ALL_THEMES)
        self.theme_changed()

    def selected_themes(self):
        if hasattr(self, 'theme_checks'):
            return tuple(name for name, checked in self.theme_checks.items() if checked.get())
        selected = self.theme.get()
        return PAGE_THEMES if selected == ALL_THEMES else (selected,)

    def set_choices_state(self, state):
        for button in getattr(self, 'theme_buttons', ()):
            button.config(state=state)

    def run_selected(self, selected):
        if isinstance(selected, str):
            selected = PAGE_THEMES if selected == ALL_THEMES else (selected,)
        if '矢志之星' not in selected:
            self.batch_step()
            return
        split = PAGE_THEMES.index('矢志之星')
        before = tuple(t for t in PAGE_THEMES[:split] if t in selected)
        after = tuple(t for t in PAGE_THEMES[split + 1:] if t in selected)
        if before or after:
            self.batch_step(before)
        if not self.cancel.is_set():
            from checkin.shizhi import ShizhiBatch
            self.report('准备矢志之星：使用 Edge 中已登录的卓越星页面。')
            batch = ShizhiBatch(ROOT, ROOT / '矢志之星数据', self.config,
                                self.cancel, self.report, history=self.history,
                                resume=getattr(self, 'memory', None))
            batch.history = self.history
            batch.engine.history = self.history
            batch.engine.handoff = self.handoff
            self.with_monitor(batch.engine)
            try:
                batch.run(module_only=True)
            except DailyLimit as error:
                self.report(str(error))
            self.history.reload()
            self.events.put(('completed', '矢志之星本轮结束，记录已保存。'))
        if after and not self.cancel.is_set():
            self.batch_step(after)

    def start(self):
        if self.busy:
            return
        try:
            load_key(ROOT)
        except (ValueError, OSError):
            self.api_key_dialog()
            return
        selected = self.selected_themes()
        if not selected:
            messagebox.showinfo('请选择星项', '至少勾选一项再运行。', parent=self.window)
            return
        themes = tuple(theme for theme in THEMES if theme in selected)
        browser = self.use_edge.get() if hasattr(self, 'use_edge') else False
        if browser != getattr(self, 'batch_browser', False):
            self.batch = None
        self.batch_browser = browser
        if not themes:
            self.batch = None
        elif self.batch and self.batch.date == today() and self.batch.themes == themes:
            self.batch.active = True
        else:
            self.batch = BatchSession(today(), themes)
        self.background(lambda: self.run_selected(selected))

    def save_text(self):
        if self.busy:
            messagebox.showinfo('正在操作', '请先停止自动操作，再修改材料。')
            return False
        if not self.row or self.row['status'] != '待提交':
            return False
        value = self.text.get('1.0', 'end-1c').strip()
        minimum = self.row.get('fields', {}).get('minimum_chars', 80)
        if sum(not c.isspace() for c in value) < minimum:
            messagebox.showwarning('字数不足', f'正文需不少于 {minimum} 字。')
            return False
        self.row = self.history.update(self.row['id'], text=value)
        self.report('修改的感想已保存到待提交记录。')
        return True

    def copy_text(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.text.get('1.0', 'end-1c'))

    def choose_image(self):
        if self.row and self.row['theme'] == '明德之星':
            self.report('明德之星无需照片，已跳过配图。')
            return
        if self.busy or not self.row or self.row['status'] != '待提交':
            messagebox.showinfo('暂不可更换', '请先停止操作，并准备一条待提交记录。')
            return
        filename = filedialog.askopenfilename(title='选择一张配图', filetypes=[('图片', '*.png *.jpg *.jpeg *.webp *.bmp')])
        if not filename:
            return
        from PIL import Image
        try:
            image = Image.open(filename).convert('RGB')
            folder = ROOT / '照片' / self.row['id']
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / '上传配图.png'
            image.save(target)
            self.row = self.history.update(self.row['id'], image=str(target), image_warning='', phase='用户手动选择配图')
            self.show_row(self.row)
        except (OSError, ValueError) as error:
            messagebox.showerror('图片无法读取', str(error))

    def next_reviewed_image(self):
        if self.busy or not self.row or self.row['status'] != '待提交':
            messagebox.showinfo('暂不可切换', '请先停止操作，并准备一条待提交记录。')
            return
        from checkin.images import ImageFinder
        try:
            finder = ImageFinder(None, ROOT, self.cancel, self.report)
            path = finder.select_reviewed(self.row['theme'], self.row['object'], self.row['id'])
            self.row = self.history.update(self.row['id'], image=path,
                image_warning=finder.warning(self.row['id'], path), phase='用户切换已审查候选')
            self.show_row(self.row)
            self.report(self.row['image_warning'])
            self.report('已切换本条配图。可点“查看配图”核对，再点“填写已有材料”；页面已有旧图时请先删除旧图。')
        except (OSError, ValueError, TypeError, KeyError) as error:
            messagebox.showinfo('不能继续换图', str(error))

    def show_image(self):
        if self.row and self.row['theme'] != '明德之星' and self.row.get('image') and Path(self.row['image']).is_file():
            os.startfile(self.row['image'])

    def fill_saved(self):
        if not self.row or not self.save_text():
            return
        row = self.row
        def task():
            engine = self.engine()
            engine.navigation(row['theme'])
            engine.fill(row)
            result, completed = engine.submit(row)
            self.events.put(('completed', '本条系统结果已记录：' + completed['object']))
            if not self.batch or not self.batch.active:
                self.batch = BatchSession(today(), (row['theme'],))
            elif result == 'success' and (self.batch.remaining.get(row['theme']) or 0) > 0:
                self.batch.confirm(row['theme'])
            self.batch_step()
        self.background(task)

    def confirm(self):
        if self.busy or not self.row or self.row['status'] not in ('待提交', '结果待核实'):
            return
        if not messagebox.askyesno('确认已提交', '你是否已核对卓越星的实际发布结果，确认此对象已成功打卡？\n只有确实提交成功才选“是”。', parent=self.window):
            return
        try:
            completed = self.history.confirm(self.row['id'])
            from checkin.completion import finalize_completed
            completed = finalize_completed(self.history, self.history.path.parent, completed, self.report)
        except (OSError, ValueError) as error:
            messagebox.showerror('记录未更新', str(error), parent=self.window)
            return

        session = self.batch
        if session and session.active and session.date == today() and (session.remaining.get(completed['theme']) or 0) > 0:
            session.confirm(completed['theme'])
            summary = session.summary()
        else:
            # A restored/stopped task also continues after user confirmation.
            # Unknown quotas must be read from the page rather than invented.
            selected = self.theme.get()
            themes = tuple(theme for theme in THEMES if theme in self.selected_themes())
            session = self.batch = (BatchSession(today(), themes) if themes else None)
            summary = '正在重新读取页面剩余次数……'
        self.reset_current_view()
        self.batch_status.set(summary)
        self.report('TXT 已记录为已完成；上一条显示已清空，正在接续下一条。')
        if completed['theme'] == '矢志之星':
            self.window.after(200, self.start)
        else:
            self.window.after(200, lambda current=session: self.continue_batch(current))

    def continue_batch(self, session):
        # Stop/clear/close or a newly started batch can invalidate this callback.
        if self.batch is not session or not session.active or self.busy or self.clearing or self.uninstalling or self.image_unloading:
            return
        selected = self.selected_themes()
        self.background(lambda: self.run_selected(selected))

    def abandon(self):
        if self.busy or not self.row or self.row['status'] != '待提交':
            return
        if messagebox.askyesno('放弃待提交', '保留材料并将此记录标为已放弃？这个对象将允许重新选择。'):
            row = self.history.update(self.row['id'], status='已放弃', phase='用户放弃')
            memory = getattr(self, 'memory', None)
            if memory is not None:
                memory.forget(row['theme'])
            self.refresh_memory()
            self.show_row(self.row)
            self.report('已放弃并重置该星续跑记忆，下一次从头查找时仍可重新选择这个对象。')

    def clear_runtime_log(self):
        """Clear visible diagnostics without discarding repeat-prevention data."""
        self.log.config(state='normal')
        self.log.delete('1.0', 'end')
        self.log.config(state='disabled')

    def clear_page(self):
        if self.clearing:
            return
        prompt = ('只清空助手当前显示的正文、配图、日志和任务进度。'
                  '\n打卡记录、待提交材料、续跑位置、配置和照片文件全部保留。'
                  '\n当前自动操作会先停止，结束后再清空显示。是否继续？')
        if not messagebox.askyesno('清空当前页面', prompt, parent=self.window):
            return
        self.clearing = True
        if self.batch:
            self.batch.active = False
        self.cancel.set()
        self.continuation.set()
        self.clear_button.config(state='disabled')
        self.start_button.config(state='disabled')
        self.confirm_button.config(state='disabled')
        self.fill_button.config(state='disabled')
        self.continue_button.config(state='disabled')
        if self.busy:
            self.status.set('正在停止当前操作；操作结束后自动清空当前页面，打卡记录保留。')
            return
        self.finish_clear_page()

    def finish_clear_page(self):
        # Called only after the worker's idle event, so late row updates cannot
        # recreate records or refill the page after the reset.
        self.row, self.batch, self.awaiting = None, None, False
        self.run_state.set('等待开始')
        self.elapsed.set('本条运行时间：00:00')
        self.text.delete('1.0', 'end')
        self.text.edit_reset()
        self.log.config(state='normal')
        self.log.delete('1.0', 'end')
        self.log.config(state='disabled')
        self.object_label.set('尚未选择打卡对象')
        self.image_label.set('配图尚未准备')
        self.batch_status.set('本次任务尚未开始')
        self.refresh_memory()
        self.status.set('当前页面已清空；打卡记录、待提交材料和续跑位置保留，继续运行可恢复。')
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self.confirm_button.config(state='disabled')
        self.fill_button.config(state='disabled')
        self.continue_button.config(state='disabled')
        self.start_button.config(state='normal')
        self.set_choices_state('normal')
        self.clear_button.config(state='normal')
        self.image_uninstall_button.config(state='normal')
        self.image_unloading = False
        self.clearing = False

    def unload_all_images(self):
        if self.busy or self.clearing or self.uninstalling or self.image_unloading:
            messagebox.showinfo('正在操作', '请等待当前操作结束，再卸载图片。', parent=self.window)
            return
        prompt = (
            '将永久删除此程序“照片”文件夹中的全部图片、缓存和审查文件，并清空 TXT 记录中的本地图片路径。'
            '\n待提交正文和对象会保留，但需要重新选择或生成配图。文字记录、配置和 API key 会保留。'
            '\n已经上传到卓越星小程序的照片不会因此撤回。'
            '\n\n是否继续？'
        )
        if not messagebox.askyesno('卸载全部图片', prompt, icon=messagebox.WARNING,
                                   default=messagebox.NO, parent=self.window):
            return
        self.image_unloading = True
        self.image_uninstall_button.config(state='disabled')
        self.clear_button.config(state='disabled')

        def task():
            validate_photo_directory(ROOT)
            rows_changed = self.history.clear_image_references()
            removed, errors = clear_photo_directory(ROOT)
            self.events.put(('images_cleared', (removed, errors, rows_changed)))

        self.background(task)


    def uninstall(self):
        if self.uninstalling or self.busy and self.clearing:
            return
        try:
            target = validate_install_root(INSTALL_ROOT, ROOT)
        except (OSError, ValueError) as error:
            messagebox.showerror('无法卸载', str(error), parent=self.window)
            return
        details = (
            '确定卸载卓越星助手？程序退出后，将永久删除整个程序目录及其中的所有内容：\n'
            + str(target) +
            '\n\n包括脚本、运行库、配置、加密 API key、打卡记录、照片、备份、日志，以及此目录中的其他文件。'
            '\n\n此操作不可撤销。程序目录之外的原始 ZIP 不会被删除。'
        )
        if not messagebox.askyesno('卸载卓越星助手', details,
                                   default=messagebox.NO, parent=self.window):
            return
        final_prompt = (
            '这是最后一次确认。删除后无法恢复目录内的数据和程序。\n\n'
            + str(target) + '\n\n要永久删除这个目录吗？'
        )
        if not messagebox.askyesno('再次确认永久卸载', final_prompt,
                                   icon=messagebox.WARNING, default=messagebox.NO,
                                   parent=self.window):
            return

        self.uninstalling = True
        if self.batch:
            self.batch.active = False
        self.cancel.set()
        self.continuation.set()
        for name in ('uninstall_button', 'image_uninstall_button', 'clear_button', 'start_button', 'stop_button',
                     'settings_button', 'confirm_button', 'fill_button', 'continue_button'):
            button = getattr(self, name, None)
            if button:
                button.config(state='disabled')
        self.set_choices_state('disabled')
        self.status.set('正在停止当前操作；结束后删除整个程序目录。')
        if not self.busy:
            self.window.after(0, self.finish_uninstall)

    def finish_uninstall(self):
        if not self.uninstalling or self.busy:
            return
        try:
            target = validate_install_root(INSTALL_ROOT, ROOT)
            helper = ROOT / 'automation' / 'uninstall.ps1'
            if not helper.is_file():
                raise OSError('卸载辅助文件不存在。')
            powershell = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
            if not powershell.is_file():
                raise OSError('找不到 Windows PowerShell，程序和数据尚未删除。')
            token = uuid.uuid4().hex
            ready = Path(tempfile.gettempdir()) / ('zhuoyuex-uninstall-' + token + '.status')
            process = subprocess.Popen(
                [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-File', str(helper), '-InstallRoot', str(target), '-ProcessId', str(os.getpid()),
                 '-ReadyFile', str(ready), '-Nonce', token],
                cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
                               | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)),
            )
            result = ''
            for _ in range(60):
                if ready.is_file():
                    try:
                        result = ready.read_text('utf-8-sig').strip()
                    except OSError:
                        result = ''
                    if result or process.poll() is not None:
                        break
                elif process.poll() is not None:
                    break
                time.sleep(.1)
            if result != 'READY:' + token:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                ready.unlink(missing_ok=True)
                raise OSError('卸载器无法验证并准备程序目录，尚未删除文件。请检查 PowerShell 权限后重试。')
            self.status.set('卸载器已就绪，正在关闭助手并删除程序目录……')
            self.window.destroy()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.uninstalling = False
            self.status.set('卸载未启动，数据和程序仍保留。')
            for name in ('uninstall_button', 'image_uninstall_button', 'clear_button', 'start_button', 'stop_button',
                         'settings_button'):
                button = getattr(self, name, None)
                if button:
                    button.config(state='normal')
            self.set_choices_state('normal')
            self.refresh_pending()
            messagebox.showerror('卸载未启动', str(error), parent=self.window)

    def review_rejected(self):
        if self.busy:
            messagebox.showinfo('正在操作', '请先停止自动操作，再处理审核打回记录。', parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title('审核打回 · 查找并移除已完成记录')
        dialog.transient(self.window)
        scale = max(1.0, dialog.winfo_fpixels('1i') / 96)
        width = min(int(820*scale), dialog.winfo_screenwidth()-int(60*scale))
        height = min(int(480*scale), dialog.winfo_screenheight()-int(80*scale))
        dialog.geometry(f'{width}x{height}')
        ttk.Label(dialog, text='输入被打回对象的关键词，选择一条记录后移除。系统会先保存独立 TXT 备份。',
                  wraplength=width-30, padding=12).pack(fill='x')
        search_bar = ttk.Frame(dialog, padding=(12, 0))
        search_bar.pack(fill='x')
        keyword = tk.StringVar()
        entry = ttk.Entry(search_bar, textvariable=keyword)
        entry.pack(side='left', fill='x', expand=True)
        scope = None if self.theme.get() == ALL_THEMES else self.theme.get()
        info = tk.StringVar(value='查找范围：' + (scope or ALL_THEMES))
        ttk.Label(dialog, textvariable=info, padding=12).pack(fill='x')
        listing = ttk.Frame(dialog, padding=(12, 0))
        listing.pack(fill='both', expand=True)
        tree = ttk.Treeview(listing, columns=('date', 'theme', 'object'), show='headings', selectmode='browse')
        for column, title, size in (('date', '日期', 100), ('theme', '主题', 100), ('object', '对象完整名称', 520)):
            tree.heading(column, text=title)
            tree.column(column, width=int(size*scale), stretch=column == 'object')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)
        matches = {}
        def search():
            if self.busy:
                return
            try:
                rows = self.history.completed_matches(keyword.get(), scope)
            except ValueError as error:
                messagebox.showwarning('请输入关键词', str(error), parent=dialog)
                return
            tree.delete(*tree.get_children())
            matches.clear()
            for row in rows:
                matches[row['id']] = row
                tree.insert('', 'end', iid=row['id'], values=(row['date'], row['theme'], row['object']))
            info.set('找到 ' + str(len(rows)) + ' 条已完成记录；只会移除你选中的一条。')
        ttk.Button(search_bar, text='查找已完成记录', command=search).pack(side='right', padx=(8, 0))
        entry.bind('<Return>', lambda _: search())
        def remove():
            if self.busy:
                messagebox.showinfo('正在操作', '请先停止自动操作，再处理记录。', parent=dialog)
                return
            selection = tree.selection()
            if len(selection) != 1 or selection[0] not in matches:
                messagebox.showwarning('选择记录', '请查找并选中一条确实被审核打回的已完成记录。', parent=dialog)
                return
            row = matches[selection[0]]
            prompt = (row['date'] + ' · ' + row['theme'] + '\n' + row['object'] +
                      '\n\n这条是否确实被审核打回？\n确认后从当前 TXT 移除这一条已完成记录；原记录先保存到“记录备份”。')
            if not messagebox.askyesno('确认审核打回', prompt, parent=dialog):
                return
            try:
                removed, backup = self.history.remove_completed(row['id'])
            except (ValueError, OSError) as error:
                messagebox.showerror('未能移除', str(error), parent=dialog)
                return
            if self.batch:
                self.batch.active = False
                self.batch_status.set('记录已调整；重新开始时读取页面实际剩余次数。')
            if self.row and self.row['id'] == removed['id']:
                self.row = None
                self.object_label.set('已移除被打回的完成记录')
                self.text.delete('1.0', 'end')
                self.image_label.set('配图文件保留在原文件夹。')
                self.confirm_button.config(state='disabled')
                self.fill_button.config(state='disabled')
            self.refresh_pending()
            memory = getattr(self, 'memory', None)
            if memory is not None:
                memory.forget(row['theme'])
            self.refresh_memory()
            self.report('被打回记录已从当前 TXT 移除，备份：' + str(backup) + '。重新开始后按页面次数选择对象。')
            dialog.destroy()
        ttk.Button(dialog, text='确认打回并移除选中记录', command=remove).pack(pady=12)
        entry.focus_set()

    def import_history(self):
        if self.theme.get() == ALL_THEMES:
            messagebox.showinfo('选择主题', '补录记录时，请先从顶部选择具体主题。')
            return
        if self.busy:
            return
        name = simpledialog.askstring('补录旧对象', f'输入 {self.theme.get()} 以前用过的对象完整名称：')
        if not name:
            return
        date = simpledialog.askstring('打卡日期', '日期格式 YYYY-MM-DD：', initialvalue=today())
        if not date:
            return
        try:
            self.history.import_completed(self.row['theme'] if self.theme.get() == ALL_THEMES and self.row else self.theme.get(), name, date)
            self.report('旧对象已补录，不会再次选择。')
        except ValueError as error:
            messagebox.showerror('无法补录', str(error))

    def open_history(self):
        selected = self.theme.get()
        target = self.history.directory if selected == ALL_THEMES else self.history.theme_path(selected)
        os.startfile(target)

    def refresh_key_status(self):
        try:
            load_key(ROOT)
        except (ValueError, OSError):
            self.key_status.set('未配置，请先填写 API key')
        else:
            self.key_status.set('API key 已配置')

    def api_key_dialog(self):
        if self.busy:
            messagebox.showinfo('正在运行', '请先停止任务，再修改 API key。', parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title('填写 DeepSeek API key')
        dialog.transient(self.window)
        dialog.resizable(False, False)
        ttk.Label(dialog, text='粘贴自己的 DeepSeek API key，然后点击确定。').pack(padx=20, pady=(18, 8))
        secret = ttk.Entry(dialog, show='•', width=52)
        secret.pack(padx=20, pady=6)
        ttk.Label(dialog, text='密钥加密保存在本机，下次打开无需重复填写。').pack(padx=20, pady=6)

        def save():
            try:
                save_key(ROOT, secret.get())
            except ValueError as error:
                messagebox.showerror('请检查 API key', str(error), parent=dialog)
                return
            except OSError:
                messagebox.showerror('保存失败', '无法加密或保存密钥，请检查文件夹写权限。', parent=dialog)
                return
            secret.delete(0, 'end')
            self.refresh_key_status()
            self.status.set('API key 已保存。选择打卡主题后，点击“继续运行”。')
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=(8, 18))
        ttk.Button(buttons, text='确定', command=save).pack(side='left', padx=6)
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='left', padx=6)
        dialog.bind('<Return>', lambda _: save())
        secret.focus_set()
        dialog.grab_set()

    def show_help(self):
        messagebox.showinfo('使用说明',
            '1. 首次使用：点击“填写 / 修改 API key”，粘贴密钥并确定。\n\n'
            '2. 企业微信打开“工作台 → 卓越星 → 底部 +”，停在五种星列表；'
            '在 Edge 登录后停在五项打卡列表，勾选“在 Edge 打卡”。矢志也从该列表进入。\n\n'
            '3. 勾选要打卡的星项，或点击“全选”，点击“继续运行”。\n\n'
            '4. 程序会记住上一次停在哪个对象，下次从它之后继续，不再重复检查断点之前的条目。'
            '想从列表开头重新查找，点“更多设置 → 清除续跑记忆（从头开始）”。\n\n'
            '“查看打卡记录”可打开当前星的 TXT；选择全部时打开五份记录所在文件夹。'
            '已记录对象会在列表直接跳过。', parent=self.window)

    def settings(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self.window)
        dialog.title('DeepSeek 设置')
        scale = max(1.0, dialog.winfo_fpixels('1i') / 96)
        width = min(int(570*scale), dialog.winfo_screenwidth()-int(60*scale))
        height = min(int(560*scale), dialog.winfo_screenheight()-int(80*scale))
        dialog.geometry(f'{width}x{height}')
        ttk.Label(dialog, text='输入新的 key；留空保留已有 key。保存时使用 Windows 加密。', wraplength=460).pack(padx=15, pady=15)
        secret = ttk.Entry(dialog, show='•', width=56)
        secret.pack(padx=15)
        model = tk.StringVar(value=self.config.get('model', 'deepseek-flash'))
        ttk.Label(dialog, text='模型（配图识别需支持图片输入，默认 deepseek-flash）').pack(pady=(15, 4))
        ttk.Entry(dialog, textvariable=model, width=45).pack()
        ttk.Label(dialog, text='企业微信 WXWork.exe 路径（可留空并手动打开企业微信）').pack(pady=(12, 3))
        executable = tk.StringVar(value=self.config.get('wecom_exe', ''))
        ttk.Entry(dialog, textvariable=executable, width=62).pack(padx=12)
        def choose_executable():
            chosen = filedialog.askopenfilename(parent=dialog, title='选择企业微信 WXWork.exe', filetypes=[('企业微信', 'WXWork.exe')])
            if chosen:
                executable.set(chosen)
        ttk.Button(dialog, text='选择企业微信程序', command=choose_executable).pack(pady=4)
        proxy_dns = tk.BooleanVar(value=self.config.get('allow_fake_ip_dns', False))
        ttk.Checkbutton(dialog, text='兼容本机代理 Fake-IP DNS（198.18.x.x）',
                        variable=proxy_dns).pack(padx=12, pady=6)

        def save():
            try:
                if secret.get().strip():
                    save_key(ROOT, secret.get())
                self.config['model'] = model.get().strip() or 'deepseek-flash'
                self.config['wecom_exe'] = executable.get().strip()
                self.config['allow_fake_ip_dns'] = proxy_dns.get()
                atomic_write(ROOT / 'config.json', json.dumps(self.config, ensure_ascii=False, indent=2))
                secret.delete(0, 'end')
                self.refresh_key_status()
                dialog.destroy()
            except (OSError, ValueError) as error:
                messagebox.showerror('保存失败', 'key 或配置保存失败：' + str(error), parent=dialog)
        ttk.Button(dialog, text='保存设置', command=save).pack(pady=12)
        def balance():
            def task():
                value = DeepSeek(load_key(ROOT), self.config.get('model', 'deepseek-flash'), self.cancel).balance()
                self.events.put(('balance', value))
            self.background(task)
        ttk.Button(dialog, text='检查余额', command=balance).pack()
        ttk.Label(dialog, text='续跑记忆：' + self.memory.summary(self.selected_themes()),
                  wraplength=width-40, padding=(12, 8)).pack(fill='x')
        def reset_memory():
            if self.busy:
                messagebox.showinfo('正在操作', '请先停止自动操作，再清除续跑记忆。', parent=dialog)
                return
            message = ('清除后续跑记忆，各星下一次从列表开头重新查找。\n'
                       '只影响“从哪里继续扫描”，不会删除任何打卡记录。是否继续？')
            if not messagebox.askyesno('清除续跑记忆', message, parent=dialog):
                return
            removed = self.memory.forget()
            self.refresh_memory()
            self.report('已清除 ' + str(removed) + ' 条续跑记忆，下一次从头查找。')
            dialog.destroy()
        ttk.Button(dialog, text='清除续跑记忆（从头开始）', command=reset_memory).pack(pady=(4, 12))
        # Keep every control reachable after adding the resume-memory block.
        dialog.update_idletasks()
        dialog.geometry(f'{width}x{max(height, dialog.winfo_reqheight())}')

    def close(self):
        if getattr(self, 'uninstalling', False):
            return
        self.cancel.set()
        self.continuation.set()
        self.window.destroy()


def main():
    dpi_user = ctypes.WinDLL('user32', use_last_error=True)
    try:
        dpi_user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        dpi_user.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        dpi_user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        dpi_user.SetProcessDPIAware()
    parser = argparse.ArgumentParser()
    parser.add_argument('--theme', choices=PAGE_THEMES)
    args = parser.parse_args()
    # 启动入口共用一把锁，避免两个进程同时覆盖 TXT。
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    instance_name = 'Local\\ZhuoyueXingCheckin_' + str(ROOT).replace('\\', '_').replace(':', '')
    lock = kernel.CreateMutexW(None, False, instance_name)
    if ctypes.get_last_error() == 183:
        user = ctypes.WinDLL('user32')
        # A portable copy and the live project have identical titles. Identify
        # the window for this data folder rather than raising an unrelated copy.
        user.GetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        user.GetPropW.restype = ctypes.c_void_p
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
        found = []
        @callback_type
        def matching_window(handle, _):
            if user.GetPropW(handle, instance_name):
                found.append(handle)
                return False
            return True
        user.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
        user.EnumWindows(matching_window, 0)
        existing = found[0] if found else None
        if existing:
            user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user.ShowWindow(existing, 9)
            user.SetForegroundWindow(existing)
        else:
            window = tk.Tk(); window.withdraw()
            messagebox.showinfo('助手正在启动', '助手正在启动，请稍等；若仍无窗口，请查看启动日志.txt。')
            window.destroy()
        return
    window = tk.Tk()
    window.update_idletasks()
    user = ctypes.WinDLL('user32')
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    user.SetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p]
    user.SetPropW.restype = ctypes.c_bool
    handle = user.GetAncestor(window.winfo_id(), 2)
    user.SetPropW(handle, instance_name, ctypes.c_void_p(1))
    try:
        App(window, args.theme)
        window.mainloop()
    except Exception as error:
        messagebox.showerror('无法启动', str(error), parent=window)
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        if lock:
            kernel.CloseHandle(lock)


if __name__ == '__main__':
    main()


