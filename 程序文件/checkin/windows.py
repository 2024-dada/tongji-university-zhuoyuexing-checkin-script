"""独立脚本使用 Windows 自带接口；不依赖 Codex 运行中的电脑操控工具。"""
import base64
import uuid
import ctypes
import io
import json
import subprocess
import time
from ctypes import wintypes
from pathlib import Path


class Stopped(RuntimeError):
    pass


def browser_window(windows, title_hint='矢志之星'):
    hints = (title_hint, '选择打卡模块') if title_hint == '矢志之星' else ((title_hint, '弘文之星打卡', '明德之星打卡', '求索之星打卡', '力行之星打卡') if title_hint == '选择打卡模块' else (title_hint,))
    candidates = [w for w in windows if any(hint in w['title'] for hint in hints)]
    if len(candidates) != 1:
        raise RuntimeError('请在 Edge 中只保留一个已登录的矢志之星或选择打卡模块页面，再启动此工具。')
    return candidates[0]


class Desktop:
    def __init__(self, root, cancel, browser=False):
        self.root, self.cancel = Path(root), cancel
        self.browser_mode = browser
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.SetProcessDPIAware()
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.hwnd = None
        self.browser_hwnd = None
        self.ocr_exclusions = lambda: ()
        self.overlay_visibility = lambda hidden: None

    def check(self):
        if self.cancel.is_set() or self.user.GetAsyncKeyState(0x1B) & 0x8000:
            self.cancel.set()
            raise Stopped('操作已停止；待提交内容保留。')

    def pause(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(.08)

    def process(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ''
        try:
            size = wintypes.DWORD(32768)
            result = ctypes.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, result, ctypes.byref(size)):
                return result.value
            return ''
        finally:
            self.kernel.CloseHandle(handle)

    def windows(self):
        values = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]

        @callback_type
        def collect(hwnd, param):
            if self.user.IsWindowVisible(hwnd):
                size = self.user.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(size + 1)
                self.user.GetWindowTextW(hwnd, title, size + 1)
                process = self.process(hwnd)
                app_name = Path(process).name.lower()
                if app_name in (('msedge.exe',) if self.browser_mode else ('wxwork.exe', 'wxworkweb.exe')):
                    values.append({'hwnd': hwnd, 'title': title.value, 'process': process})
            return True

        self.user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user.EnumWindows(collect, 0)
        return values

    def connect(self, executable):
        if self.browser_mode:
            if self.hwnd is None:
                self.connect_browser('选择打卡模块')
            else:
                self.focus()
            return
        candidates = self.windows()
        if not candidates:
            if not executable or not Path(executable).is_file():
                import os
                candidates_paths = [Path(os.environ.get(base, '')) / 'Tencent' / folder / 'WXWork.exe'
                                    for base in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')
                                    for folder in ('WXWork', 'WeChatWork')]
                executable = next((str(p) for p in candidates_paths if p.is_file()), '')
            if not executable or not Path(executable).is_file():
                raise RuntimeError('未找到企业微信。请手动打开，或在设置里选择 WXWork.exe 路径。')
            subprocess.Popen([executable])
            for _ in range(40):
                self.pause(.25)
                candidates = self.windows()
                if candidates:
                    break
        main = [w for w in candidates if w['title'] == '企业微信']
        if len(main) == 1:
            self.hwnd = main[0]['hwnd']
        elif len(candidates) == 1:
            self.hwnd = candidates[0]['hwnd']
        else:
            raise RuntimeError('企业微信目标窗口不唯一或没有窗口，请手动打开主窗口后重试。')
        self.focus()

    def connect_browser(self, title_hint='矢志之星'):
        self.browser_mode = True
        self.hwnd = browser_window(self.windows(), title_hint)['hwnd']
        self.browser_hwnd = self.hwnd
        self.focus()

    def focus(self):
        self.check()
        if not self.hwnd:
            raise RuntimeError('尚未选择企业微信窗口。')
        self.user.IsWindow.argtypes = [wintypes.HWND]
        if not self.user.IsWindow(self.hwnd):
            if self.browser_mode:
                raise RuntimeError('原 Edge 窗口已关闭，请重新开始。')
            current = [w for w in self.windows() if
                       ('矢志之星' in w['title'] if self.browser_mode else w['title'] == '企业微信')]
            if len(current) != 1:
                raise RuntimeError('目标窗口已关闭，请重新打开后重试。')
            self.hwnd = current[0]['hwnd']
        if self.user.IsIconic(self.hwnd):
            self.user.ShowWindow(self.hwnd, 9)
        if self.user.GetAncestor(self.user.GetForegroundWindow(), 2) != self.hwnd:
            self.user.SetForegroundWindow(self.hwnd)
        self.pause(.25)
        if self.user.GetAncestor(self.user.GetForegroundWindow(), 2) != self.hwnd:
            # A worker launched from the assistant may not own the latest input.
            # Attach briefly for activation only; always detach before any click.
            active_thread = self.user.GetWindowThreadProcessId(self.user.GetForegroundWindow(), None)
            current_thread = self.kernel.GetCurrentThreadId()
            self.user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            attached = active_thread != current_thread and self.user.AttachThreadInput(current_thread, active_thread, True)
            target_thread = self.user.GetWindowThreadProcessId(self.hwnd, None)
            target_attached = target_thread not in (current_thread, active_thread) and self.user.AttachThreadInput(current_thread, target_thread, True)
            try:
                self.user.BringWindowToTop.argtypes = [wintypes.HWND]
                self.user.BringWindowToTop(self.hwnd)
                self.user.SetForegroundWindow(self.hwnd)
            finally:
                if target_attached:
                    self.user.AttachThreadInput(current_thread, target_thread, False)
                if attached:
                    self.user.AttachThreadInput(current_thread, active_thread, False)
            self.pause(.25)
        if self.user.GetAncestor(self.user.GetForegroundWindow(), 2) != self.hwnd:
            raise RuntimeError('目标窗口未能获得焦点，已停止，避免误点其他窗口。')

    def snapshot(self, activate=True):
        if activate:
            self.focus()
        self.check()
        process_name = Path(self.process(self.hwnd)).name.lower()
        allowed = ('msedge.exe',) if self.browser_mode else ('wxwork.exe', 'wxworkweb.exe')
        if process_name not in allowed:
            raise RuntimeError('目标窗口已失效，已停止。')
        helper = self.root / 'automation' / 'uia_snapshot.ps1'
        try:
            command = '& {\n' + helper.read_text('utf-8-sig') + '\n} ' + str(int(self.hwnd))
            if getattr(self, 'include_offscreen', False):
                command += ' -IncludeOffscreen'
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                      '-Command', command], capture_output=True,
                                     timeout=18, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            raise RuntimeError('页面控件读取超时，请等待页面加载后重试。') from None
        if result.returncode:
            raise RuntimeError('无法读取目标页面控件。请先完成登录并打开卓越星。')
        text = result.stdout.decode('utf-8-sig', errors='replace').strip()
        try:
            nodes = json.loads(text)
            nodes = nodes if isinstance(nodes, list) else [nodes]
            if activate and (len([n for n in nodes if n.get('name')]) <= 2 or (
                    not any(n.get('id') == 'msaa' for n in nodes) and
                    not any(n.get('id') == '1148' for n in nodes))):
                nodes += self.ocr_nodes()
            return nodes
        except ValueError:
            raise RuntimeError('页面控件返回格式错误。') from None

    def ocr_nodes(self):
        # Built-in Windows OCR only. Temporary captures never leave this computer.
        self.focus()
        from PIL import ImageGrab
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        folder = self.root / '.private'
        folder.mkdir(parents=True, exist_ok=True)
        capture = folder / ('capture-' + uuid.uuid4().hex + '.png')
        try:
            ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(capture)
            source = (self.root / 'automation' / 'ocr_snapshot.ps1').read_text('utf-8-sig')
            command = '& {\n' + source + "\n} '" + str(capture).replace("'", "''") + "'"
            encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                    capture_output=True, timeout=18, creationflags=0x08000000)
            if result.returncode:
                raise RuntimeError('本机中文文字识别失败，已停止。')
            values = json.loads(result.stdout.decode('utf-8-sig'))
            nodes, seen = [], set()
            for value in values if isinstance(values, list) else [values]:
                name = ''.join(str(value.get('text', '')).split())
                key = (name, round(value['x']), round(value['y']))
                if key in seen or not name:
                    continue
                screen_x = rect.left + value['x'] + value['width'] / 2
                screen_y = rect.top + value['y'] + value['height'] / 2
                if any(left <= screen_x <= right and top <= screen_y <= bottom
                       for left, top, right, bottom in self.ocr_exclusions()):
                    continue
                seen.add(key)
                nodes.append(dict(name=name, kind='Text', id='ocr', enabled=True,
                                  x=rect.left + value['x'], y=rect.top + value['y'],
                                  width=value['width'], height=value['height']))
            return nodes
        finally:
            capture.unlink(missing_ok=True)

    def window_name(self, hwnd):
        """Read a caption for diagnostics only; never used to choose a target."""
        if not hwnd:
            return ''
        length = self.user.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        self.user.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def window_class(self, hwnd):
        if not hwnd:
            return ''
        buffer = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def covering_window(self, x, y):
        """Top-level window at the point, or None when it is our target."""
        target = self.user.GetAncestor(self.user.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)
        return None if target == self.hwnd else target

    def describe_window(self, hwnd):
        name = self.window_name(hwnd).strip() or '无标题'
        process = Path(self.process(hwnd)).name if hwnd else ''
        kind = self.window_class(hwnd).strip()
        details = [name]
        if process:
            details.append(process)
        if kind:
            details.append(kind)
        return '，'.join(details)

    def click(self, node, allow_protected=False):
        name = str(node.get('name', ''))
        if not allow_protected and any(word in name for word in ('发布', '矢志', '登录', '统一身份认证')):
            raise RuntimeError('此按钮需要用户操作或不在打卡范围：' + name)
        if not node.get('enabled', True):
            raise RuntimeError('目标控件当前不可用：' + name)
        self.focus()
        self.check()
        x, y = int(node['x'] + node['width'] / 2), int(node['y'] + node['height'] / 2)
        covered = any(left <= x <= right and top <= y <= bottom
                      for left, top, right, bottom in self.ocr_exclusions())
        if covered:
            self.overlay_visibility(True)
        try:
            # A page that is still loading can be covered for a moment by the
            # host's own transient window. Wait it out, then verify again; never
            # click while the point belongs to another window.
            blocker = self.covering_window(x, y)
            for _ in range(3):
                if blocker is None:
                    break
                self.pause(.5)
                self.focus()
                self.check()
                blocker = self.covering_window(x, y)
            if blocker is not None:
                raise RuntimeError('目标被其他窗口遮挡（' + self.describe_window(blocker) +
                                   '），已停止，避免误点。请移开该窗口后点击“继续当前步骤”。')
            self.user.SetCursorPos(x, y)
            self.user.mouse_event(0x0002, 0, 0, 0, 0)
            self.user.mouse_event(0x0004, 0, 0, 0, 0)
            self.pause(.35)
        finally:
            if covered:
                self.overlay_visibility(False)

    def drag(self, node, distance_y):
        """Move a measured date-wheel row with a bounded pointer gesture."""
        self.focus()
        self.check()
        x = int(node['x']+node['width']/2)
        start = int(node['y']+node['height']/2)
        end = int(start+distance_y)
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        if not (rect.left <= x < rect.right and rect.top <= start < rect.bottom
                and rect.top <= end < rect.bottom):
            raise RuntimeError('日期滚轮拖动超出目标窗口，已停止。')
        for y in (start, end):
            target = self.user.GetAncestor(self.user.WindowFromPoint(wintypes.POINT(x, y)), 2)
            if target != self.hwnd:
                raise RuntimeError('日期滚轮被其他窗口遮挡，请移开遮挡窗口后重试。')
        self.user.SetCursorPos(x, start)
        self.user.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            for index in range(1, 17):
                self.check()
                self.user.SetCursorPos(x, round(start+(end-start)*index/16))
                self.pause(.025)
            # Stop before release so the wheel does not coast past the row.
            self.pause(.15)
        finally:
            self.user.mouse_event(0x0004, 0, 0, 0, 0)
        self.pause(.5)

    def key(self, code):
        self.focus()
        self.check()
        self.user.keybd_event(code, 0, 0, 0)
        self.user.keybd_event(code, 0, 2, 0)

    def refresh(self):
        self.focus()
        self.check()
        self.user.keybd_event(0x11, 0, 0, 0)
        try:
            self.user.keybd_event(0x52, 0, 0, 0)
            self.user.keybd_event(0x52, 0, 2, 0)
        finally:
            self.user.keybd_event(0x11, 0, 2, 0)
        self.pause(1)

    def paste(self, value):
        self.focus()
        self.check()
        encoded = (str(value) + '\0').encode('utf-16-le')
        self.kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalLock.restype = ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self.user.OpenClipboard.argtypes = [wintypes.HWND]
        self.user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user.SetClipboardData.restype = wintypes.HANDLE
        block = self.kernel.GlobalAlloc(0x0002, len(encoded))
        if not block:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = self.kernel.GlobalLock(block)
        if not pointer:
            self.kernel.GlobalFree(block)
            raise RuntimeError('无法分配剪贴板内存。')
        ctypes.memmove(pointer, encoded, len(encoded))
        self.kernel.GlobalUnlock(block)
        if not self.user.OpenClipboard(self.hwnd):
            self.kernel.GlobalFree(block)
            raise RuntimeError('剪贴板被占用，请重试。')
        try:
            self.user.EmptyClipboard()
            if not self.user.SetClipboardData(13, block):
                self.kernel.GlobalFree(block)
                raise RuntimeError('剪贴板写入失败。')
        finally:
            self.user.CloseClipboard()
        # 输入仅粘贴普通文字；不执行命令。
        for key in (0x41, 0x56):
            self.user.keybd_event(0x11, 0, 0, 0)
            try:
                self.check()
                self.user.keybd_event(key, 0, 0, 0)
                self.user.keybd_event(key, 0, 2, 0)
            finally:
                self.user.keybd_event(0x11, 0, 2, 0)
        self.pause(.25)

    def set_dialog_filename(self, field, filename):
        """Write and read back the native Edit, bypassing the shared clipboard."""
        self.focus()
        handle = field.get('handle')
        self.user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        if not handle or not self.user.IsChild(self.hwnd, handle):
            return False
        self.user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
            wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.user.SendMessageTimeoutW.restype = wintypes.LPARAM
        result = ctypes.c_size_t()
        value = ctypes.create_unicode_buffer(filename)
        actual = ctypes.create_unicode_buffer(len(value))
        send = self.user.SendMessageTimeoutW
        return bool(send(handle, 0x000C, 0, ctypes.addressof(value), 2, 1000, ctypes.byref(result))
                    and result.value
                    and send(handle, 0x000D, len(actual), ctypes.addressof(actual), 2, 1000, ctypes.byref(result))
                    and actual.value == filename)

    def back(self):
        """Click the measured WeCom mini-program back arrow."""
        self.focus()
        self.check()
        if self.browser_mode:
            self.user.keybd_event(0x12, 0, 0, 0)
            try:
                self.user.keybd_event(0x25, 0, 0, 0)
                self.user.keybd_event(0x25, 0, 2, 0)
            finally:
                self.user.keybd_event(0x12, 0, 2, 0)
        else:
            left, top, right, bottom = self.window_bounds()
            if right - left < 250 or bottom - top < 180:
                raise RuntimeError('企业微信窗口尺寸异常，无法安全点击页面返回。')
            dpi = 96
            try:
                get_dpi = self.user.GetDpiForWindow
                get_dpi.argtypes = [wintypes.HWND]
                get_dpi.restype = wintypes.UINT
                dpi = get_dpi(self.hwnd) or 96
            except (AttributeError, OSError):
                pass
            scale = dpi / 96
            # Measured from the current WeCom window screenshot: the back arrow
            # center is at (92, 56) relative to the window's top-left at 100% scaling.
            offset_x, offset_y = round(92 * scale), round(56 * scale)
            self.click(dict(name='企业微信页面返回', kind='Button', enabled=True,
                            x=left + offset_x - 18 * scale,
                            y=top + offset_y - 18 * scale,
                            width=36 * scale, height=36 * scale))
        self.pause(.45)

    def visible_bounds(self):
        self.check()
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def scroll(self, down=True, horizontal=False, x=None, y=None, amount=480):
        self.focus()
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self.user.SetCursorPos(x or rect.right - 80, y or (rect.top + rect.bottom) // 2)
        flag = 0x1000 if horizontal else 0x0800
        delta = (amount if down else -amount) if horizontal else (-amount if down else amount)
        self.user.mouse_event(flag, 0, 0, ctypes.c_ulong(delta).value, 0)
        self.pause(.5)

    def window_bounds(self):
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def horizontal_scrollbar(self, top, bottom):
        from .scrollbars import locate_scrollbar
        from PIL import Image
        data, (x, y) = self.screenshot(with_origin=True)
        picture = Image.open(io.BytesIO(data))
        bar = locate_scrollbar(picture, (15, top-y, picture.width-25, bottom-y))
        if bar:
            return {key: value + (y if key == 'y' else x) for key, value in bar.items()}
        return None

    def vertical_scrollbar(self, top):
        from .scrollbars import locate_scrollbar
        from PIL import Image
        data, (x, y) = self.screenshot(with_origin=True)
        picture = Image.open(io.BytesIO(data))
        bar = locate_scrollbar(picture, (picture.width-25, top-y, picture.width, picture.height-8), False)
        if bar:
            return {key: value + (x if key == 'x' else y) for key, value in bar.items()}
        return None

    def drag_pointer(self, start, end):
        """Drag a measured scrollbar thumb without following other windows."""
        self.focus()
        self.check()
        left, top, right, bottom = self.window_bounds()
        for x, y in (start, end):
            if not (left <= x < right and top <= y < bottom):
                raise RuntimeError('滚动条拖动超出浏览器窗口，已停止。')
            target = self.user.GetAncestor(self.user.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)
            if target != self.hwnd:
                raise RuntimeError('滚动条被其他窗口遮挡，请移开后重试。')
        self.user.SetCursorPos(int(start[0]), int(start[1]))
        self.user.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            for step in range(1, 17):
                self.check()
                self.user.SetCursorPos(round(start[0]+(end[0]-start[0])*step/16),
                                       round(start[1]+(end[1]-start[1])*step/16))
                self.pause(.025)
        finally:
            self.user.mouse_event(0x0004, 0, 0, 0, 0)
        self.pause(.5)

    def screenshot(self, with_origin=False):
        self.focus()
        from PIL import ImageGrab
        rect = wintypes.RECT()
        self.user.GetWindowRect(self.hwnd, ctypes.byref(rect))
        picture = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        buffer = io.BytesIO()
        picture.save(buffer, 'PNG')
        data = buffer.getvalue()
        return (data, (rect.left, rect.top)) if with_origin else data


def find(nodes, label, kinds=None):
    exact = [n for n in nodes if str(n.get('name', '')).strip() == label and
             (kinds is None or n.get('kind') in kinds)]
    if exact:
        return exact[-1]
    matches = [n for n in nodes if label in str(n.get('name', '')) and
               (kinds is None or n.get('kind') in kinds) and
               not any(w in str(n.get('name', '')) for w in ('发布', '矢志'))]
    # 不能依据多义名字随意选择第一个。
    return matches[0] if len(matches) == 1 else None
