"""记住上一次打卡停在哪个对象，下一次从那里继续。

这里只保存“扫描位置”，判重权威仍然是各星 TXT（History）；即使这个文件丢失、
损坏或被人手动删除，也只会让下一轮从列表开头重新查找，不会重复提交已记录对象。
"""
import json
import threading
from datetime import datetime
from pathlib import Path

from .storage import CHINA, atomic_write

FILENAME = '续跑记忆.json'
VERSION = 1
MAX_TEXT = 120


def list_key(theme, section=''):
    """A theme keeps one cursor per traversed list (section names the list)."""
    theme = str(theme or '').strip()
    section = str(section or '').strip()
    return theme if not section else theme + '|' + section


def _text(value):
    return str(value or '').strip()[:MAX_TEXT]


def _short(value):
    text = _text(value)
    return text if len(text) <= 24 else text[:24] + '…'


def _index(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def describe_time(entry):
    """Return a short local timestamp for logs and labels."""
    stamp = str((entry or {}).get('time') or '')
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp or '时间未知'
    return parsed.strftime('%m-%d %H:%M')


class ResumeMemory:
    """One small JSON file beside the per-theme TXT records."""

    def __init__(self, root):
        self.path = Path(root) / '打卡记录' / FILENAME
        self.lock = threading.RLock()
        self.entries = {}
        self.warning = ''
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text('utf-8-sig'))
        except (OSError, ValueError):
            self._quarantine('续跑记忆文件无法读取，已改名备份；本轮从列表开头查找，判重记录不受影响。')
            return
        if not isinstance(data, dict) or not isinstance(data.get('lists'), dict):
            self._quarantine('续跑记忆文件格式不正确，已改名备份；本轮从列表开头查找。')
            return
        entries = {}
        for key, value in data['lists'].items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            name = _text(value.get('object'))
            if not name:
                continue
            entries[key] = {'object': name, 'index': _index(value.get('index')),
                            'date': str(value.get('date') or ''), 'time': str(value.get('time') or '')}
        self.entries = entries

    def _quarantine(self, warning):
        stamp = datetime.now(CHINA).strftime('%Y%m%d-%H%M%S')
        try:
            self.path.replace(self.path.with_name(self.path.stem + '-损坏-' + stamp + '.json'))
        except OSError:
            pass
        self.warning = warning

    def _save(self):
        payload = {'version': VERSION, 'lists': self.entries}
        try:
            atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        except OSError as error:
            self.warning = '续跑记忆写入失败，本轮仍可正常打卡：' + str(error)

    # ---------------- read ----------------
    def entry(self, theme, section=''):
        with self.lock:
            value = self.entries.get(list_key(theme, section))
            return dict(value) if value else None

    def has_entries(self):
        with self.lock:
            return bool(self.entries)

    def describe(self, theme, section=''):
        value = self.entry(theme, section)
        if not value:
            return '尚无续跑记录'
        return '上次停在“' + value['object'] + '”（' + describe_time(value) + '）'

    def summary(self, themes=()):
        with self.lock:
            parts = []
            for key in sorted(self.entries):
                value = self.entries[key]
                theme = key.split('|', 1)[0]
                if themes and theme not in themes:
                    continue
                label = theme + ('·' + key.split('|', 1)[1] if '|' in key else '')
                parts.append(label + ' 停在“' + _short(value['object']) + '” ' +
                             describe_time(value))
            return '；'.join(parts) if parts else '尚无续跑记录，将从列表开头查找。'

    # ---------------- write ----------------
    def remember(self, theme, name, index=0, section=''):
        name = _text(name)
        if not name:
            return None
        now = datetime.now(CHINA)
        value = {'object': name, 'index': _index(index),
                 'date': now.date().isoformat(), 'time': now.isoformat(timespec='seconds')}
        with self.lock:
            self.entries[list_key(theme, section)] = value
            self._save()
        return dict(value)

    def forget(self, theme=None, section=None):
        """Remove everything, one theme's cursor, or one theme's list cursor."""
        with self.lock:
            if theme is None:
                keys = list(self.entries)
            elif section is None:
                keys = [key for key in self.entries if key.split('|', 1)[0] == theme]
            else:
                key = list_key(theme, section)
                keys = [key] if key in self.entries else []
            for key in keys:
                self.entries.pop(key, None)
            if keys:
                self._save()
            return len(keys)


class ResumePoint:
    """Skip the rows the previous run already passed before it stopped.

    ``index`` is the zero-based position of the remembered row in the previous
    scan order, so a row that was renamed still resumes at the same depth
    instead of skipping the whole list or re-checking from the top.
    """

    def __init__(self, entry, matches):
        entry = entry or {}
        self.matches = matches
        self.object = _text(entry.get('object'))
        self.left = _index(entry.get('index')) + 1 if self.object else 0
        self.active = bool(self.object)
        self.skipped = 0
        self.located = False

    @property
    def done(self):
        return not self.active

    def before(self, name):
        """True when this row belongs to the already processed prefix."""
        if not self.active:
            return False
        if self.object and self.matches(name, self.object):
            self.active, self.left, self.located = False, 0, True
            self.skipped += 1
            return True
        if self.left > 0:
            self.left -= 1
            self.skipped += 1
            self.active = self.left > 0
            return True
        self.active = False
        return False
