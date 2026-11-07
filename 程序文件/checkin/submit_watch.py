"""Capture short website toasts while the normal OCR snapshot is busy."""
import queue
import base64
import subprocess
import threading


class SubmitWatch:
    def __init__(self, desktop, wait_for_arm=False):
        self.wait_for_arm = wait_for_arm
        self.messages = queue.Queue()
        source = (desktop.root / 'automation' / 'watch_submit.ps1').read_text('utf-8-sig')
        # Windows PowerShell 5 reads BOM-less UTF-8 scripts as ANSI. Preserve
        # Chinese patterns explicitly, as the normal snapshot reader does.
        snapshot = str(desktop.root / 'automation' / 'uia_snapshot.ps1').replace("'", "''")
        command = ('& {\n' + source + '\n} -WindowHandle ' + str(int(desktop.hwnd))
                   + " -SnapshotPath '" + snapshot + "'" + (' -WaitForArm' if wait_for_arm else ''))
        encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
        self.process = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-EncodedCommand', encoded],
            stdin=subprocess.PIPE if wait_for_arm else None,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8-sig', creationflags=0x08000000)
        def read():
            for line in self.process.stdout:
                self.messages.put(line.strip())
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            if self.messages.get(timeout=10) != 'READY':
                raise RuntimeError('提交提示监听启动失败，尚未点击发布。')
        except Exception:
            self.close()
            raise

    def arm(self):
        if self.wait_for_arm:
            if self.process.poll() is not None:
                raise RuntimeError('提交监听已停止，尚未点击发布。')
            # ASCII control line must not acquire the UTF-8-sig stream BOM.
            self.process.stdin.buffer.write(b'ARM\n')
            self.process.stdin.buffer.flush()
            try:
                if self.messages.get(timeout=5) != 'ARMED':
                    raise RuntimeError('提交监听未确认就绪，尚未点击发布。')
            except queue.Empty:
                raise RuntimeError('提交监听就绪超时，尚未点击发布。') from None

    def result(self):
        try:
            return self.messages.get_nowait()
        except queue.Empty:
            return ''

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()
        if self.process.stdin:
            self.process.stdin.close()
