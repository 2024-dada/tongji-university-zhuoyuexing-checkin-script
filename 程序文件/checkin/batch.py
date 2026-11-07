"""Page quotas and user-confirmed progress for the integrated assistant."""
import re

THEMES = ('弘文之星', '明德之星', '求索之星', '力行之星')
PAGE_THEMES = ('弘文之星', '明德之星', '矢志之星', '求索之星', '力行之星')
ALL_THEMES = '打卡所有卓越星'


def remaining_counts(nodes):
    headings = {}
    for theme in PAGE_THEMES:
        matches = [n for n in nodes if str(n.get('name', '')).replace(' ', '').startswith(theme)
                   and not str(n.get('name', '')).replace(' ', '').startswith(theme + '打卡')]
        if matches:
            headings[theme] = min(matches, key=lambda n: (n['y'], n.get('height', 0)))
    ordered = sorted(headings, key=lambda theme: headings[theme]['y'])
    result = {theme: None for theme in THEMES}
    if "矢志之星" in headings:
        result["矢志之星"] = None
    for index, theme in enumerate(ordered):
        if theme not in result:
            continue
        heading = headings[theme]
        lower = heading['y'] - 5
        upper = headings[ordered[index+1]]['y'] - 5 if index+1 < len(ordered) else heading['y'] + max(180, heading.get('height', 20)*8)
        values = set()
        for node in nodes:
            if not lower <= node['y'] < upper:
                continue
            text = ''.join(str(node.get('name', '')).split())
            match = re.search(r'(?:今日|今天)(?:还)?(?:可以|可)?打卡(?:还剩|剩余)?(\d+)次', text)
            if match:
                values.add(int(match.group(1)))
            elif re.search(r'(?:今日|今天).*次数.*(?:已用完|用尽|为0)', text):
                values.add(0)
        if len(values) == 1:
            result[theme] = values.pop()
    return result


class BatchSession:
    def __init__(self, date, themes=THEMES):
        self.date, self.themes = date, tuple(themes)
        if not self.themes or any(t not in THEMES for t in self.themes):
            raise ValueError('主题范围无效。')
        self.remaining = {theme: None for theme in self.themes}
        self.confirmed = {theme: 0 for theme in self.themes}
        self.active = True

    def refresh(self, counts):
        self.shizhi_snapshot = counts.get("矢志之星")
        for theme in self.themes:
            value = counts.get(theme)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError('未能识别 ' + theme + ' 的剩余次数，已暂停。')
        for theme in self.themes:
            value = counts[theme]
            old = self.remaining[theme]
            # A stale page must not restore a quota already consumed locally.
            self.remaining[theme] = value if old is None else min(old, value)

    def next_theme(self, pending=()):
        if not self.active:
            return None
        for theme in tuple(pending) + self.themes:
            if theme in self.remaining and (self.remaining[theme] or 0) > 0:
                return theme
        return None

    def confirm(self, theme):
        if not self.active or (self.remaining.get(theme) or 0) <= 0:
            raise ValueError('本次任务没有该主题的可用次数。')
        self.remaining[theme] -= 1
        self.confirmed[theme] += 1

    def summary(self):
        parts = [theme + '剩余' + str(self.remaining[theme]) + '次' for theme in self.themes]
        count = getattr(self, 'shizhi_snapshot', None)
        parts.append(f'矢志之星剩余{count}次（企业微信扫描）' if count is not None else '矢志之星次数未读到')
        return '；'.join(parts)
