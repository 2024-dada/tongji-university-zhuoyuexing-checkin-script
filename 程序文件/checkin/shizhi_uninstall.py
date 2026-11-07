"""Remove only the standalone Shizhi application from a shared installation."""
import base64
import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


FILES = ('shizhi_app.pyw', 'shizhi_launcher.pyw', 'checkin/shizhi.py',
         'checkin/shizhi_uninstall.py')


def owned_command(path, root):
    if not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_text('utf-8')
        if '-EncodedCommand ' in text:
            encoded = text.split('-EncodedCommand ', 1)[1].split()[0]
            text = base64.b64decode(encoded, validate=True).decode('utf-16le')
        pattern = re.escape(str(root)) + r'''(?:['"]|[\\/]shizhi_launcher\.pyw['"])'''
        return bool(re.search(pattern, text, re.IGNORECASE)) and 'shizhi_launcher.pyw' in text
    except (OSError, UnicodeError, ValueError):
        return False


def targets(root, desktop=None):
    from checkin.uninstall import validate_install_root
    root = Path(root).resolve(strict=True)
    validate_install_root(root, root)
    if not all((root / name).is_file() for name in FILES):
        raise ValueError('无法验证矢志之星程序文件，卸载已锁定。')
    result = [root / '矢志之星数据'] + [root / name for name in FILES]
    for folder, stems in ((root / '__pycache__', ('shizhi_app', 'shizhi_launcher')),
                          (root / 'checkin' / '__pycache__', ('shizhi', 'shizhi_uninstall'))):
        if folder.is_dir() and not folder.is_symlink() and not folder.is_junction():
            for stem in stems:
                result.extend(folder.glob(stem + '.cpython-*.pyc'))
    # Remove known launchers only if their content names this installation.
    for path in (root.parent / '矢志之星.cmd',
                 root.parent.parent / '矢志之星.cmd',
                 Path(desktop) / '矢志之星.cmd' if desktop else None):
        if path is not None and owned_command(path, root):
            result.append(path)
    for path in result:
        # Resolve the parent before removal, never follow an entry itself.
        if path.parent.resolve() != path.parent.absolute():
            raise ValueError('卸载路径包含链接目录，已停止。')
        if path.is_symlink() or path.is_junction():
            raise ValueError('卸载目标包含链接，已停止。')
    return result


def remove_entry(path):
    """Remove children without traversing symbolic links or Windows junctions."""
    if path.is_symlink():
        path.unlink()
    elif path.is_junction():
        path.rmdir()
    elif path.is_dir():
        for child in path.iterdir():
            remove_entry(child)
        path.rmdir()
    else:
        path.unlink(missing_ok=True)


def desktop_directory():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
        value, _ = winreg.QueryValueEx(key, 'Desktop')
    return Path(os.path.expandvars(value))


def launch(root):
    desktop = desktop_directory()
    targets(root, desktop)
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root),
                      str(os.getpid()), str(desktop)], cwd=tempfile.gettempdir(),
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def run(root, pid, desktop):
    root = Path(root).resolve(strict=True)
    sys.path.insert(0, str(root))
    plan = targets(root, desktop)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
                raise OSError('无法等待助手退出，未删除程序。')
        finally:
            kernel.CloseHandle(handle)
    elif ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: already exited
        raise ctypes.WinError(ctypes.get_last_error())
    # Hold the application lock so another launch cannot write during removal.
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    mutex = kernel.CreateMutexW(None, False, r'Local\TongjiShizhiStandaloneAssistant')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if ctypes.get_last_error() == 183:
            raise ValueError('矢志之星已重新启动，未删除程序；请关闭后重试。')
        # Validate again after exit; never remove the parent directory.
        current = targets(root, desktop)
        if current != plan:
            raise ValueError('卸载范围已变化，未删除程序。')
        for path in plan:
            remove_entry(path)
    finally:
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        run(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None, '卸载未完整完成：\n' + str(error), '矢志之星卸载', 0x10)
