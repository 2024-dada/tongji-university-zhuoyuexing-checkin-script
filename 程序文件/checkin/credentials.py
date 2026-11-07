"""只用 Windows 当前用户 DPAPI 保存凭据，不记录明文。"""
import ctypes
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def transform(data, decrypt=False):
    memory = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), memory)
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes = [wintypes.HLOCAL]
    free.restype = wintypes.HLOCAL
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        free(result.pbData)


def save_key(root, key):
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError('API key 不能为空或含空格。')
    path = Path(root) / '.private' / 'deepseek.dpapi'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(transform(key.encode('utf-8')))


def load_key(root):
    path = Path(root) / '.private' / 'deepseek.dpapi'
    if not path.exists():
        raise ValueError('尚未设置 DeepSeek key，请在设置窗口保存。')
    return transform(path.read_bytes(), decrypt=True).decode('utf-8')
