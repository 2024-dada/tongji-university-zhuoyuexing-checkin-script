"""矢志之星热门地标与路线的独立批次。"""
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path

from .deepseek import DeepSeek
from .credentials import load_key
from .engine import DailyLimit, Engine, NeedUser
from .images import ImageFinder, ImageSelectionRequired, ImageSearchUnavailable
from .resume import ResumePoint, describe_time
from .storage import History, object_keyword_key, same_object_by_keyword
from .windows import Desktop, find


THEME = '矢志之星'
HOME_PATH = '/app/pages-home/shizhi'
DETAIL_PATH = '/app/pages-home/detail/dibiao'
LIST_PATH = '/app/pages/index/dibiaoList'
LIST_PATHS = (LIST_PATH, '/app/pages-index/dibiaoList')
MODULE_PATH = '/app/pages/index/daka'
FORM_PATH = '/app/pages-daka/shizhi'
MINIMUM_CHARS = 80


class OutOfRange(RuntimeError):
    """The website rejected entry because the landmark is outside its range."""


class SubmitRejected(RuntimeError):
    """The website explicitly rejected this publish attempt."""


class CarouselIncomplete(NeedUser):
    """A section could not be fully scrolled; other sections remain usable."""


def clean(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split())


def landmark_name_key(value):
    return object_keyword_key(value)


def similar_landmark(left, right):
    return same_object_by_keyword(left, right, minimum_containment=6)


class ShizhiBatch:
    """只在页面明确给出可打卡状态时填写并提交。"""

    def __init__(self, source_root, data_root, config, cancel, report, *, history=None, resume=None):
        self.source_root = Path(source_root)
        self.data_root = Path(data_root)
        self.temp_root = self.data_root / '.shizhi-temp'
        self.config, self.cancel, self.report = config, cancel, report
        self.history = history if history is not None else History(self.data_root, '矢志之星打卡记录.txt')
        self.resume = resume
        self.nearby_index = None
        self.desktop = Desktop(self.source_root, cancel, browser=True)
        self.desktop.include_offscreen = True
        self.engine = Engine(self.source_root, config, self.history, None, cancel,
                             report, self._handoff, data_root=self.temp_root, browser=True,
                             resume=resume)
        self.engine.desktop = self.desktop
        raw_nodes = self.engine.nodes

        def guarded_nodes():
            nodes = self.selector_text_nodes(raw_nodes())
            if (self.desktop.browser_hwnd is not None and
                    self.desktop.hwnd == self.desktop.browser_hwnd):
                self.require_site(nodes)
                self.raise_if_daily_limit(nodes)
            return nodes

        # Engine.fill also reads controls directly; guard every page read so a tab
        # switch cannot cause text or images to be entered into another site.
        self.engine.nodes = guarded_nodes
        self.seen_this_run = set()

    @staticmethod
    def selector_text_nodes(nodes):
        """Recover translated popup text only when visible OCR anchors agree."""
        visible = [n for n in nodes if not n.get('offscreen')]
        semantic = [n for n in nodes if n.get('offscreen') and
                    clean(n.get('name')) == '输入地标关键词']
        anchors = [n for n in nodes if n.get('id') == 'ocr' and
                   clean(n.get('name')) == '输入地标关键词']
        if len(semantic) != 1 or len(anchors) != 1:
            return visible
        source, anchor = semantic[0], anchors[0]
        dx, dy = anchor['x'] - source['x'], anchor['y'] - source['y']
        actions = [n for n in nodes if n.get('offscreen') and clean(n.get('name')) == '去打卡']
        actual = [n for n in nodes if n.get('id') == 'ocr' and clean(n.get('name')) == '去打卡']
        aligned = sum(any(abs(n['y'] + dy - a['y']) < 35 and
                          abs(n['x'] + dx - a['x']) < 100 for n in actions) for a in actual)
        if not actual or aligned < min(2, len(actual)):
            return visible
        lower = anchor['y'] + anchor.get('height', 0)
        return visible + [dict(n, x=n['x'] + dx, y=n['y'] + dy, offscreen=False)
                          for n in nodes if n.get('offscreen') and n['y'] + dy >= lower]

    def _handoff(self, message):
        raise NeedUser(message)

    def check(self):
        self.desktop.check()

    def nodes(self):
        self.check()
        try:
            nodes = self.engine.nodes()
        except RuntimeError as error:
            if '目标窗口未能获得焦点' in str(error):
                # Read the already-selected Edge window without activating it.
                # This probe may end the batch; it must never permit a click.
                try:
                    self.raise_if_daily_limit(self.desktop.snapshot(activate=False))
                except DailyLimit:
                    raise
                except (OSError, RuntimeError):
                    pass
            raise
        self.raise_if_daily_limit(nodes)
        return nodes

    @staticmethod
    def quota_message(labels):
        text = clean(''.join(str(label) for label in labels))
        if re.search(r'(?:今天|今日)(?:打卡的|打卡)?次数(?:已用完|已耗尽|不足|已达上限)', text):
            return '今天打卡的次数已用完，已停止操作，矢志之星助手将自动退出。'
        return ''

    def raise_if_daily_limit(self, nodes):
        from urllib.parse import urlsplit
        url = self.page_url(nodes)
        if not url.startswith('https://star.tongji.edu.cn/app/'):
            return
        path = urlsplit(url).path
        if path not in (HOME_PATH, DETAIL_PATH, *LIST_PATHS, '/app/pages-daka/shizhi'):
            return
        message = self.quota_message(node.get('name', '') for node in nodes)
        if message:
            raise DailyLimit(message)

    @staticmethod
    def page_url(nodes):
        for node in nodes:
            for value in (node.get('value', ''), node.get('name', '')):
                value = str(value).strip()
                if 'star.tongji.edu.cn/app/' in value:
                    # Edge may omit the scheme in the address bar's ValuePattern.
                    match = re.fullmatch(r'(?:https://)?star\.tongji\.edu\.cn/app/[^\s<>"\']+', value)
                    if match:
                        url = match.group(0).rstrip('),;')
                        return url if url.startswith('https://') else 'https://' + url
        return ''

    def require_site(self, nodes):
        url = self.page_url(nodes)
        if not url:
            raise NeedUser('无法从 Edge 地址栏确认卓越星网址，已暂停。请保持矢志之星标签页为当前页后重试。')
        if not url.startswith('https://star.tongji.edu.cn/app/'):
            raise NeedUser('当前页面不是同济卓越星，请打开矢志之星首页后再开始。')
        return url

    def go_home(self):
        for _ in range(8):
            nodes = self.nodes()
            url = self.require_site(nodes)
            from urllib.parse import urlsplit
            parts = urlsplit(url)
            if parts.path == MODULE_PATH or (parts.path == FORM_PATH and not parts.query):
                return nodes, url
            if HOME_PATH in url:
                return nodes, url
            if not (DETAIL_PATH in url or self.is_list_url(url) or '/app/pages-daka/shizhi' in url):
                raise NeedUser('当前是卓越星内无法安全识别的页面；请返回矢志之星首页。')
            self.desktop.back()
        raise NeedUser('返回矢志之星首页失败，请手动返回后再开始。')

    def enter_module(self, nodes):
        """Enter or re-enter the available Shizhi module after a successful post."""
        from urllib.parse import urlsplit
        if urlsplit(self.require_site(nodes)).path == '/app/pages/index/my':
            footer = [max((n for n in nodes if clean(n.get('name')) == label and
                           n.get('kind') == 'Text'), key=lambda n:n.get('y',0),default=None)
                      for label in ('社区','排行','我的')]
            if not all(footer):
                raise NeedUser('成功后返回我的页面，但未识别底部入口，记录已保留。')
            community, ranking, mine = footer
            if (ranking['x'] <= community['x'] or
                    abs(mine['y']-community['y']) > max(8,community['height']) or
                    abs(ranking['y']-community['y']) > max(8,community['height'])):
                raise NeedUser('底部导航未对齐，已停止避免误点。')
            center = (community['x']+community['width']/2+ranking['x']+ranking['width']/2)/2
            size = max(12,min(50,community['height']))
            self.report('提交成功后返回我的页面，重新打开打卡模块……')
            self.desktop.click(dict(name='打卡入口加号',kind='Button',enabled=True,
                                    x=center-size/2,y=community['y']-community['height']-size/2,
                                    width=size,height=size))
            nodes = self.nodes()
        if urlsplit(self.require_site(nodes)).path == MODULE_PATH:
            headings = [n for n in nodes if clean(n.get('name')).startswith(THEME)
                        and '打卡' not in clean(n.get('name')) and self.visible(n)]
            target = self.unique_label_target(headings)
            next_y = min((n['y'] for n in nodes if n.get('y', 0) > target['y']
                          and any(clean(n.get('name')).startswith(t) for t in
                                  ('弘文之星', '明德之星', '求索之星', '力行之星'))), default=target['y'] + 180)
            local = [n for n in nodes if target['y'] <= n.get('y', -1) < next_y]
            message = self.quota_message(n.get('name', '') for n in local)
            if message:
                raise DailyLimit(message)
            if not any(re.search(r'今日可以打卡[1-9]\d*次', clean(n.get('name'))) for n in local):
                raise NeedUser('未能确认矢志之星剩余次数，请等待页面加载后继续。')
            self.report('矢志仍有次数，进入地标选择和自动填写流程。')
            self.desktop.click(target, allow_protected=True)
            return self.nodes()
        return nodes

    def run_module(self, nodes):
        """Use the website's nearby-landmark selector and the existing publisher."""
        from urllib.parse import urlsplit
        self.enter_module(nodes)
        attempted_titles = set()
        for _ in range(100):
            nodes = self.enter_module(self.nodes())
            if urlsplit(self.require_site(nodes)).path != FORM_PATH:
                raise NeedUser('未进入矢志填写页，已停止避免操作其他页面。')
            self.engine.open_selector(nodes, THEME)
            for _ in range(30):
                loading = self.nodes()
                if any(clean(n.get('name')) == '去打卡' and self.visible(n) for n in loading):
                    break
                if any(clean(n.get('name')) in ('暂无数据', '暂无地标', '没有找到相关地标') for n in loading):
                    raise NeedUser('网站没有提供附近地标，请检查定位后继续。')
                self.report('等待网站定位并加载附近地标……')
                self.desktop.pause(1)
            else:
                raise NeedUser('附近地标列表仍未加载，已暂停，可点击继续重试。')
            # Titles bridge the module selector (no id in URL) and old numeric-id history.
            used = {str((r.get('fields') or {}).get('title') or r['object'])
                    for r in self.history.rows if r['theme'] == THEME and
                    (r['status'] == '已完成' or self.requires_verification(r))}
            used.update(attempted_titles)
            name, nodes = self.select_nearby_form(used)
            self.process_detail(nodes, self.require_site(nodes), name, '附近地标', form_ready=True)
            self.remember_position(name)
            attempted_titles.add(name)
            self.desktop.refresh()
            self.desktop.pause(1)
        raise NeedUser('附近地标处理达到安全上限，记录已保留。')

    def select_nearby_form(self, used):
        name = self.choose_nearby(used)
        try:
            return name, self.wait_form(name)
        except NeedUser:
            nodes = self.nodes()
            # Retry only an empty, still-unsubmitted selector form. A different
            # selected landmark or an unknown page must retain the original stop.
            if not any(clean(n.get('name')) == '填写地标名称' for n in nodes):
                raise
            self.report('网站尚未回填地标，重新选择同一条一次：' + name)
            self.engine.open_selector(nodes, THEME)
            selected = self.choose_nearby(used, expected=name)
            return selected, self.wait_form(selected)

    def choose_nearby(self, used, *, expected=None):
        excluded = {clean(name) for name in used}
        previous, unchanged = None, 0
        point, entry = self.resume_point('附近地标')
        if point is not None:
            self.report('续跑记忆：矢志之星 上次停在“' + entry['object'] + '”（' + describe_time(entry) +
                        '）；本次从它之后继续，断点之前的条目不再重复检查。')
        order, retried, reported_skip = [], False, False
        self.nearby_index = None
        for _ in range(150):
            nodes = self.engine.selector_visible_nodes(self.nodes())
            actions = self.engine.unique_click_targets([n for n in nodes if clean(n.get('name')) == '去打卡'
                                                       and n.get('enabled', True)])
            labels = []
            for action in sorted(actions, key=lambda n:(n['y'], n['x'])):
                name = self.nearby_row_name(nodes, action)
                # OCR can read the location icon at a row's left edge as punctuation.
                # Keep internal punctuation (such as “一·二九”) unchanged.
                name = str(name).lstrip(' \t\r\n；;：:，,。·•')
                if not name:
                    continue
                labels.append(clean(name))
                key = clean(name)
                if key not in order:
                    order.append(key)
                    if point is not None and point.active and point.before(name):
                        if point.done and not reported_skip:
                            reported_skip = True
                            if point.skipped:
                                self.report('续跑记忆：已跳过断点及之前的 ' + str(point.skipped) +
                                            ' 个地标，从下一个未完成地标继续。')
                        continue
                elif point is not None and point.active:
                    continue
                if (not self.skip_list_landmark(name) and
                        not any(similar_landmark(name, old) for old in excluded) and
                        (expected is None or clean(name) == clean(expected))):
                    self.nearby_index = len(order) - 1
                    self.report('选择附近未完成地标：' + name)
                    self.desktop.click(action, allow_protected=True)
                    return name
            signature = tuple(labels)
            unchanged = unchanged + 1 if signature == previous else 0
            previous = signature
            if unchanged >= 3:
                if point is not None and not point.done and not retried:
                    self.report('续跑记忆中的地标不在当前列表，已清除这条记忆并回到列表开头按常规查重继续。')
                    self.forget_position('附近地标')
                    point, retried = None, True
                    previous, unchanged = None, 0
                    order.clear()
                    try:
                        self.engine.rewind_selector(nodes)
                    except NeedUser:
                        self.report('未能把附近地标列表滚回开头，按当前位置继续查找。')
                    continue
                if point is not None and point.done:
                    self.report('续跑记忆：断点之后已经没有未完成地标，本次已扫到列表末尾，'
                                '重置记忆后下次从列表开头查找。')
                    self.forget_position('附近地标')
                raise NeedUser('当前可访问的附近地标已检查，没有未完成条目；记录保留。')
            more = [n for n in nodes if clean(n.get('name')) == '点击加载更多']
            if more:
                self.desktop.click(self.unique_label_target(more))
            else:
                self.engine.scroll_selector(nodes)
            self.desktop.pause(1)
        raise NeedUser('附近地标列表扫描达到安全上限，记录保留。')

    def resume_point(self, section=''):
        memory = getattr(self, 'resume', None)
        if memory is None:
            return None, None
        entry = memory.entry(THEME, section)
        if not entry:
            return None, None
        return ResumePoint(entry, similar_landmark), entry

    def forget_position(self, section=''):
        memory = getattr(self, 'resume', None)
        if memory is not None:
            memory.forget(THEME, section)

    def remember_position(self, name):
        memory = getattr(self, 'resume', None)
        hint = getattr(self, 'nearby_index', None)
        if memory is not None and isinstance(hint, int):
            memory.remember(THEME, name, hint, section='附近地标')

    def nearby_row_name(self, nodes, action):
        # The address is below the button's top; never promote a longer address
        # over the landmark title when both fall inside the row tolerance.
        titles = [n for n in nodes if n.get('y', 0) <= action['y'] + action.get('height', 0) * .25]
        semantic = [n for n in titles if n.get('id') != 'ocr']
        return (self.engine.row_label_for_action(semantic, action) or
                self.engine.row_label_for_action(titles, action))

    @staticmethod
    def headings(nodes, names):
        result = []
        for node in nodes:
            label = clean(node.get('name'))
            if label in names and node.get('height', 0) > 0:
                if not any(existing[0] == label for existing in result):
                    result.append((label, node))
        return sorted(result, key=lambda item: item[1].get('y', 0))

    def card_titles(self, nodes, section, lower=None):
        headers = self.headings(nodes, ('热门地标', '热门路线', '最新活动'))
        selected = next((node for label, node in headers if label == section), None)
        if selected is None:
            return []
        upper = next((node.get('y', 10**9) for label, node in headers
                      if node.get('y', 0) > selected.get('y', 0)), 10**9)
        columns = {}
        blocked = ('热门地标', '热门路线', '最新活动', '已收录', '全部', '推荐', '关注',
                   '同济文化元素', '红色文化传承', '国家战略需求', '科学精神实践')
        for node in sorted(nodes, key=lambda item: (item.get('y', 0), item.get('x', 0))):
            if not self.visible(node):
                continue
            label = clean(node.get('name'))
            y, x = node.get('y', 0), node.get('x', 0)
            if y <= selected.get('y', 0) + 130 or y >= upper or (lower is not None and y < lower):
                continue
            if not 3 <= len(label) <= 64 or any(word in label for word in blocked):
                continue
            if re.fullmatch(r'[\d.]+(?:km|个地标)?', label, re.I):
                continue
            if (re.search(r'(?:路|街|道|巷)\d+号', label) or
                    any(token in label for token in ('已收录', '个地标', '公里'))):
                continue
            columns.setdefault(round(x / 220), []).append((label, node))
        titles = []
        for entries in columns.values():
            entries.sort(key=lambda item: item[1].get('y', 0))
            titles.append(entries[0])
        return sorted(titles, key=lambda item: (item[1].get('x', 0), item[1].get('y', 0)))

    @staticmethod
    def is_list_url(url):
        from urllib.parse import urlsplit
        return urlsplit(url).path in LIST_PATHS

    def visible(self, node):
        if node.get('width', 0) <= 0 or node.get('height', 0) <= 0:
            return False
        if not hasattr(getattr(self, 'desktop', None), 'window_bounds'):
            return True
        left, top, right, bottom = self.desktop.window_bounds()
        x, y = node['x'] + node['width']/2, node['y'] + node['height']/2
        return left <= x < right and top + 90 <= y < bottom - 8

    def carousel_bar(self, nodes, section):
        headers = self.headings(nodes, ('热门地标', '热门路线', '最新活动'))
        header = next((node for name, node in headers if name == section), None)
        if not header:
            raise NeedUser('无法识别“' + section + '”区块，已暂停。')
        titles = self.card_titles(nodes, section)
        top = max((node['y'] + node['height'] for _, node in titles), default=header['y']+130)
        bottom = next((node['y'] for name, node in headers if node['y'] > header['y']),
                      self.desktop.window_bounds()[3]-8 if hasattr(self.desktop, 'window_bounds') else top+300)
        if hasattr(self.desktop, 'horizontal_scrollbar'):
            return self.desktop.horizontal_scrollbar(top=top, bottom=bottom)
        return None

    def scan_carousel(self, section):
        # Keep moving the actual horizontal carousel until its visible card set stops changing.
        all_titles, prior = {}, None
        stable = 0
        for _ in range(24):
            nodes = self.nodes()
            titles = self.card_titles(nodes, section)
            fingerprint = tuple((name, round(node.get('x', 0) / 180)) for name, node in titles)
            for name, node in titles:
                all_titles.setdefault(name, node)
            self.report(f'扫描{section}：已发现 {len(all_titles)} 项，正在横向浏览')
            if fingerprint == prior:
                stable += 1
            else:
                stable = 0
            if stable >= 2:
                break
            prior = fingerprint
            header = next((node for label, node in self.headings(nodes, ('热门地标', '热门路线'))
                           if label == section), None)
            if not header:
                raise NeedUser('未能识别首页的“' + section + '”区块，已停止。')
            ys = [node.get('y', header['y'] + 100) for _, node in titles]
            scroll_y = int((min(ys) + max(ys)) / 2) if ys else int(header['y'] + 180)
            self.desktop.scroll(horizontal=True, x=int(header.get('x', 300) + 500),
                                y=scroll_y, amount=500)
        # Return to the first cards before processing the collected list.
        previous = None
        for _ in range(24):
            nodes = self.nodes()
            header = next((node for label, node in self.headings(nodes, ('热门地标', '热门路线'))
                           if label == section), None)
            if not header:
                break
            current = tuple((name, round(node.get('x', 0)))
                            for name, node in self.card_titles(nodes, section))
            if previous == current:
                break
            previous = current
            self.desktop.scroll(x=int(header.get('x', 300) + 500),
                                y=int(header['y'] + 180), amount=500, horizontal=True,
                                down=False)
        if not all_titles:
            raise NeedUser('首页“' + section + '”没有识别到地标卡片，已暂停避免漏扫。')
        return list(all_titles)

    @staticmethod
    def unique_label_target(matches):
        """Collapse duplicate accessibility layers, never separate same-name cards."""
        matches = [node for node in matches if node.get('width', 0) > 0
                   and node.get('height', 0) > 0 and node.get('enabled', True)]
        matches.sort(key=lambda node: node['width'] * node['height'])
        targets = []
        for node in matches:
            left, top = node['x'], node['y']
            right, bottom = left + node['width'], top + node['height']
            duplicate = False
            for smaller in targets:
                x = smaller['x'] + smaller['width'] / 2
                y = smaller['y'] + smaller['height'] / 2
                # A container for an already selected text node is the same
                # target. Distinct spatial occurrences remain ambiguous.
                if left - 2 <= x <= right + 2 and top - 2 <= y <= bottom + 2:
                    duplicate = True
                    break
            if not duplicate:
                targets.append(node)
        if len(targets) != 1:
            raise NeedUser(f'识别到 {len(targets)} 个不同位置的同名目标，无法安全选择。')
        return targets[0]

    def card_image_target(self, nodes, title, section):
        header = next((node for name, node in self.headings(nodes, (section,))
                       if name == section), None)
        if header is None:
            raise NeedUser('无法确认卡片所在区块，停止点击。')
        top = header['y'] + header['height']
        bottom = title['y']
        if bottom <= top:
            raise NeedUser('卡片图片区域不可见，请返回首页后重试。')
        # The site's title is inert; navigation is attached to the picture.
        # Both carousel layouts place the image between the heading and title.
        # Derive the point from this fresh snapshot, independent of DPI/zoom.
        return dict(name=title.get('name', ''), kind='Image', enabled=True,
                    x=title['x'] + title['width'] / 2, y=(top + bottom) / 2,
                    width=1, height=1)

    def click_label(self, label, section=None, nodes=None):
        nodes = self.nodes() if nodes is None else nodes
        matches = [node for node in nodes if clean(node.get('name')) == clean(label)
                   and node.get('kind') in ('Text', 'Hyperlink', 'ListItem', 'Button', 'Custom')]
        if section:
            titles = dict(self.card_titles(nodes, section))
            anchor = titles.get(label)
            if anchor:
                matches = [node for node in matches if abs(node.get('x', 0) - anchor.get('x', 0)) < 200
                           and abs(node.get('y', 0) - anchor.get('y', 0)) < 120]
        try:
            target = self.unique_label_target(matches)
        except NeedUser as error:
            raise NeedUser('页面中无法唯一识别“' + label + '”：' + str(error)) from None
        self.report('正在打开：' + label)
        if section:
            target = self.card_image_target(nodes, target, section)
        elif self.is_list_url(self.page_url(nodes)):
            target = self.list_image_target(nodes, target)
        self.desktop.click(target)
        return self.wait_url_change(self.page_url(nodes))

    def wait_url_change(self, before):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self.desktop.pause(.25)
            nodes = self.nodes()
            url = self.require_site(nodes)
            if url != before:
                return nodes, url
        raise NeedUser('点击列表项后页面没有打开详情，请核对网络和页面状态。')

    def process_carousel(self, section, home_url, initial_nodes=None):
        processed = set()
        stable, scrolls = 0, 0
        initial_nodes = self.rewind_carousel(section, initial_nodes)
        while True:
            self.check()
            nodes = self.nodes() if initial_nodes is None else initial_nodes
            initial_nodes = None
            titles = self.card_titles(nodes, section)
            pending = [(title, node) for title, node in titles if title not in processed]
            if pending:
                title, _ = pending[0]
                if section == '热门地标' and self.skip_list_landmark(title):
                    processed.add(title)
                    initial_nodes = nodes
                    continue
                self.report(f'{section}第 {len(processed) + 1} 项：{title}')
                detail_nodes, detail_url = self.click_label(title, section, nodes=nodes)
                if section == '热门地标':
                    self.process_detail(detail_nodes, detail_url, title, section)
                else:
                    self.process_list(detail_url, title)
                processed.add(title)
                initial_nodes = self.back_to(home_url)
                stable = 0
                continue
            fingerprint = tuple((name, round(node.get('x', 0))) for name, node in titles)
            if scrolls >= 64:
                raise CarouselIncomplete('横向浏览次数过多但未确认到达末尾，未确认完整。')
            header = next((node for name, node in self.headings(nodes, (section,))
                           if name == section), None)
            if not header:
                raise NeedUser('无法识别“' + section + '”区块，已暂停。')
            anchor = titles[0][1] if titles else None
            point = self.card_image_target(nodes, anchor, section) if anchor else dict(
                x=header['x'] + 500, y=header['y'] + 180)
            self.report(f'{section}：已处理 {len(processed)} 项，向右查看下一批')
            bar = self.carousel_bar(nodes, section)
            if bar:
                width = bar['thumb_right'] - bar['thumb_left']
                center = (bar['thumb_left'] + bar['thumb_right']) / 2
                # Move by less than a thumb width: adjacent views overlap.
                destination = min(bar['right'] - 3, center + max(35, width * .7))
                self.desktop.drag_pointer((center, bar['y']), (destination, bar['y']))
            else:
                self.desktop.scroll(horizontal=True, x=int(point['x']),
                                    y=int(point['y']), amount=500)
            scrolls += 1
            after = self.nodes()
            after_titles = self.card_titles(after, section)
            current = tuple((name, round(node.get('x', 0))) for name, node in after_titles)
            end_bar = self.carousel_bar(after, section)
            moved = bool(bar and end_bar and abs(end_bar['thumb_left'] - bar['thumb_left']) > 2)
            at_end = bool(end_bar and end_bar['right'] - end_bar['thumb_right'] <= 30)
            if current == fingerprint and not moved:
                stable += 1
                if stable >= 2:
                    if not at_end or not processed:
                        raise CarouselIncomplete('横向滚动未生效或无法确认滑块到达末尾；未把当前数量当作全部。')
                    self.report(f'{section}滑块已到最右端，共处理 {len(processed)} 项。')
                    return after
            else:
                stable = 0
            initial_nodes = after

    def rewind_carousel(self, section, initial_nodes=None):
        nodes = self.nodes() if initial_nodes is None else initial_nodes
        for _ in range(4):
            bar = self.carousel_bar(nodes, section)
            if not bar:
                raise CarouselIncomplete('无法识别“' + section + '”的横向滑块，未确认完整。')
            if bar['thumb_left'] - bar['left'] <= 40:
                return nodes
            self.report('将“' + section + '”滑块归到最左端……')
            center = (bar['thumb_left']+bar['thumb_right'])/2
            self.desktop.drag_pointer((center, bar['y']), (bar['left']+3, bar['y']))
            nodes = self.nodes()
        raise CarouselIncomplete('滑块未能归到最左端，未确认完整。')

    def back_to(self, target):
        target = target.split('#', 1)[0]
        for _ in range(8):
            nodes = self.nodes()
            url = self.require_site(nodes)
            if url.split('#', 1)[0] == target:
                return nodes
            self.desktop.back()
        raise NeedUser('无法返回当前列表，已停止，页面状态请手动核对。')

    def detail_id(self, url):
        if DETAIL_PATH not in url:
            raise NeedUser('列表项没有打开预期的地标详情页，已停止。')
        match = re.search(r'(?:\?|&)id=(\d+)', url)
        if not match:
            raise NeedUser('详情页没有可识别的地标编号，已停止以免重复。')
        return match.group(1)

    def detail_name(self, nodes, fallback):
        candidates = [clean(node.get('name')) for node in nodes
                      if clean(node.get('name')) and node.get('y', 0) > 0]
        ui = {'矢志之星', '地标详情', '去打卡', '可打卡', '超出打卡范围', '定位失败，请刷新重试',
              '返回', '地点介绍', '相关活动'}
        for value in candidates:
            if value == clean(fallback):
                return clean(fallback)
        meaningful = [value for value in candidates if value not in ui and len(value) >= 3
                      and not value.startswith(('热门', '已收录', '今日', '同济文化元素'))]
        return meaningful[-1] if meaningful else clean(fallback)

    def page_description(self, nodes, title):
        if FORM_PATH in self.page_url(nodes):
            # The module form contains no landmark introduction. Browser tabs,
            # notices and OCR overlay text must not become reflection material.
            return ''
        pieces = []
        for node in nodes:
            value = str(node.get('name', '')).strip()
            normalized = clean(value)
            if (len(normalized) >= 18 and normalized != clean(title) and
                    not any(word in normalized for word in ('填写地标', '填写感悟', '去选择', '上传图片',
                                                            '确认发布', '定位失败', '超出打卡', '已收录'))):
                pieces.append(value)
        return '\n'.join(dict.fromkeys(pieces))[:5000]

    def location_result(self, nodes):
        labels = [clean(node.get('name')) for node in nodes]
        if any('超出打卡范围' in value or '超出范围' == value for value in labels):
            return '超范围'
        if any('定位失败' in value or '暂时无法获取定位' in value for value in labels):
            return '定位失败'
        if any('定位中' in value or '正在定位' in value for value in labels):
            return '定位中'
        if any(clean(node.get('name')) in ('去打卡', '可打卡')
               and node.get('enabled', True) for node in nodes):
            return '可打卡'
        if any(value in ('去打卡', '可打卡') for value in labels):
            return '定位中'
        return '未识别'

    def wait_for_location(self, expected_id):
        last = []
        wait_seconds = max(10, min(300, float(self.config.get('location_wait_seconds', 90))))
        for attempt in range(2):
            started = time.monotonic()
            deadline = started + wait_seconds
            reported_at = -10
            ready_reads = 0
            while time.monotonic() < deadline:
                self.check()
                last = self.nodes()
                current_url = self.require_site(last)
                if DETAIL_PATH not in current_url or self.detail_id(current_url) != expected_id:
                    raise NeedUser('详情页在定位过程中发生变化，已暂停。')
                result = self.location_result(last)
                if result in ('超范围', '定位失败'):
                    return result, last
                ready_reads = ready_reads + 1 if result == '可打卡' else 0
                if ready_reads >= 2:
                    self.report('底部打卡入口已可用，准备点击；范围限制继续以网站反馈为准。')
                    return '可打卡', last
                elapsed = int(time.monotonic() - started)
                if elapsed - reported_at >= 5:
                    self.report(f'等待系统定位 {elapsed}/{int(wait_seconds)} 秒（第 {attempt + 1}/2 轮），底部状态：{result}')
                    reported_at = elapsed
                self.desktop.pause(1)
            if attempt == 0:
                self.report('系统定位尚未完成，刷新一次后继续等待；不会把未完成定位记作超范围。')
                self.desktop.refresh()
        raise NeedUser('定位等待并刷新后仍无结果；批次暂停，没有把它记作超范围。')

    def safe_cleanup(self, row_id):
        if not re.fullmatch(r'[0-9a-f]{32}', row_id):
            raise RuntimeError('图片临时目录编号无效，停止清理。')
        if self.temp_root.is_symlink():
            raise RuntimeError('矢志图片临时目录是符号链接，停止清理以保护目录外文件。')
        root = self.temp_root.resolve()
        photo_root = self.temp_root / '照片'
        if photo_root.is_symlink():
            raise RuntimeError('图片临时目录是符号链接，停止清理以保护目录外文件。')
        photo_base = photo_root.resolve()
        for folder in (photo_root / row_id, self.temp_root / row_id):
            if folder.is_symlink():
                raise RuntimeError('图片临时目录是符号链接，停止清理以保护目录外文件。')
            resolved = folder.resolve()
            if resolved.parent not in (root, photo_base):
                raise RuntimeError('图片临时目录超出矢志数据目录，停止清理。')
            if folder.exists():
                shutil.rmtree(folder)
        if photo_root.exists() and not any(photo_root.iterdir()):
            photo_root.rmdir()

    def clear_stale_images(self):
        if self.temp_root.is_symlink():
            raise RuntimeError('矢志图片临时目录是符号链接，已停止。')
        self.temp_root.mkdir(parents=True, exist_ok=True)
        photo_root = self.temp_root / '照片'
        if photo_root.is_symlink():
            raise RuntimeError('矢志图片目录是符号链接，已停止。')
        pending = {row['id'] for row in self.history.rows
                   if row.get('status') == '待提交' and row.get('image')}
        stale = {child.name for folder in (self.temp_root, photo_root) if folder.exists()
                 for child in folder.iterdir() if re.fullmatch(r'[0-9a-f]{32}', child.name)}
        for row_id in stale - pending:
            self.safe_cleanup(row_id)

    @staticmethod
    def requires_verification(row):
        return (row.get('theme') == THEME and row.get('status') in ('待提交', '结果待核实') and
                (row.get('status') == '结果待核实' or row.get('phase') in
                 ('正在提交，等待网站结果', '结果待核实', '提交结果待核实')))

    def skip_list_landmark(self, title):
        """Consult persisted results before navigating away from a list."""
        history = getattr(self, 'history', None)
        if history is not None:
            history.reload()
        for row in history.rows if history is not None else ():
            if row.get('theme') != THEME or not (row.get('status') == '已完成' or self.requires_verification(row)):
                continue
            previous = (row.get('fields') or {}).get('title', '')
            if not previous:
                previous = str(row.get('object', '')).removeprefix('名称:')
                if previous.isdecimal():
                    continue
            if similar_landmark(previous, title):
                status = '已完成' if row.get('status') == '已完成' else '结果待核实'
                self.report(f'列表查重，{status}，直接跳过：{title}（匹配记录：{previous}）')
                return True
        return False

    def process_detail(self, nodes, url, expected_name, route, *, form_ready=False):
        title = clean(expected_name) if form_ready else self.detail_name(nodes, expected_name)
        identifier = '名称:' + title if form_ready else self.detail_id(url)
        matches = lambda item: item.get('theme') == THEME and (item.get('object') == identifier or
                    clean((item.get('fields') or {}).get('title')) == title)
        if any(matches(item) and self.requires_verification(item)
               for item in self.history.rows):
            self.report('仅跳过提交结果待核实的地标，继续其他条目：' + title)
            return '待核实'
        known = next((row for row in self.history.rows if matches(row) and row.get('status') == '已完成'), None)
        if known:
            self.report('已记录完成，跳过：' + title)
            return '完成'
        if identifier in self.seen_this_run:
            self.report('本次运行已核查过，跳过重复入口：' + title)
            return '本次已核查'
        self.seen_this_run.add(identifier)
        row = next((item for item in reversed(self.history.rows) if matches(item) and item.get('status') == '待提交'), None)
        self.report('正在核查位置和打卡范围：' + title)
        outcome, nodes = ('可打卡', nodes) if form_ready else self.wait_for_location(identifier)
        if outcome == '超范围':
            if row is None:
                row = self.history.reserve(THEME, identifier, {'title': title, 'route': route},
                                           independent_objects=True)
            self.history.update(row['id'], status='超出范围', phase='网站明确显示超出打卡范围')
            self.report('超出打卡范围，记入 TXT 后尝试下一个：' + title)
            return '超范围'
        if outcome == '定位失败':
            raise NeedUser('卓越星报告定位失败；已暂停，请检查浏览器定位权限后重试。')
        if outcome != '可打卡':
            raise NeedUser('详情页没有出现明确的范围结果，已暂停：' + title)
        fields = {'title': title, 'route': route, 'landmark_id': identifier,
                  'address': self.page_address(nodes), 'introduction': self.page_description(nodes, title),
                  'minimum_chars': MINIMUM_CHARS}
        if row is None:
            row = self.history.reserve(THEME, identifier, fields, independent_objects=True)
        else:
            fields.update(row.get('fields') or {})
            row = self.history.update(row['id'], fields=fields, phase='恢复待提交地标')
        self.report('打卡入口已可用，准备进入表单：' + title)
        try:
            buttons = [node for node in nodes if clean(node.get('name')) in ('去打卡', '可打卡')
                       and node.get('enabled', True)]
            if not form_ready:
                try:
                    button = self.unique_label_target(buttons)
                except NeedUser:
                    raise NeedUser('底部打卡入口无法唯一识别，已停止。') from None
                self.report('正在点击底部“' + clean(button.get('name')) + '”，打开填写表单。')
                self.desktop.click(button, allow_protected=True)
            nodes = self.wait_form(title)
            api = DeepSeek(load_key(self.source_root),
                           self.config.get('model', 'deepseek-flash'), self.cancel)
            if not row.get('text'):
                row = self.history.update(row['id'], text=api.reflection(THEME, row['fields'], MINIMUM_CHARS),
                                          phase='正文已生成')
            finder = ImageFinder(api, self.temp_root, self.cancel, self.report,
                                 self.config.get('allow_fake_ip_dns', False))
            photo_dir = self.temp_root / '照片' / row['id']
            image = str(photo_dir / '上传配图.png')
            if not row.get('image') or not finder.ready(THEME, title, row['id'], image):
                image = finder.choose(THEME, title, row['id'])
            proof_path = photo_dir / '配图选择.json'
            try:
                proof = json.loads(proof_path.read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                proof = {}
            if (proof.get('selection') not in ('approved', 'best_after_limit') or
                    not finder.ready(THEME, title, row['id'], image)):
                raise ImageSelectionRequired('配图未通过完整审查，不能用于自动发布。')
            row = self.history.update(row['id'], image=image, phase='配图通过完整审查')
            self.engine.api = api
            self.engine.fill(row)
            nodes = self.nodes()
            self.verify_filled(title, row, nodes)
            result = self.submit_once(nodes, row)
            if result == 'already':
                self.report('网站提示此前已打卡，记入 TXT 并尝试下一个：' + title)
                self.history.update(row['id'], status='已完成', image='',
                                    phase='网站提示此前已打卡，已防止重复提交')
            else:
                self.report('网站确认发布成功：' + title)
                self.history.update(row['id'], status='已完成',
                                    confirmed_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                    image='', phase='网站确认发布成功')
            return '完成'
        except DailyLimit:
            self.history.discard_pending(row['id'])
            self.report('今日次数已用完，已移除当前未完成记录：' + title)
            raise
        except OutOfRange:
            self.history.update(row['id'], status='超出范围', image='',
                                phase='点击入口后网站明确提示超出打卡范围')
            self.report('网站提示超出打卡范围，尝试下一个：' + title)
            return '超范围'
        except ImageSearchUnavailable as error:
            self.history.update(row['id'], phase='搜图暂不可用，保留正文等待继续', image='')
            raise NeedUser(str(error)) from error
        except ImageSelectionRequired as error:
            self.history.update(row['id'], status='已跳过', image='',
                                phase='配图未通过完整审查，已跳过：' + str(error))
            self.report('没有符合自动发布标准的配图，已记录并跳过：' + title)
            return '已跳过'
        except Exception:
            current = next((item for item in self.history.rows if item['id'] == row['id']), row)
            if current.get('status') == '待提交' and current.get('phase') in (
                    '正在提交，等待网站结果', '结果待核实', '提交结果待核实'):
                self.history.update(row['id'], phase='结果待核实')
            raise
        finally:
            current = next((item for item in self.history.rows if item['id'] == row['id']), None)
            if not current or current.get('status') != '待提交' or not current.get('image'):
                self.safe_cleanup(row['id'])

    @staticmethod
    def page_address(nodes):
        for node in nodes:
            value = str(node.get('name', '')).strip()
            if any(fragment in value for fragment in ('上海市', '杨浦区', '浦东新区', '嘉定区', '四平路')):
                return value[:300]
        return ''

    def wait_form(self, title):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            nodes = self.nodes()
            url = self.require_site(nodes)
            if self.location_result(nodes) == '超范围':
                raise OutOfRange('网站提示超出打卡范围。')
            form_controls = (find(nodes, '填写感悟、寄语等') or
                             (find(nodes,'确认发布') and find(nodes,'上传图片') and
                              any(n.get('kind') == 'Edit' and '://' not in
                                  Engine.field_content(n,()) for n in nodes)))
            if '/app/pages-daka/shizhi' in url and form_controls:
                if self.selected_title_nodes(title, nodes):
                    return nodes
                self.report('等待网站回填所选地标名称：' + title)
            self.desktop.pause(.4)
        detected = [Engine.field_content(n, ())[:100] for n in nodes
                    if n.get('kind') == 'Edit' and Engine.field_content(n, ())
                    and '://' not in Engine.field_content(n, ())]
        raise NeedUser('等待地标表单超时；预期：' + title + '；读取字段：' +
                       '、'.join(dict.fromkeys(detected)) + '；感悟框标记：' +
                       str(bool(find(nodes, '填写感悟、寄语等'))))

    @staticmethod
    def selected_title_nodes(title, nodes):
        if any(clean(n.get('name')) in ('去打卡', '输入地标关键词') for n in nodes):
            return []
        selectors = [n for n in nodes if clean(n.get('name')) == '去选择']
        return [n for n in nodes if clean(Engine.field_content(n, ())) == clean(title)
                and (n.get('kind') == 'Edit' or (n.get('kind') in ('Text','Custom') and
                     any(abs(n.get('y',0)-s.get('y',0)) <= max(40,s.get('height',0)*2)
                         for s in selectors)))]

    def verify_filled(self, title, row, nodes):
        url = self.require_site(nodes)
        if '/app/pages-daka/shizhi' not in url:
            raise NeedUser('填写后离开矢志之星表单，已停止发布。')
        self.verify_form_fields(title, row, nodes)
        upload_label = find(nodes, '上传图片') or find(nodes, '上传封面图')
        thumbnails = [node for node in nodes if node.get('kind') == 'Image'
                      and node.get('id') == 'msaa' and upload_label
                      and abs(node.get('y', 0) - upload_label.get('y', 0)) <=
                      max(100, upload_label.get('height', 0) * 6)]
        from .thumbnail import matches_uploaded_photo
        if not thumbnails and not matches_uploaded_photo(self.desktop, nodes, row.get('image')):
            raise NeedUser('页面没有可核验的配图缩略图，已停止发布。')
        if not any(clean(node.get('name')) == '确认发布' for node in nodes):
            raise NeedUser('未找到“确认发布”按钮，已停止。')

    def verify_form_fields(self, title, row, nodes):
        # UIA and MSAA can both expose the same input. MSAA may put the
        # entered text in Name while leaving Value empty, as fill_field does.
        fields = [node for node in nodes if node.get('kind') == 'Edit']
        title_fields = self.selected_title_nodes(title, nodes)
        expected = str(row.get('text') or '')
        if len(expected) < MINIMUM_CHARS:
            raise NeedUser('提交前核验：准备的正文不足最低字数，已停止。')
        text_fields = [node for node in fields
                       if clean(Engine.field_content(node, ())) == clean(expected)]
        if not title_fields:
            raise NeedUser('提交前核验：读取的地标名称与当前条目不一致，已停止。')
        if not text_fields:
            raise NeedUser('提交前核验：读取的正文与本条已填写正文不一致，已停止。')
        self.unique_label_target(title_fields)
        self.unique_label_target(text_fields)
        if any('上传失败' in clean(node.get('name')) for node in nodes):
            raise NeedUser('提交前核验：网站报告图片上传失败，已停止。')

    def submit_once(self, nodes, row):
        for attempt in range(1, 4):
            if attempt > 1:
                self.desktop.pause(2)
                nodes = self.nodes()
                self.verify_filled(row['fields']['title'], row, nodes)
            try:
                return self.submit_attempt(nodes, row)
            except SubmitRejected as error:
                self.history.update(row['id'], phase='发布明确失败，可继续重试')
                if attempt == 3:
                    raise NeedUser('连续3次发布明确失败，请检查页面后点击“继续”：' + str(error)) from error
                self.report(f'网站明确提示发布失败，准备重试（{attempt + 1}/3）：{error}')

    def submit_attempt(self, nodes, row):
        buttons = [node for node in nodes if clean(node.get('name')) == '确认发布'
                   and node.get('enabled', True)]
        button = buttons[0] if len(buttons) == 1 else self.unique_label_target(buttons)
        from .submit_watch import SubmitWatch
        observer = SubmitWatch(self.desktop) if isinstance(self.desktop, Desktop) else None
        try:
            self.history.update(row['id'], phase='正在提交，等待网站结果')
            self.report('表单与图片核验通过，正在自动提交……')
            self.desktop.click(button, allow_protected=True)
            return self.wait_submit_result(row, observer)
        finally:
            if observer:
                observer.close()

    @staticmethod
    def submit_result(labels):
        if ShizhiBatch.quota_message(labels):
            return 'limit'
        if any(any(word in label for word in ('已打卡', '已经打卡', '不能重复提交',
                                              '请勿重复提交', '请勿重复打卡')) for label in labels):
            return 'already'
        if any(any(word in label for word in ('提交成功', '发布成功', '打卡成功')) for label in labels):
            return 'success'
        if any(any(word in label for word in ('次数已用完', '今日次数不足', '登录已过期',
                                              '请重新登录', '提交失败', '发布失败', '打卡失败'))
               for label in labels):
            return 'error'
        return None

    def wait_submit_result(self, row, observer=None):
        if observer:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                self.desktop.pause(.1)
                message = observer.result()
                result = self.submit_result([clean(message)])
                if result == 'limit':
                    raise DailyLimit(self.quota_message([message]))
                if result == 'already':
                    self.report('网站提示：' + message)
                    return 'already'
                if result == 'success':
                    self.report('网站提示：' + message)
                    return
                if result == 'error':
                    if any(word in clean(message) for word in ('提交失败', '发布失败', '打卡失败')) and not any(
                            word in clean(message) for word in ('登录', '次数', '重新登录')):
                        raise SubmitRejected(message)
                    self.history.update(row['id'], phase='提交结果待核实')
                    raise NeedUser('网站提示：' + message)
            # A persistent quota/result page may be outside the toast crop.
            nodes = self.nodes()
            result = self.submit_result([clean(node.get('name')) for node in nodes])
            if result == 'success':
                return
            if result == 'already':
                return 'already'
            self.history.update(row['id'], phase='结果待核实')
            raise NeedUser('45秒内没有确认提交结果；本条保留待核实，点击“继续”可扫描其他地标。')
        for _ in range(40):
            self.desktop.pause(.25)
            nodes = self.nodes()
            labels = [clean(node.get('name')) for node in nodes]
            result = self.submit_result(labels)
            if result == 'success':
                return
            if result == 'already':
                return 'already'
            if result == 'error':
                message = '；'.join(labels)
                if any(word in message for word in ('提交失败', '发布失败', '打卡失败')) and not any(
                        word in message for word in ('登录已过期', '次数', '请重新登录')):
                    raise SubmitRejected(message)
                self.history.update(row['id'], phase='提交结果待核实')
                raise NeedUser('页面报告次数耗尽或登录状态变化，结果待核实；已暂停批次。')
        self.history.update(row['id'], phase='结果待核实')
        raise NeedUser('提交后未能确认网站结果，记录为待核实并暂停；恢复时不会自动再次提交。')

    def handle_detail(self, expected_name, route, source_url):
        nodes, url = self.wait_url_change(source_url)
        identifier = self.detail_id(url)
        self.process_detail(nodes, url, expected_name, route)
        return self.back_to(source_url)

    def process_list(self, source_url, route):
        attempted = set()
        unchanged, scrolls, loads = 0, 0, 0
        initial = self.rewind_list(source_url)
        while True:
            self.check()
            nodes = self.nodes() if initial is None else initial
            initial = None
            url = self.require_site(nodes)
            if not self.is_list_url(url) or url != source_url:
                raise NeedUser('热门路线没有打开地标列表，已停止。')
            titles = self.list_titles(nodes, route)
            if not titles:
                self.desktop.pause(.8)
                nodes = self.nodes()
                titles = self.list_titles(nodes, route)
            fingerprint = self.list_fingerprint(nodes, route)
            candidate = next((name for name in titles if name not in attempted), None)
            if candidate:
                if self.skip_list_landmark(candidate):
                    attempted.add(candidate)
                    initial = nodes
                    continue
                self.report(f'路线“{route}”正在核查：{candidate}')
                detail_nodes, detail_url = self.click_label(candidate, nodes=nodes)
                self.process_detail(detail_nodes, detail_url, candidate, route)
                attempted.add(candidate)
                initial = self.back_to(url)
                unchanged = 0
                continue
            more = [node for node in nodes if clean(node.get('name')) == '点击加载更多' and self.visible(node)]
            if more:
                if loads >= 100:
                    raise NeedUser('路线加载页数超过上限，已暂停，未宣告扫描完成。')
                self.report(f'路线“{route}”：已核查 {len(attempted)} 项，加载更多地标')
                self.desktop.click(self.unique_label_target(more))
                loads += 1
                self.desktop.pause(.8)
                initial = self.wait_list_loading(source_url)
                if self.list_fingerprint(initial, route) == fingerprint:
                    unchanged += 1
                    if unchanged >= 3:
                        raise NeedUser('点击加载更多后没有新条目，已暂停等待网络；未把它当作末尾。')
                else:
                    unchanged = 0
                continue
            if scrolls >= 400:
                raise NeedUser('路线列表超过安全滚动上限，已暂停，未宣告扫描完成。')
            bar = self.route_bar(nodes)
            self.report(f'路线“{route}”：已核查 {len(attempted)} 项，向下浏览')
            self.desktop.scroll(down=True, amount=420)
            scrolls += 1
            after = self.wait_list_loading(source_url)
            after_bar = self.route_bar(after)
            moved = bool(bar and after_bar and abs(after_bar['thumb_top'] - bar['thumb_top']) > 2)
            unchanged = unchanged + 1 if self.list_fingerprint(after, route) == fingerprint and not moved else 0
            at_end = bool(after_bar and after_bar['bottom'] - after_bar['thumb_bottom'] <= 38)
            explicit_end = any((clean(node.get('name')).startswith('没有更多') or
                                clean(node.get('name')) in ('已加载全部', '暂无更多数据'))
                               and self.visible(node) for node in after)
            if unchanged >= 2:
                if (at_end or explicit_end) and attempted:
                    # Check the final view once more for a visible load button or new title.
                    if any(name not in attempted for name in self.list_titles(after, route)) or any(
                            clean(node.get('name')) == '点击加载更多' and self.visible(node) for node in after):
                        initial = after
                        continue
                    self.report(f'路线“{route}”已到底且没有待加载条目，共核查 {len(attempted)} 项。')
                    return
                raise NeedUser('路线滚动未生效或无法确认已到底，已暂停避免漏扫。')
            initial = after

    def rewind_list(self, source_url):
        nodes = self.wait_list_loading(source_url)
        for _ in range(4):
            bar = self.route_bar(nodes)
            if not bar:
                # No track on a short list: scroll up explicitly, and require
                # an explicit terminal message before claiming completion.
                self.desktop.scroll(down=False, amount=2000)
                return self.wait_list_loading(source_url)
            if bar['thumb_top']-bar['top'] <= 38:
                return nodes
            center = (bar['thumb_top']+bar['thumb_bottom'])/2
            self.desktop.drag_pointer((bar['x'], center), (bar['x'], bar['top']+3))
            nodes = self.wait_list_loading(source_url)
        raise NeedUser('路线列表未能归到顶部，已暂停避免遗漏。')

    def wait_list_loading(self, source_url):
        for _ in range(40):
            nodes = self.nodes()
            if self.require_site(nodes) != source_url:
                raise NeedUser('加载过程中路线页面发生变化，已暂停。')
            if not any(('正在加载' in clean(node.get('name')) or clean(node.get('name')) == '加载中')
                       and self.visible(node) for node in nodes):
                return nodes
            self.desktop.pause(.3)
        raise NeedUser('路线地标仍在加载，已暂停等待网络；未宣告扫描完成。')

    def route_bar(self, nodes):
        top = max((node['y'] + node.get('height', 0) for node in nodes
                   if clean(node.get('name')) == '地标筛选'), default=150)
        return self.desktop.vertical_scrollbar(top=top) if hasattr(self.desktop, 'vertical_scrollbar') else None

    def list_fingerprint(self, nodes, route):
        titles = self.list_titles(nodes, route)
        return tuple((clean(node.get('name')), round(node.get('x', 0)), round(node.get('y', 0)))
                     for node in nodes if clean(node.get('name')) in titles and self.visible(node))

    def list_titles(self, nodes, route=None):
        top = max((node['y'] for node in nodes if clean(node.get('name')) == '地标筛选'), default=0)
        values = []
        cards = set()
        blocked = ('输入地标关键词', '搜索', '推荐地标', '热门路线', '地标筛选', '重选', '已收录', '返回', '全部',
                   '同济文化元素', '红色文化传承', '国家战略需求', '科学精神实践', '超出范围', '去打卡',
                   '点击加载更多', '没有更多', '加载中', '哎呀')
        for node in sorted(nodes, key=lambda item: (item.get('y', 0), item.get('x', 0))):
            label = clean(node.get('name'))
            if (not self.visible(node) or node.get('y', 0) < top + 90 or not 3 <= len(label) <= 64 or
                    label == clean(route) or
                    any(word in label for word in blocked) or
                    re.search(r'(?:路|街|道|巷)\d+号', label) or
                    any(token in label for token in ('已收录', '个地标', '公里', '://'))):
                continue
            if re.fullmatch(r'[\d.]+(?:km|个地标)?', label, re.I):
                continue
            anchor = self.list_card_anchor(nodes, node)
            if anchor:
                key = (round(anchor['x']), round(anchor['y']))
                if key in cards:
                    continue
                cards.add(key)
            if label not in values:
                values.append(label)
        return values

    def list_card_anchor(self, nodes, title):
        center = title['x'] + title['width']/2
        images = [node for node in nodes if node.get('kind') == 'Image'
                  and node.get('width', 0) >= title['width']
                  and node['x'] <= center <= node['x']+node['width']
                  and 0 <= title['y']-(node['y']+node['height']) <= 150]
        # MSAA sometimes exposes only text. The category badge marks the top of
        # this card's photograph, including cards in later rows of the grid.
        badges = [node for node in nodes if clean(node.get('name')) in (
                    '同济文化元素', '红色文化传承', '国家战略需求', '科学精神实践')
                  and abs(node.get('x', 0)-title['x']) < 100
                  and 30 < title['y']-node.get('y', 0) < 600]
        return max(images+badges, key=lambda node: node['y']) if images or badges else None

    def list_image_target(self, nodes, title):
        anchor = self.list_card_anchor(nodes, title)
        if not anchor or not self.visible(anchor):
            raise NeedUser('无法确认“' + title['name'] + '”的卡片图片，已暂停避免误点。')
        if anchor.get('kind') == 'Image':
            return dict(anchor, name=title['name'])
        center = title['x'] + title['width']/2
        return dict(name=title['name'], kind='Image', enabled=True,
                    x=center, y=(anchor['y']+anchor['height']+title['y'])/2, width=1, height=1)

    def ensure_card_visible(self, section, title):
        stable = 0
        previous = None
        for _ in range(24):
            nodes = self.nodes()
            titles = self.card_titles(nodes, section)
            if any(name == title for name, _ in titles):
                return
            fingerprint = tuple((name, round(node.get('x', 0) / 180)) for name, node in titles)
            stable = stable + 1 if fingerprint == previous else 0
            if stable >= 3:
                break
            previous = fingerprint
            header = next((node for label, node in self.headings(nodes, ('热门地标', '热门路线'))
                           if label == section), None)
            if not header:
                break
            self.desktop.scroll(horizontal=True, x=int(header.get('x', 300) + 500),
                                y=int(header['y'] + 180), amount=500)
        raise NeedUser('横向浏览“' + section + '”后仍未找到“' + title + '”，批次暂停。')

    def run(self, start_section='热门地标', *, module_only=False):
        if start_section not in ('热门地标', '热门路线'):
            raise ValueError('起始区块无效。')
        self.clear_stale_images()
        unresolved = [row for row in self.history.rows if self.requires_verification(row)]
        for row in unresolved:
            title = (row.get('fields') or {}).get('title') or row['object']
            self.report('保留待核实记录，仅跳过该地标并继续扫描：' + title + '（' + row['object'] + '）')
        self.desktop.connect_browser()
        home_nodes, home_url = self.go_home()
        from urllib.parse import urlsplit
        if urlsplit(home_url).path in (MODULE_PATH, FORM_PATH):
            return self.run_module(home_nodes)
        if module_only:
            raise NeedUser('请在 Edge 打开五项打卡列表后继续矢志之星。')
        self.report('已识别矢志之星首页：' + home_url)
        landmarks_complete = True
        if start_section == '热门地标':
            self.report('扫描首页热门地标……')
            try:
                home_nodes = self.process_carousel('热门地标', home_url, home_nodes)
            except CarouselIncomplete as error:
                landmarks_complete = False
                self.report('热门地标未确认完整：' + str(error) + ' 继续处理热门路线，保留原记录。')
                home_nodes, home_url = self.go_home()
        self.report('扫描首页热门路线……')
        self.process_carousel('热门路线', home_url, home_nodes)
        if start_section == '热门路线':
            self.report('热门路线已遍历；本次未扫描热门地标。')
        else:
            self.report('热门地标与热门路线已全部遍历。' if landmarks_complete else
                        '热门路线已遍历；热门地标横向浏览未确认完整，可稍后重试。')
        return landmarks_complete

