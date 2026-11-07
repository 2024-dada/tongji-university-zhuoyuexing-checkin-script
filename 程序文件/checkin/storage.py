import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
import unicodedata

CHINA = timezone(timedelta(hours=8))
MATERIAL_FIELDS = ('text', 'image', 'image_warning', 'fields')


def today():
    return datetime.now(CHINA).date().isoformat()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def object_key(name):
    return ''.join(str(name).split()).casefold()


def object_keyword_key(name):
    value = unicodedata.normalize('NFKC', str(name or '')).casefold()
    return ''.join(char for char in value if char.isalnum())


def same_object_by_keyword(candidate, previous, *, minimum_containment=4,
                           minimum_fuzzy=6, similarity=.88):
    """Match a list label to a recorded object before opening its detail page."""
    current = object_keyword_key(candidate)
    known = object_keyword_key(previous)
    if not current or not known:
        return False
    if current == known:
        return True
    shorter = min(len(current), len(known))
    if shorter >= minimum_containment and (current in known or known in current):
        return True
    return (shorter >= minimum_fuzzy and
            SequenceMatcher(None, current, known, autojunk=False).ratio() >= similarity)


class History:
    """TXT 采用逐行 JSON；损坏时拒绝覆盖，以免丢失判重历史。"""

    def __init__(self, root, filename='打卡记录.txt'):
        if Path(filename).name != filename or not filename.endswith('.txt'):
            raise ValueError('记录文件名无效。')
        self.path = Path(root) / filename
        self.lock = threading.RLock()
        self.rows = []
        self.reload()

    def reload(self):
        """Read current TXT before selection; retain memory if parsing fails."""
        with self.lock:
            rows = []
            if self.path.exists():
                for number, line in enumerate(self.path.read_text('utf-8-sig').splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict) or row.get('status') not in (
                                '待提交', '已完成', '已放弃', '超出范围', '已跳过', '结果待核实'):
                            raise ValueError('状态无效')
                        if not all(row.get(k) for k in ('id', 'theme', 'object', 'date')):
                            raise ValueError('字段不完整')
                        rows.append(row)
                    except (ValueError, TypeError) as error:
                        raise ValueError(f'打卡记录第 {number} 行损坏，请修复后启动；原文件没有被覆盖。') from error
            self.rows = rows
            return len(rows)

    def _save(self, discard_materials_for=None):
        if self.path.exists():
            previous = self.path.read_text('utf-8-sig')
            if discard_materials_for:
                backup_rows = [json.loads(line) for line in previous.splitlines() if line.strip()]
                for backup_row in backup_rows:
                    if backup_row['id'] == discard_materials_for:
                        for key in MATERIAL_FIELDS:
                            backup_row.pop(key, None)
                previous = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in backup_rows)
            atomic_write(self.path.with_suffix('.txt.bak'), previous)
        atomic_write(self.path, ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.rows))

    def done_today(self, theme):
        return any(row['theme'] == theme and row['status'] == '已完成' and row['date'] == today() for row in self.rows)

    def pending(self, theme):
        return next((row for row in reversed(self.rows) if row['theme'] == theme and row['status'] in ('待提交', '结果待核实')), None)

    def used(self, theme, *, include_skipped=False):
        statuses = ('待提交', '已完成', '结果待核实', '已跳过') if include_skipped else ('待提交', '已完成', '结果待核实')
        return {object_key(row['object']) for row in self.rows if row['theme'] == theme and row['status'] in statuses}

    def reserve(self, theme, name, fields, *, independent_objects=False):
        with self.lock:
            if not independent_objects and self.pending(theme):
                raise ValueError('该主题已有待提交内容，请先恢复或放弃。')
            if object_key(name) in self.used(theme):
                raise ValueError('该对象已经使用过。')
            row = dict(id=uuid.uuid4().hex, date=today(), theme=theme, object=name,
                       status='待提交', fields=fields, text='', image='', phase='已选对象')
            self.rows.append(row)
            try:
                self._save()
            except Exception:
                self.rows.pop()
                raise
            return row

    def replace_pending(self, row_id, name, fields):
        """Keep old materials and reserve another object in one saved update."""
        with self.lock:
            row = next(r for r in self.rows if r['id'] == row_id)
            if row['status'] != '待提交':
                raise ValueError('只有尚未发布的待提交对象可以自动换目标。')
            if object_key(name) == object_key(row['object']):
                raise ValueError('换目标时必须选择不同对象。')
            original = row.copy()
            row.update(status='已放弃', phase='按用户要求自动另选目标，原材料保留')
            try:
                return self.reserve(row['theme'], name, fields)
            except Exception:
                row.clear()
                row.update(original)
                raise

    def update(self, row_id, **values):
        allowed = {'text', 'image', 'image_warning', 'phase', 'fields', 'status', 'confirmed_at', 'date', 'submit_result', 'submit_message', 'submit_started_at'}
        if set(values) - allowed:
            raise ValueError('不能修改记录标识或打卡对象。')
        with self.lock:
            row = next(r for r in self.rows if r['id'] == row_id)
            old = row.copy()
            row.update(values)
            try:
                self._save()
            except Exception:
                row.clear()
                row.update(old)
                raise
            return row

    def discard_pending(self, row_id):
        """Remove one rejected attempt without changing completed history."""
        with self.lock:
            index, row = next((i, r) for i, r in enumerate(self.rows) if r['id'] == row_id)
            if row['status'] not in ('待提交', '结果待核实'):
                raise ValueError('只能移除未完成的待提交记录。')
            self.rows.pop(index)
            try:
                self._save()
            except Exception:
                self.rows.insert(index, row)
                raise
            return row.copy()

    def clear_completed_materials(self, row_id):
        """Retain repeat-prevention identity/result, discard completed payload."""
        with self.lock:
            row = next(r for r in self.rows if r['id'] == row_id)
            if row['status'] != '已完成':
                raise ValueError('只能清理已经完成的打卡材料。')
            old = row.copy()
            for key in MATERIAL_FIELDS:
                row.pop(key, None)
            try:
                self._save(discard_materials_for=row_id)
            except Exception:
                row.clear()
                row.update(old)
                raise
            return row

    def confirm(self, row_id):
        with self.lock:
            row = next(r for r in self.rows if r['id'] == row_id)
            if row['status'] not in ('待提交', '结果待核实'):
                raise ValueError('只有待提交或结果待核实记录能人工确认。')
            return self.update(row_id, status='已完成', date=today(),
                               confirmed_at=datetime.now(CHINA).isoformat(), phase='用户确认已提交')

    def import_completed(self, theme, name, date):
        datetime.strptime(date, '%Y-%m-%d')
        if not name.strip():
            raise ValueError('对象名称不能为空。')
        if object_key(name) in self.used(theme):
            raise ValueError('此对象已有记录。')
        with self.lock:
            self.rows.append(dict(id=uuid.uuid4().hex, date=date, theme=theme,
                                  object=name.strip(), status='已完成', phase='用户补录'))
            try:
                self._save()
            except Exception:
                self.rows.pop()
                raise

    def completed_matches(self, keyword, theme=None):
        keyword = object_key(keyword)
        if not keyword:
            raise ValueError('请输入打卡对象的关键词，不能使用空关键词。')
        with self.lock:
            return [row.copy() for row in reversed(self.rows)
                    if row['status'] == '已完成' and (theme is None or row['theme'] == theme)
                    and keyword in object_key(row['object'])]

    def remove_completed(self, row_id):
        with self.lock:
            selected = next(((i, row) for i, row in enumerate(self.rows) if row['id'] == row_id), None)
            if not selected or selected[1]['status'] != '已完成':
                raise ValueError('只能移除选中的已完成记录；待提交和其他记录不变。')
            index, row = selected
            snapshot = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.rows)
            stamp = datetime.now(CHINA).strftime('%Y%m%d-%H%M%S-%f')
            backup = self.path.parent / '记录备份' / ('审核打回-' + stamp + '-' + uuid.uuid4().hex[:8] + '.txt')
            atomic_write(backup, snapshot)
            self.rows.pop(index)
            try:
                self._save()
            except Exception:
                self.rows.insert(index, row)
                raise
            return row.copy(), backup

    def clear_all(self):
        with self.lock:
            snapshot = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.rows)
            backup = None
            if snapshot:
                stamp = datetime.now(CHINA).strftime('%Y%m%d-%H%M%S-%f')
                backup = self.path.parent / '记录备份' / ('清空记录-' + stamp + '-' + uuid.uuid4().hex[:8] + '.txt')
                atomic_write(backup, snapshot)
            previous = self.rows[:]
            self.rows.clear()
            try:
                self._save()
            except Exception:
                self.rows[:] = previous
                raise
            return backup

    def clear_image_references(self):
        """Forget every saved local image path while preserving all check-in records."""
        with self.lock:
            previous = [row.copy() for row in self.rows]
            changed = 0
            for row in self.rows:
                had_image_data = bool(row.get('image') or row.get('image_warning'))
                if row.get('image') or 'image' in row:
                    row['image'] = ''
                    had_image_data = True
                if row.get('image_warning') or 'image_warning' in row:
                    row['image_warning'] = ''
                    had_image_data = True
                if row.get('status') == '待提交' and row.get('theme') != '明德之星':
                    row['image_warning'] = '配图已卸载，请重新选择或重新生成。'
                    row['phase'] = '配图已卸载，需重新选择'
                    had_image_data = True
                if had_image_data:
                    changed += 1
            if not changed:
                return 0
            try:
                self._save()
            except Exception:
                self.rows[:] = previous
                raise
            return changed


class ThemeHistory(History):
    """Five authoritative TXT files; legacy files are imported exactly once."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / '打卡记录.txt'  # Compatibility for material/backup roots.
        self.directory = self.root / '打卡记录'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.rows = []
        marker = self.directory / '.已迁移'
        if not marker.exists():
            sources = (History(self.root),
                       History(self.root / '矢志之星数据', '矢志之星打卡记录.txt'))
            existing = self._read_files()
            merged = {row['id']: row for source in sources for row in source.rows}
            merged.update({row['id']: row for row in existing})
            self.rows = list(merged.values())
            self._save()
            atomic_write(marker, '旧记录已导入；以后只读取各星 TXT。\n')
        self.reload()

    def theme_path(self, theme):
        from .batch import PAGE_THEMES
        if theme not in PAGE_THEMES:
            raise ValueError('主题范围无效。')
        return self.directory / (theme + '打卡记录.txt')

    def _read_files(self):
        from .batch import PAGE_THEMES
        rows = []
        for theme in PAGE_THEMES:
            store = History(self.directory, self.theme_path(theme).name)
            if any(row['theme'] != theme for row in store.rows):
                raise ValueError(theme + '记录包含其他主题，请修复后重试；原文件未覆盖。')
            rows.extend(store.rows)
        identifiers = [row['id'] for row in rows]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('各星记录存在重复标识，请修复后重试。')
        return rows

    def reload(self):
        with self.lock:
            rows = self._read_files()
            self.rows = rows
            return len(rows)

    def _save(self, discard_materials_for=None):
        from .batch import PAGE_THEMES
        if any(row['theme'] not in PAGE_THEMES for row in self.rows):
            raise ValueError('历史包含未知主题，未覆盖记录。')
        for theme in PAGE_THEMES:
            path = self.theme_path(theme)
            text = ''.join(json.dumps(row, ensure_ascii=False) + '\n'
                           for row in self.rows if row['theme'] == theme)
            previous = path.read_text('utf-8-sig') if path.exists() else None
            if previous == text:
                continue
            if previous:
                backup_rows = [json.loads(line) for line in previous.splitlines() if line.strip()]
                for row in backup_rows:
                    if row['id'] == discard_materials_for:
                        for key in MATERIAL_FIELDS:
                            row.pop(key, None)
                atomic_write(path.with_suffix('.txt.bak'), ''.join(
                    json.dumps(row, ensure_ascii=False) + '\n' for row in backup_rows))
            atomic_write(path, text)
