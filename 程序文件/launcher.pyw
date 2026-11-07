import ctypes
import os
import sys
import traceback
from pathlib import Path

root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
log_path = root / '启动日志.txt'
try:
    with log_path.open('a', encoding='utf-8') as stream:
        from datetime import datetime
        stream.write('\n启动：' + datetime.now().isoformat(timespec='seconds') + '\n')
    import app
    app.main()
except BaseException as error:
    if isinstance(error, SystemExit) and error.code in (None, 0):
        raise
    with log_path.open('a', encoding='utf-8') as stream:
        traceback.print_exc(file=stream)
    ctypes.windll.user32.MessageBoxW(None, '助手启动失败。详细原因已保存到：\n' + str(log_path), '卓越星助手启动错误', 0x10)
