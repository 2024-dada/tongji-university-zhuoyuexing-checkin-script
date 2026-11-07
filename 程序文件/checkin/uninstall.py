"""Guarded self-removal helpers for the portable Windows application."""
import os
from pathlib import Path

INSTALL_MARKER = '.zhuoyuex-checkin-installation'
MARKER_CONTENT = b'zhuoyuex-checkin-installation-v1\n'


def get_install_root(program_root):
    program_root = Path(program_root).resolve(strict=True)
    return program_root.parent if program_root.name == '程序文件' else program_root


def _program_root(install_root):
    nested = install_root / '程序文件'
    return nested if (nested / 'app.py').is_file() else install_root


def ensure_install_marker(root, program_root=None):
    root = Path(root).resolve(strict=True)
    program = Path(program_root).resolve(strict=True) if program_root is not None else _program_root(root)
    if root != program and root not in program.parents:
        raise ValueError('程序目录与安装目录不匹配，卸载功能已锁定。')
    marker = program / INSTALL_MARKER
    if marker.exists():
        if marker.read_bytes() != MARKER_CONTENT:
            raise ValueError('程序目录标识异常，卸载功能已锁定。')
        return marker
    required = ('app.py', 'launcher.pyw', 'config.json', 'checkin/storage.py',
                'checkin/credentials.py', 'automation/uninstall.ps1')
    if not all((program / name).is_file() for name in required):
        raise ValueError('无法验证程序目录，卸载功能已锁定。')
    marker.write_bytes(MARKER_CONTENT)
    return marker


def validate_install_root(root, program_root=None):
    path = Path(root).resolve(strict=True)
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError('程序目录路径不安全，卸载功能已锁定。')
    program = Path(program_root).resolve(strict=True) if program_root is not None else _program_root(path)
    if path != program and path not in program.parents:
        raise ValueError('程序目录与安装目录不匹配，卸载功能已锁定。')
    required = ('app.py', 'launcher.pyw', 'config.json',
                'checkin/storage.py', 'checkin/credentials.py', 'automation/uninstall.ps1')
    if not (program / INSTALL_MARKER).is_file() or not all((program / name).is_file() for name in required):
        raise ValueError('无法验证程序目录，卸载功能已锁定。')
    if (program / INSTALL_MARKER).read_bytes() != MARKER_CONTENT:
        raise ValueError('程序目录标识异常，卸载功能已锁定。')

    if path == Path.home().resolve():
        raise ValueError('程序目录是 Windows 或个人常用目录，卸载功能已锁定。')
    for name in ('WINDIR', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        value = os.environ.get(name)
        if value:
            protected_system_path = Path(value).resolve()
            if path == protected_system_path or protected_system_path in path.parents:
                raise ValueError('程序目录是 Windows 或个人常用目录，卸载功能已锁定。')

    personal_folders = []
    if os.name == 'nt':
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
        known_names = ('Desktop', 'Personal', 'My Pictures', 'My Music', 'My Video',
                       '{374DE290-123F-4565-9164-39C4925E467B}')
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for name in known_names:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    expanded = os.path.expandvars(value)
                    if expanded:
                        personal_folders.append(Path(expanded).resolve())
        except OSError:
            pass
    else:
        personal_folders.extend(Path.home()/name for name in
                                ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos'))
    for folder in personal_folders:
        if path == folder or path in folder.parents:
            raise ValueError('程序目录是 Windows 或个人常用目录，卸载功能已锁定。')
    return path

