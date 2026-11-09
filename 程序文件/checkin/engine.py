import json
import re
import time
from pathlib import Path

from .images import ImageFinder, ImageSelectionRequired, ImageSearchUnavailable
from .storage import object_key, same_object_by_keyword, today
from .windows import Desktop, find
from .teachers import teacher_name
from .deepseek import TeacherInformationMissing
from .batch import PAGE_THEMES, THEMES, remaining_counts
from .resume import ResumePoint, describe_time
from .thumbnail import matches_uploaded_photo
from .date_picker import centered_picker_confirm, date_matches, date_picker_open, picker_rows

OBJECT_FIELDS = {
    '弘文之星': ('填写书籍名称', '书籍名称', '书名'),
    '明德之星': ('填写教师/校友姓名', '教师/校友姓名'),
    '求索之星': ('填写讲座名称', '讲座名称'),
    '力行之星': ('填写活动主题', '活动主题'),
}


class NeedUser(RuntimeError):
    pass


class DailyLimit(RuntimeError):
    pass


class MaterialUnavailable(NeedUser):
    """An unsubmitted object has no usable material; the batch may advance."""


class Engine:
    def __init__(self, root, config, history, api, cancel, report, handoff, on_row=None,
                 data_root=None, browser=False, resume=None):
        self.on_row = on_row or (lambda row: None)
        self.root, self.config, self.history, self.api = Path(root), config, history, api
        self.data_root = Path(data_root) if data_root else self.root
        self.cancel, self.report, self.handoff = cancel, report, handoff
        self.resume = resume
        self.position_hint = None
        self.desktop = Desktop(root, cancel, browser=browser)

    def nodes(self):
        self.desktop.pause(.45)
        result = []
        snapshot = self.desktop.snapshot()
        if getattr(self.desktop, 'browser_mode', False) is True:
            from .shizhi import ShizhiBatch
            if not ShizhiBatch.page_url(snapshot):
                raise NeedUser('无法确认当前 Edge 标签是同济卓越星，请切回该标签后继续。')
        for node in snapshot:
            duplicate = next((i for i, old in enumerate(result) if old.get('kind') == node.get('kind')
                and all(abs(old.get(k, 0) - node.get(k, 0)) <= 2 for k in ('x', 'y', 'width', 'height'))), None)
            if duplicate is None:
                result.append(node)
            elif node.get('id') == 'msaa':
                result[duplicate] = node
        return result

    def navigation(self, theme, stop_at_menu=False):
        if theme not in THEMES:
            raise ValueError('请选择弘文、明德、求索或力行；矢志由地点流程处理。')
        self.desktop.connect(self.config.get('wecom_exe', ''))
        entered_workbench = False
        for _ in range(20):
            nodes = self.nodes()
            text = '\n'.join(str(n.get('name', '')) for n in nodes)
            if '统一身份认证' in text or any(n.get('name') in ('登录', '扫码登录') for n in nodes):
                self.handoff('请手动完成统一身份认证登录。完成后点击助手窗口的“继续”。')
                continue
            if not stop_at_menu and (theme + '打卡' in text or (theme in text and any(t in text for t in ('填写', '上传图片')))):
                return nodes
            theme_node = find(nodes, theme)
            home_navigation = find(nodes, '社区') and find(nodes, '排行') and find(nodes, '我的')
            if stop_at_menu and not home_navigation and sum(any(str(n.get('name', '')).startswith(t) and not str(n.get('name', '')).startswith(t+'打卡') for n in nodes) for t in PAGE_THEMES) >= 2:
                return nodes
            if (date_picker_open(nodes) or any(self.is_checkin_form(t, nodes) for t in PAGE_THEMES)
                    or any(n.get('name') == '去打卡' for n in nodes)):
                self.report('从当前表单或选择列表返回主题入口……')
                self.desktop.back()
                continue
            if theme_node and not home_navigation:
                nearby = [n for n in nodes if abs(n.get('y', -1) - theme_node['y']) < 70]
                if any('今天打卡的次数已用完' in str(n.get('name', '')) for n in nearby):
                    raise DailyLimit(f'{theme}今天的次数已用完，已停止。未将未知对象写入完成记录。')
                self.report(f'进入{theme}打卡页面……')
                self.desktop.click(theme_node)
                self.desktop.pause(.35)
                continue
            community, ranking = find(nodes, '社区'), find(nodes, '排行')
            if community and ranking and find(nodes, '我的'):
                left = community['x'] + community['width'] / 2
                right = ranking['x'] + ranking['width'] / 2
                middle = (left + right) / 2
                choices = [n for n in nodes if n.get('kind') == 'Image' and
                           abs(n['x'] + n['width'] / 2 - middle) < (right - left) * .2 and
                           abs(n['y'] + n['height'] / 2 - community['y']) < 120]
                choices = list({tuple(round(n[k] / 3) for k in ('x', 'y', 'width', 'height')): n for n in choices}.values())
                # The central icon can expose overlapping Image nodes or no
                # bounds. Aligned neighboring labels anchor its actual center.
                mine = find(nodes, '我的')
                if right > left and mine and abs(community['y'] - ranking['y']) <= max(8, community['height']) and abs(mine['y'] - community['y']) <= max(8, community['height']):
                    size = max(12, min(50, community['height']))
                    self.report('打开底部加号后的主题次数列表……')
                    self.desktop.click(dict(name='打卡入口加号', kind='Button', enabled=True,
                                            x=middle-size/2, y=community['y']-community['height']-size/2,
                                            width=size, height=size))
                    self.desktop.pause(.35)
                    continue
                if len(choices) == 1:
                    self.report('已打开打卡次数列表，继续选择目标主题……')
                    self.desktop.click(choices[0])
                    self.desktop.pause(.35)
                    continue
                self.handoff('已到卓越星首页，请点击底部加号，再点击“继续”。')
                continue
            # The native sidebar can contain a chat named 卓越星; go through workbench first.
            sidebar = [n for n in nodes if n.get('name') == '工作台' and n.get('id') == 'ocr']
            if len(sidebar) == 1 and not entered_workbench:
                self.report('点击企业微信侧边栏“工作台”……')
                self.desktop.click(sidebar[0])
                self.desktop.pause(.45)
                entered_workbench = True
                continue
            app = find(nodes, '卓越星')
            if app and not find(nodes, '发现'):
                self.report('从工作台打开“卓越星”小程序……')
                self.desktop.click(app)
                self.desktop.pause(.6)
                continue
            workbench = find(nodes, '工作台')
            if workbench and not entered_workbench:
                self.report('点击“工作台”并查找卓越星……')
                self.desktop.click(workbench)
                self.desktop.pause(.45)
                entered_workbench = True
                continue
            if any(n.get('id') == 'ocr' for n in nodes):
                self.report('在工作台向下查找卓越星入口……')
                self.desktop.scroll()
                self.desktop.pause(.35)
                continue
            self.handoff('暂时无法识别当前页面。请按“企业微信侧边栏 → 工作台 → 卓越星 → 底部加号 → '+theme+'”进入对应打卡页，再点击“继续”。')
        raise NeedUser('导航超过限制，请确认页面状态后重试。')

    def read_remaining(self, themes=THEMES):
        if not themes or any(theme not in THEMES for theme in themes):
            raise ValueError('请选择弘文、明德、求索或力行；矢志由地点流程处理。')
        self.desktop.connect(self.config.get('wecom_exe', ''))
        for _ in range(12):
            nodes = self.nodes()
            counts = remaining_counts(nodes)
            if all(counts.get(theme) is not None for theme in themes):
                self.report('页面剩余次数：' + '；'.join(t + str(counts[t]) + '次' for t in themes))
                return counts
            # Quota discovery must not navigate: incomplete accessibility
            # data can make a visible menu look like another page.
            counts = remaining_counts(nodes + self.desktop.ocr_nodes())
            if all(counts.get(theme) is not None for theme in themes):
                return counts
            missing = '、'.join(theme for theme in themes if counts.get(theme) is None)
            self.handoff(f'未读清{missing}的剩余次数。请将打卡窗口停在底部加号打开的五项列表，'
                         '让列表完整可见，再点“继续当前步骤”。扫描期间不会点击页面。')
        raise NeedUser('未能打开主题次数列表。材料已保留，请打开主题列表后重新开始。')

    def open_selector(self, nodes, theme):
        if date_picker_open(nodes):
            self.fill_date(today())
            nodes = self.nodes()
        if any(n.get('name') == '去打卡' for n in nodes):
            return  # 已在选择列表，避免点击背后表单。
        options = self.unique_click_targets([n for n in nodes if n.get('name') in ('去搜索', '去选择')])
        if theme == '明德之星':
            options = [n for n in options if n.get('name') == '去搜索']
        if len(options) != 1:
            self.handoff('请点击对象字段旁的绿色搜索/选择按钮，进入列表后点击“继续”。')
        else:
            self.desktop.click(options[0])

    @staticmethod
    def row_label_for_action(nodes, action):
        """Read the displayed object label from the same row as its Go button."""
        controls = {'去打卡', '去搜索', '去选择', '搜索', '上一页', '下一页', '取消', '确认',
                    '返回', '关闭', '选择', '请选择', '剩余次数', '今日可以打卡'}
        centre = action['y'] + action['height'] / 2
        candidates = []
        for node in nodes:
            if node.get('kind') not in ('ListItem', 'Hyperlink', 'Text'):
                continue
            label = str(node.get('value') or node.get('name', '')).strip()
            if (not label or label in controls or re.match(r'^(?:https?://|file:|www\.)', label, re.I) or label.startswith(('填写', '请输入', '剩余', '今日', '今天', '点击', '上传', '确认发布', '存草稿', '注意：'))
                    or '之星打卡' in label or '今天打卡的次数' in label):
                continue
            if node.get('x', 0) >= action['x'] + action['width'] / 2:
                continue
            if abs(node['y'] + node['height'] / 2 - centre) > max(25, min(node['height'], action['height']) * 1.1):
                continue
            candidates.append((node, label))
        if not candidates:
            return ''
        # Prefer an actual list row, then a hyperlink, then visible text; for
        # duplicate text providers use the fuller label in that row.
        candidates.sort(key=lambda item: (
            {'ListItem': 0, 'Hyperlink': 1, 'Text': 2}.get(item[0].get('kind'), 3),
            -len(item[1]), item[0].get('x', 0)))
        return candidates[0][1]

    def choose(self, theme, used):
        history = getattr(self, 'history', None)
        if history is not None:
            history.reload()
            used = set(used) | history.used(theme, include_skipped=True)
            record_path = history.theme_path(theme) if hasattr(history, 'theme_path') else history.path
            self.report(f'列表查重：已重新读取 {record_path}，{theme} 有 {len(used)} 个已记录对象（完成、待核实或跳过）。')
        seen = set()
        reported_duplicates = set()
        previous, unchanged = None, 0
        excluded = {str(item) for item in used}
        keyword_length = 4 if theme == '弘文之星' else 6
        if theme == '明德之星':
            excluded_teachers = {object_key(teacher_name(item)) for item in used if teacher_name(item)}
        else:
            excluded_teachers = set()

        def matching_name(name, candidates, minimum_containment=keyword_length):
            return next((old for old in candidates if same_object_by_keyword(
                name, old, minimum_containment=minimum_containment)), None)

        point, resume_entry = self.resume_point(theme, keyword_length)
        if point is not None:
            self.report('续跑记忆：' + theme + ' 上次停在“' + resume_entry['object'] + '”（' +
                        describe_time(resume_entry) + '）；本次从它之后继续，断点之前的条目不再重复检查。')
        order = []
        retried = False
        reported_skip = False
        self.position_hint = None

        for _ in range(200):
            nodes = self.selector_visible_nodes(self.nodes())
            actions = self.unique_click_targets([n for n in nodes
                if object_key(n.get('name', '')) == '去打卡' and n.get('enabled', True)])
            actions = sorted(actions, key=lambda n: (n['y'] + n.get('height', 0) / 2,
                                                     n['x'] + n.get('width', 0) / 2))
            labels = []
            available = []
            for action in actions:
                name = self.row_label_for_action(nodes, action)
                key = object_key(name)
                if not key:
                    continue
                labels.append(name)
                if key not in order:
                    order.append(key)
                    if point is not None and point.active and point.before(name):
                        continue
                elif point is not None and point.active:
                    continue
                person_key = object_key(teacher_name(name)) if theme == '明德之星' else ''
                previous_name = matching_name(name, excluded)
                if previous_name or (person_key and person_key in excluded_teachers):
                    if key not in reported_duplicates:
                        self.report('列表查重，已使用，点击前跳过：' + name +
                                    '（匹配记录：' + (previous_name or person_key) + '）')
                        reported_duplicates.add(key)
                    continue
                if matching_name(name, seen):
                    continue
                target = self.existing_list_target(nodes, name)
                if target:
                    available.append((name, target, len(order) - 1))
            fingerprint = tuple(sorted(set(labels), key=object_key))
            unchanged = unchanged + 1 if fingerprint == previous else 0
            previous = fingerprint
            if unchanged >= 3:
                if point is not None and not point.done and not retried:
                    # The remembered object is no longer listed (removed, renamed
                    # or hidden). Drop the stale cursor and rescan from the top
                    # instead of silently skipping every visible row.
                    self.report('续跑记忆中的对象不在当前列表，已清除这条记忆并回到列表开头按常规查重继续。')
                    self.forget_position(theme)
                    point, retried = None, True
                    previous, unchanged = None, 0
                    seen.clear()
                    order.clear()
                    try:
                        self.rewind_selector(nodes)
                    except NeedUser:
                        self.report('未能把列表滚回开头，按当前位置继续查找。')
                    continue
                if point is not None and point.done:
                    # The remembered prefix and everything after it are done, so
                    # reset the cursor: the next run starts at the top and can
                    # pick up tasks the list inserts there.
                    self.report('续跑记忆：断点之后已经没有未使用对象，本次已扫到列表末尾，'
                                '重置记忆后下次从列表开头查找。')
                    self.forget_position(theme)
                raise NeedUser('已按页面顺序遍历当前可访问列表，没有未使用对象；保留现有记录并停止。')
            if point is not None and point.done and not reported_skip:
                reported_skip = True
                if point.skipped:
                    self.report('续跑记忆：已跳过断点及之前的 ' + str(point.skipped) +
                                ' 个条目，从下一个未使用对象继续。')
            if available:
                name, target, position = min(available, key=lambda item: (
                    item[1]['y'] + item[1]['height'] / 2,
                    item[1]['x'] + item[1]['width'] / 2))
                self.position_hint = position
                self.report('按页面顺序选择未使用对象：' + name)
                self.select_list_item(nodes, target)
                self.desktop.pause(.4)
                if theme == '明德之星':
                    selected = self.wait_for_form(theme, name)
                    return name, self.teacher_fields(name, {}, selected)
                return name, self.details(theme, name)
            seen.update(object_key(name) for name in labels)
            next_page = find(nodes, '下一页')
            if next_page and next_page.get('enabled'):
                self.report('当前页没有未使用对象，翻到下一页继续查找……')
                self.desktop.click(next_page)
                self.desktop.pause(.4)
            else:
                self.scroll_selector(nodes)
        raise NeedUser('下滑查找达到安全上限，仍未找到未使用对象；记录保留，不自动重复。')

    def resume_point(self, theme, minimum_containment=6):
        """Locate the remembered stop point for this list, if any."""
        memory = getattr(self, 'resume', None)
        if memory is None:
            return None, None
        entry = memory.entry(theme)
        if not entry:
            return None, None
        return ResumePoint(entry, lambda left, right: same_object_by_keyword(
            left, right, minimum_containment=minimum_containment)), entry

    def forget_position(self, theme, section=''):
        memory = getattr(self, 'resume', None)
        if memory is not None:
            memory.forget(theme, section)

    def remember_position(self, theme, name):
        """Save where the scan stopped; only a fresh selection may advance it."""
        memory = getattr(self, 'resume', None)
        hint = getattr(self, 'position_hint', None)
        if memory is not None and isinstance(hint, int):
            memory.remember(theme, name, hint)

    def selector_visible_nodes(self, nodes):
        left, top, right, bottom = self.desktop.visible_bounds()
        return [n for n in nodes if n.get('width', 0) > 0 and n.get('height', 0) > 0
                and left <= n['x']+n['width']/2 < right
                and top <= n['y']+n['height']/2 < bottom-35]

    def scroll_selector(self, nodes):
        visible = self.selector_visible_nodes(nodes)
        actions = self.unique_click_targets([n for n in visible if object_key(n.get('name', '')) == '去打卡'])
        left, top, right, bottom = self.desktop.visible_bounds()
        if not actions:
            raise NeedUser('未读到可见列表行，无法确定下滑区域；材料保留。')
        x = max(left+80, min(right-80, min(n['x'] for n in actions)-(right-left)*.18))
        y = sorted(n['y']+n['height']/2 for n in actions)[len(actions)//2]
        self.report('当前屏未找到合适的未使用对象，向下滑动列表继续找……')
        self.desktop.scroll(x=int(x), y=int(y), amount=480)
        self.desktop.pause(.5)

    def rewind_selector(self, nodes):
        """Scroll the selector list back to its first row, bounded and reported."""
        visible = self.selector_visible_nodes(nodes)
        actions = self.unique_click_targets([n for n in visible if object_key(n.get('name', '')) == '去打卡'])
        left, top, right, bottom = self.desktop.visible_bounds()
        if not actions:
            raise NeedUser('未读到可见列表行，无法确定返回区域；材料保留。')
        x = max(left+80, min(right-80, min(n['x'] for n in actions)-(right-left)*.18))
        y = sorted(n['y']+n['height']/2 for n in actions)[len(actions)//2]
        self.report('把列表滚回开头，重新查找未使用对象……')
        for _ in range(8):
            self.desktop.scroll(x=int(x), y=int(y), amount=1200, down=False)
            self.desktop.pause(.4)

    def clear_selector_query(self, nodes):
        controls = self.selector_search_controls(nodes)
        if not controls:
            current = nodes+self.desktop.ocr_nodes()
            controls = self.selector_search_controls(current)
        if not controls:
            return
        input_node, search = controls
        if not self.field_content(input_node, ('搜索', '请输入')):
            return
        editable = dict(input_node, width=min(input_node['width'], search['x']-input_node['x']-8), name='列表搜索输入框')
        if editable['width'] < 15:
            return
        self.desktop.click(editable)
        self.desktop.paste('')
        self.desktop.key(0x08)
        current = self.nodes()
        buttons = self.unique_click_targets([n for n in current if object_key(n.get('name', '')) == '搜索'
                  and abs(n['y']-search['y']) <= max(12, search['height'])])
        if len(buttons) == 1:
            self.desktop.click(buttons[0])
            self.desktop.pause(.7)
        self.report('已清空旧搜索条件，从当前列表选择未使用对象。')

    def is_checkin_form(self, theme, nodes):
        if date_picker_open(nodes) or any(str(n.get('name', '')).strip() == '去打卡' for n in nodes):
            return False
        text = '\n'.join(str(n.get('value') or n.get('name', '')) for n in nodes)
        if any(other+'打卡' in text for other in PAGE_THEMES if other != theme):
            return False
        markers = ('确认发布', '上传图片', '上传封面图', '填写感悟', '填写学习感想',
                   '填写地点', '填写教师所在学院', '填写讲座名称', '填写活动主题')
        return theme + '打卡' in text or any(marker in text for marker in markers)

    def wait_for_form(self, theme, name=None):
        nodes = []
        last_clicked_page = None
        for _ in range(18):
            nodes = self.nodes()
            if self.is_checkin_form(theme, nodes):
                return nodes
            signature = tuple(sorted({(str(n.get('name', '')).strip(), str(n.get('kind', '')))
                                      for n in nodes if str(n.get('name', '')).strip()}))
            if signature != last_clicked_page:
                if name:
                    target = self.existing_list_target(nodes, name)
                    if target:
                        self.report('已定位所选对象所在行，继续进入打卡表单……')
                        self.select_list_item(nodes, target)
                        last_clicked_page = signature
                        self.desktop.pause(.45)
                        continue
                actions = self.unique_click_targets([n for n in nodes
                    if str(n.get('name', '')).strip() in ('去打卡', '开始打卡', '立即打卡')
                    and n.get('enabled', True)])
                if len(actions) == 1 and (not name or find(nodes, name)):
                    self.report('点击当前对象的打卡入口……')
                    self.desktop.click(actions[0])
                    last_clicked_page = signature
                    self.desktop.pause(.45)
                    continue
            self.desktop.pause(.3)
        raise NeedUser('已选择对象，但页面没有切换到打卡表单。请按“企业微信 → 工作台 → 卓越星 → 底部加号 → '+theme+'”打开表单后点击“继续”。')

    def teacher_fields(self, name, facts, nodes):
        texts = [str(n.get('value') or n.get('name', '')) for n in nodes]
        minimum = re.search(r'不少于\s*(\d+)\s*个?字', '\n'.join(texts))
        facts = facts or {}
        college = self.read_field(('填写教师所在学院', '教师所在学院', '学院'), nodes) or facts.get('college', '')
        person = teacher_name(name) or name
        fields = dict(name=name, date=today(), teacher_name=person, college=college,
                    description=facts.get('description', ''),
                    location=person+'老师的办公室', minimum_chars=int(minimum.group(1)) if minimum else self.config.get('minimum_chars',80))
        for key in ('school', 'teacher_source'):
            if facts.get('source' if key == 'teacher_source' else key):
                fields[key] = facts['source' if key == 'teacher_source' else key]
        return fields

    def details(self, theme, name):
        nodes = self.wait_for_form(theme, name)
        texts = [str(n.get('value') or n.get('name', '')).strip()
                 for n in nodes if n.get('kind') in ('Text', 'Edit')]
        facts = []
        seen = set()
        for text in texts:
            key = object_key(text)
            if (not text or not key or key in seen or text == name
                    or text.startswith(('填写', '请输入', '去打卡', '去搜索', '上传图片', '确认发布'))):
                continue
            seen.add(key)
            facts.append(text)
        fields = {'name': name, 'date': today(), 'description': '\n'.join(facts)[:4000]}
        if theme == '明德之星':
            fields['location'] = name + '老师的办公室'
            fields['college'] = self.read_field(('填写教师所在学院', '教师所在学院', '学院'), nodes)
        if theme == '力行之星':
            fields['organizer'] = self.read_field(('填写主办方', '主办方'), nodes)
        minimum = re.search(r'不少于\s*(\d+)\s*个?字', '\n'.join(str(t) for t in texts))
        fields['minimum_chars'] = int(minimum.group(1)) if minimum else self.config['minimum_chars']
        return fields

    @staticmethod
    def field_controls(nodes):
        """Merge two accessibility records of the same physical input."""
        result = []
        for node in nodes:
            if node.get('kind') != 'Edit' or not all(node.get(k, 0) > 0 for k in ('width', 'height')):
                result.append(node)
                continue
            def same_input(other):
                if other.get('kind') != 'Edit':
                    return False
                if all(abs(other.get(k, 0)-node.get(k, 0)) <= tolerance
                       for k, tolerance in (('x', 3), ('y', 3), ('width', 6), ('height', 6))):
                    return True
                a, b = other, node
                contains = lambda a, b: a['x']-8 <= b['x'] and a['y']-8 <= b['y'] and a['x']+a['width']+8 >= b['x']+b['width'] and a['y']+a['height']+8 >= b['y']+b['height']
                aligned = abs(a['x']-b['x']) <= 24 and (
                    abs(a['y']-b['y']) <= 24 or
                    abs(a['y']+a['height']/2-b['y']-b['height']/2) <= max(16,min(a['height'],b['height'])*.55))
                return aligned and (contains(a,b) or contains(b,a))
            matching = next((other for other in result if same_input(other)), None)
            if matching is None:
                result.append(dict(node))
                continue
            first_value = str(matching.get('value') or '').strip()
            second_value = str(node.get('value') or '').strip()
            if not first_value and not second_value:
                first_name = str(matching.get('name') or '').strip()
                second_name = str(node.get('name') or '').strip()
                first_actual = first_name and not first_name.startswith(('填写', '请输入'))
                second_actual = second_name and not second_name.startswith(('填写', '请输入'))
                if first_actual and second_actual and object_key(first_name) != object_key(second_name):
                    result.append(dict(node))
                    continue
                if second_actual and not first_actual:
                    matching['name'] = second_name
            if first_value and second_value and object_key(first_value) != object_key(second_value):
                # Conflicting reads cannot safely establish one input value.
                result.append(dict(node))
                continue
            if second_value and not first_value:
                matching['value'] = second_value
            if not matching.get('name') and node.get('name'):
                matching['name'] = node['name']
        return result

    def field_target(self, nodes, labels):
        nodes = self.field_controls(nodes)
        searches = self.unique_click_targets([n for n in nodes if object_key(n.get('name', '')) == '去搜索'])
        same_search = len(searches) == 1
        form_context = not any(n.get('name') == '去打卡' for n in nodes) and (
            any('之星打卡' in n.get('name', '') for n in nodes) or
            (same_search and
             any(n.get('name') in ('上传图片', '上传封面图') for n in nodes)))
        target = None
        for label in labels:
            matches = [n for n in nodes if n.get('kind') == 'Edit' and
                       (str(n.get('name', '')).strip() == label or
                        (label.startswith('填写') and str(n.get('name', '')).startswith(label)))]
            if len(matches) == 1:
                target = matches[0]
                break
        if target is None:
            # 有些输入框 Name 为空；依据紧邻的字段标签和几何关系选择。
            named_labels = [n for n in nodes if n.get('kind') == 'Text' and any(
                str(n.get('name', '')).strip() == label or
                (label.startswith('填写') and str(n.get('name', '')).startswith(label)) for label in labels)]
            label = named_labels[0] if len(named_labels) == 1 else None
            if label:
                candidates = [n for n in nodes if n.get('kind') == 'Edit' and
                              abs(n['y'] - label['y']) < 80 and n['x'] >= label['x'] - 15]
                if len(candidates) == 1:
                    target = candidates[0]
        if not target and form_context:
            search = searches[0] if same_search else None
            teacher_fields = [n for n in nodes if n.get('kind') == 'Edit' and search and abs(n['y'] - search['y']) < 50]
            if len(teacher_fields) == 1:
                teacher = teacher_fields[0]
                if any(word in label for label in labels for word in (
                        '教师/校友姓名', '讲座名称', '活动主题', '书籍名称', '书名')):
                    target = teacher
                    candidates = []
                elif any('地点' in label for label in labels):
                    candidates = [n for n in nodes if n.get('kind') == 'Edit'
                                  and n['y'] + n['height'] <= teacher['y'] + 5
                                  and n['height'] < max(100, teacher['height']*1.7) and abs(n['x'] - teacher['x']) <= 20]
                    if candidates:
                        nearest = max(n['y'] for n in candidates)
                        candidates = [n for n in candidates if abs(n['y'] - nearest) <= 5]
                elif any('学院' in label for label in labels):
                    candidates = [n for n in nodes if n.get('kind') == 'Edit'
                                  and n['y'] >= teacher['y'] + teacher['height'] - 5
                                  and n['height'] < max(100, teacher['height']*1.7) and abs(n['x'] - teacher['x']) <= 20]
                    if candidates:
                        nearest = min(n['y'] for n in candidates)
                        candidates = [n for n in candidates if abs(n['y'] - nearest) <= 5]
                else:
                    candidates = []
                if len(candidates) == 1:
                    target = candidates[0]
        if not target and any(word in label for label in labels for word in ('读后感', '感想', '感悟')):
            largest = max((n.get('height', 0) for n in nodes if n.get('kind') == 'Edit'), default=0)
            body_fields = [n for n in nodes if n.get('kind') == 'Edit' and n.get('height', 0) >= max(100, largest*.65)]
            if len(body_fields) == 1 and form_context:
                target = body_fields[0]
        if not target and any('主办方' in label for label in labels):
            largest = max((n.get('height', 0) for n in nodes if n.get('kind') == 'Edit'), default=0)
            bodies = [n for n in nodes if n.get('kind') == 'Edit' and n.get('height', 0) >= max(100, largest*.65)]
            if same_search and len(bodies) == 1 and not any(n.get('name') == '去打卡' for n in nodes):
                search, body = searches[0], bodies[0]
                candidates = [n for n in nodes if n.get('kind') == 'Edit' and n.get('height', 0) < 100
                              and n['y'] > search['y'] + search.get('height', 0)
                              and n['y'] + n.get('height', 0) <= body['y'] + 5]
                if len(candidates) == 1:
                    target = candidates[0]
        return target

    @staticmethod
    def field_content(node, labels):
        if not node:
            return ''
        value = str(node.get('value') or node.get('name') or '').strip()
        if value in labels or value.startswith(('填写', '请输入')):
            return ''
        return value

    def read_field(self, labels, nodes=None):
        nodes = self.nodes() if nodes is None else nodes
        return self.field_content(self.field_target(nodes, labels), labels)

    def fill_field(self, labels, value):
        nodes = self.nodes()
        target = self.field_target(nodes, labels)
        if not target:
            return False
        existing = self.field_content(target, labels)
        if not value:
            # A selected activity may already have populated its organizer.
            return bool(existing)
        if existing and object_key(existing) == object_key(value):
            return True
        self.desktop.click(target)
        self.desktop.paste(value)
        for _ in range(3):
            current = self.field_controls(self.nodes())
            # Re-read the field at the same bounds, even when its Name changed
            # from the placeholder to the entered text. Ignore other fields.
            nearby = [n for n in current if n.get('kind') == 'Edit' and
                      abs(n.get('x', 0) - target.get('x', 0)) <= 8 and
                      abs(n.get('y', 0) - target.get('y', 0)) <= 8 and
                      abs(n.get('width', 0) - target.get('width', 0)) <= 12 and
                      abs(n.get('height', 0) - target.get('height', 0)) <= 12]
            if len(nearby) == 1 and object_key(self.field_content(nearby[0], labels)) == object_key(value):
                return True
            relocated = self.field_target(current, labels)
            if relocated and abs(relocated['x']-target['x']) <= max(24,target['width']*.1) and abs(relocated['y']+relocated['height']/2-target['y']-target['height']/2) <= max(24,target['height']*.7):
                if object_key(self.field_content(relocated, labels)) == object_key(value):
                    return True
        return False

    @staticmethod
    def unique_click_targets(nodes):
        """Merge duplicate UIA/MSAA/OCR labels for one physical button."""
        result = []
        for node in sorted(nodes, key=lambda n: n.get('width', 0)*n.get('height', 0)):
            if not all(node.get(k, 0) > 0 for k in ('width', 'height')):
                continue
            cx, cy = node['x']+node['width']/2, node['y']+node['height']/2
            if not any(abs(cx-n['x']-n['width']/2) <= max(8, min(node['height'], n['height'])*.5)
                       and abs(cy-n['y']-n['height']/2) <= max(8, min(node['height'], n['height'])*.5)
                       for n in result):
                result.append(node)
        return result

    def select_list_item(self, nodes, target):
        centre = target['y']+target['height']/2
        actions = self.unique_click_targets([n for n in nodes if object_key(n.get('name', '')) == '去打卡'
                  and abs(n['y']+n['height']/2-centre) <= max(25, min(target['height'], n['height'])*1.1)])
        if len(actions) == 1:
            self.desktop.click(actions[0])
        elif not actions:
            self.desktop.click(target)
        else:
            raise NeedUser('无法唯一确定所选对象所在行的去打卡按钮，已停止。')

    def selector_search_controls(self, values):
        nodes = self.field_controls(values)
        searches = self.unique_click_targets([n for n in nodes
                    if object_key(n.get('name', '')) == '搜索' and n.get('enabled', True)])
        actions = [n for n in nodes if object_key(n.get('name', '')) == '去打卡']
        first_row = min((n['y']+n['height']/2 for n in actions), default=None)
        pairs = []
        for search in searches:
            cy = search['y']+search['height']/2
            if first_row is not None and cy >= first_row:
                continue
            inputs = [n for n in nodes if (n.get('kind') == 'Edit' or
                      (n.get('id') == 'ocr' and object_key(n.get('name', '')) == '请输入'))
                      and n.get('enabled', True) and n['x'] < search['x']-10
                      and abs(n['y']+n['height']/2-cy) <= max(20, min(n['height'], search['height'])*.8)]
            editable_inputs = [n for n in inputs if n.get('kind') == 'Edit']
            inputs = self.unique_click_targets(editable_inputs or inputs)
            # The modal overlays a taller form textarea whose bounding box can
            # also cover this row. Prefer the compact input contained inside
            # it; keep separate, equally sized inputs ambiguous.
            inputs = [n for n in inputs if not any(
                other is not n and n['height'] >= other['height'] * 2
                and n['x'] <= other['x'] and n['y'] <= other['y']
                and n['x'] + n['width'] >= other['x'] + other['width']
                and n['y'] + n['height'] >= other['y'] + other['height']
                for other in inputs)]
            if len(inputs) == 1:
                pairs.append((inputs[0], search))
        if not pairs:
            return None
        if len(pairs) > 1:
            # The modal search immediately above its results outranks native
            # sidebar search controls. Do not select an unrelated search box.
            if first_row is None:
                return None
            nearest = min(first_row-search['y']-search['height']/2 for _, search in pairs)
            pairs = [(n, search) for n, search in pairs if abs(first_row-search['y']-search['height']/2-nearest) <= 8]
        return pairs[0] if len(pairs) == 1 else None

    def existing_list_target(self, nodes, name):
        exact = [n for n in nodes if n.get('kind') in ('Text', 'ListItem', 'Hyperlink')
                 and object_key(n.get('value') or n.get('name', '')) == object_key(name)]
        pairs = []
        for candidate in exact:
            centre = candidate['y']+candidate['height']/2
            actions = [n for n in nodes if object_key(n.get('name', '')) == '去打卡'
                       and abs(n['y']+n['height']/2-centre) <= max(25, min(candidate['height'], n['height'])*1.1)]
            for action in actions:
                pairs.append((candidate, action))
        # A row container, its text and OCR may name the same teacher with
        # different widths. Establish uniqueness by the associated button.
        buttons = self.unique_click_targets([action for _, action in pairs])
        if len(buttons) != 1:
            return None
        button = buttons[0]
        candidates = [n for n, action in pairs if abs(action['y']+action['height']/2-button['y']-button['height']/2)
                      <= max(8, min(action['height'], button['height'])*.5)]
        return min(candidates, key=lambda n: n['width']*n['height']) if candidates else None

    def fill_date(self, value):
        from datetime import date
        date.fromisoformat(value)
        # Let the Tk event loop hide both progress surfaces before capturing
        # the picker; excluding covered pixels alone cannot reveal the date.
        try:
            self.desktop.overlay_visibility(True)
            problem = self._fill_date_visible(value)
        finally:
            self.desktop.overlay_visibility(False)
        if problem:
            self.handoff(problem)
            rendered = self.nodes()
            if not date_matches(rendered, value):
                try:
                    rendered += self.desktop.ocr_nodes()
                except Exception:
                    pass
            if not date_matches(rendered, value):
                raise NeedUser('日期仍未核实为 ' + value + '，已暂停，不会提交。请修正日期后重新点击“填写已有材料”。')

    def submission_gaps(self, row, nodes=None):
        """Return required form values that are not present in the live page."""
        nodes = self.field_controls(self.nodes() if nodes is None else nodes)
        body_labels = ('填写感悟、寄语等', '填写学习感想等', '填写感想、寄语等', '读后感', '感想', '感悟')
        requirements = []
        if row['theme'] == '明德之星':
            requirements.extend((
                ('教师/校友姓名', ('填写教师/校友姓名', '教师/校友姓名')),
                ('地点', ('填写地点', '地点')),
                ('教师所在学院（部门）/校友曾就读学院', ('填写教师所在学院', '教师所在学院', '学院')),
            ))
        elif row['theme'] == '求索之星':
            requirements.append(('讲座名称', ('填写讲座名称', '讲座名称')))
        elif row['theme'] == '力行之星':
            requirements.extend((
                ('活动主题', ('填写活动主题', '活动主题')),
                ('主办方', ('填写主办方', '主办方')),
            ))
        elif row['theme'] == '弘文之星':
            requirements.append(('书籍名称', ('填写书籍名称', '书籍名称', '书名')))

        gaps = []
        for label, labels in requirements:
            if not self.read_field(labels, nodes):
                gaps.append(label)
        selected = self.read_field(OBJECT_FIELDS.get(row['theme'], ()), nodes) if row['theme'] in OBJECT_FIELDS else ''
        if selected and row.get('object') and not self.object_matches(row['theme'], selected, row['object']):
            gaps.append('当前对象与本条材料不一致')

        body = self.read_field(body_labels, nodes)
        minimum = int((row.get('fields') or {}).get('minimum_chars', 80))
        if sum(not char.isspace() for char in body) < minimum:
            gaps.append(f'感想正文（至少 {minimum} 字）')
        elif row.get('text') and object_key(body) != object_key(row['text']):
            gaps.append('正文与本条准备材料不一致')

        if row['theme'] == '明德之星':
            expected_date = (row.get('fields') or {}).get('date') or row.get('date')
            if expected_date and not date_matches(nodes, expected_date):
                try:
                    ocr_nodes = self.desktop.ocr_nodes()
                except Exception:
                    ocr_nodes = []
                if not date_matches(ocr_nodes, expected_date):
                    gaps.append('日期（' + expected_date + '）')

        return gaps

    @staticmethod
    def object_matches(theme, selected, expected):
        if object_key(selected) == object_key(expected):
            return True
        if theme == '明德之星':
            selected_person, expected_person = teacher_name(selected), teacher_name(expected)
            return bool(selected_person and expected_person and selected_person == expected_person)
        return False

    def _fill_date_visible(self, value):
        nodes = self.nodes()
        overlay = date_picker_open(nodes)
        if not overlay and date_matches(nodes, value):
            return None
        if not overlay and any(find(nodes, label, {'Edit'}) for label in ('日期', '时间')) and self.fill_field(('日期', '时间'), value):
            return None
        # A retry can begin inside an already-open wheel dialog. Do not click
        # the underlying form's selector through the modal overlay.
        if not overlay:
            label = find(nodes, '时间') or find(nodes, '日期')
            selectors = [n for n in nodes if str(n.get('name', '')).strip() == '去选择'
                         and label and abs(n['y']-label['y']) < max(50, label['height']*2)]
            if len(selectors) != 1:
                return '日期入口未能识别，请核对日期为 ' + value + ' 后点击“继续”。'
            self.desktop.click(selectors[0])
            self.desktop.pause(.6)
        desired = dict(zip('年月日', map(int, value.split('-'))))
        unchanged, previous = 0, None
        # Bounded gestures, with fresh rendered rows after every movement.
        for attempt in range(18):
            nodes = self.nodes() + self.desktop.ocr_nodes()
            shot, origin = self.desktop.screenshot(with_origin=True)
            state = picker_rows(nodes, shot, origin)
            if not state:
                if attempt < 2:
                    self.desktop.pause(.6)
                    continue
                confirm = centered_picker_confirm(nodes, shot, origin, value)
                if confirm:
                    self.report('日期选中行对比度无法识别，但滚轮中心与目标日期一致；确认后回读表单日期……')
                    self.desktop.click(confirm)
                    for _ in range(4):
                        rendered = self.nodes()
                        if date_matches(rendered, value):
                            return None
                        if date_matches(rendered+self.desktop.ocr_nodes(), value):
                            return None
                        self.desktop.pause(.4)
                    return '确认后的表单日期未能读取为 ' + value + '，请核对后继续。'
                return '日期滚轮的选中行未能读取。请设置为 ' + value + ' 后点击“继续”。'
            current = tuple(state['selected'][unit]['number'] for unit in '年月日')
            if current == tuple(desired.values()):
                self.report('日期滚轮已核实为 ' + value + '，确认并读取表单结果……')
                self.desktop.click(state['confirm'])
                for _ in range(4):
                    rendered = self.nodes()
                    if date_matches(rendered, value):
                        return None
                    if date_matches(rendered+self.desktop.ocr_nodes(), value):
                        return None
                    self.desktop.pause(.4)
                return '日期确认后的表单值未能读取为 ' + value + '，请核对后继续。'
            unchanged = unchanged+1 if current == previous else 0
            if unchanged >= 2:
                return '日期滚轮没有响应移动，请设置为 ' + value + ' 后点击“继续”。'
            previous = current
            unit = next(u for u in '年月日' if state['selected'][u]['number'] != desired[u])
            row = state['selected'][unit]
            step = state['step']
            if not step:
                return '日期滚轮间距未能识别，请设置为 ' + value + ' 后点击“继续”。'
            difference = desired[unit]-row['number']
            count = min(3, abs(difference))
            distance = step*count*(-1 if difference > 0 else 1)
            end_y = min(state['bottom'], max(state['top'], row['cy']+distance))
            if abs(end_y-row['cy']) < step*.6:
                return '日期滚轮可操作区域不足，请放大企业微信窗口，核对日期后继续。'
            self.report(f'调整日期{unit}列：{row["number"]} → {desired[unit]}……')
            self.desktop.drag(row, end_y-row['cy'])
            self.desktop.pause(.7)
        return '日期调整达到重试上限，请设置为 ' + value + ' 后点击“继续”。'

    def upload(self, image):
        image = Path(image).resolve()
        if not image.is_file():
            raise NeedUser('配图文件不存在，请重新选择图片。')
        nodes = self.nodes()
        def thumbnails(values):
            upload_label = find(values, '上传图片') or find(values, '上传封面图')
            if not upload_label:
                return 0
            return sum(n.get('kind') == 'Image' and n.get('id') == 'msaa' and
                       abs(n['y'] - upload_label['y']) <= max(100, upload_label['height'] * 6)
                       for n in values)
        before_images = thumbnails(nodes)
        if before_images:
            if matches_uploaded_photo(self.desktop, nodes, image):
                self.report('页面已有本条配图，缩略图匹配，跳过重复上传。')
                return
            raise NeedUser('页面已有配图，但未能核实它属于本条对象；请移除旧图后恢复，暂不自动提交。')
        target = find(nodes, '上传图片') or find(nodes, '上传封面图')
        if not target:
            self.desktop.scroll()
            target = find(self.nodes(), '上传图片') or find(self.nodes(), '上传封面图')
        if not target:
            self.handoff('请在打卡页面手动上传助手窗口里显示的配图，再点击“继续”。')
            return
        # The upload label can exist below the visible scroll area while
        # its center is covered by the fixed publish footer. Scroll first.
        footer = find(nodes, '存草稿')
        if footer and target['y'] + target['height'] >= footer['y'] - 8:
            self.desktop.scroll()
            nodes = self.nodes()
            target = find(nodes, '上传图片') or find(nodes, '上传封面图')
            footer = find(nodes, '存草稿')
            if not target:
                self.handoff('上传控件不可见。请滚动到配图区域完成上传，再点击“继续”。')
                return
            if footer and target['y'] + target['height'] >= footer['y'] - 8:
                visible_height = footer['y'] - 8 - target['y']
                if visible_height < 5:
                    self.handoff('上传控件被底栏遮挡。请放大企业微信窗口完成上传，再点击“继续”。')
                    return
                target = dict(target, height=min(target['height'], visible_height))
        def file_dialogs():
            return {w['hwnd'] for w in self.desktop.windows()
                    if w['title'] in ('打开', '选择文件', '上传文件', 'Open')}

        existing = file_dialogs()
        self.desktop.click(target)
        for _ in range(8):
            self.desktop.pause(.25)
            dialogs = file_dialogs() - existing
            if dialogs:
                break
        if len(dialogs) != 1:
            self.handoff('图片选择窗口无法唯一识别。请手动选择配图完成上传，再点击“继续”。')
            return
        previous = self.desktop.hwnd
        dialog = dialogs.pop()
        self.desktop.hwnd = dialog
        try:
            # The native file picker has no browser address bar.
            def filename_field():
                fields = [n for n in self.desktop.snapshot()
                          if n.get('kind') == 'Edit' and not n.get('offscreen')]
                named = [n for n in fields if n.get('id') == '1148']
                if len(named) == 1:
                    return named[0]
                return find(fields, '文件名') or find(fields, 'File name')

            target = filename_field()
            if not target:
                raise NeedUser('文件选择框无法识别，请手动上传配图。')
            self.desktop.click(target)
            verified = self.desktop.set_dialog_filename(target, str(image))
            if not verified:
                self.desktop.paste(str(image))
                entered = filename_field()
                verified = entered and entered.get('value', '').strip('"') == str(image)
            if not verified or not image.is_file():
                raise NeedUser('未能核实配图的完整路径。请关闭文件选择框后继续，配图保留。')
            self.desktop.key(0x0D)
            for _ in range(12):
                self.desktop.pause(.25)
                if dialog not in file_dialogs():
                    break
            else:
                raise NeedUser('文件选择框仍未关闭。请处理窗口提示后继续，配图保留。')
        finally:
            self.desktop.hwnd = previous
        after = self.nodes()
        for _ in range(20):
            if not any('上传中' in str(n.get('name', '')) for n in after):
                break
            self.desktop.pause(.5)
            after = self.nodes()
        if any('上传中' in str(n.get('name', '')) for n in after):
            raise NeedUser('配图仍在上传，已停止，待提交材料保留。请等上传结束后审查。')
        if any('上传失败' in str(n.get('name', '')) for n in after):
            raise NeedUser('页面报告图片上传失败，请重试。')
        after_images = thumbnails(after)
        if after_images <= before_images:
            self.report('自动核验上传缩略图……')
            for _ in range(12):
                if matches_uploaded_photo(self.desktop, after, image):
                    self.report('上传缩略图与本条审查通过的配图匹配。')
                    return
                self.desktop.pause(.5)
                after = self.nodes()
                if any('上传失败' in str(n.get('name', '')) for n in after):
                    raise NeedUser('页面报告图片上传失败，已停止。')
            raise NeedUser('未能自动核实本条图片的上传缩略图，已暂停；不会提交未经核验的图片。')

    def page_ready(self, theme, nodes):
        """True when the mini-program has rendered something we can navigate."""
        if any(self.is_checkin_form(other, nodes) for other in PAGE_THEMES):
            return True
        if any(n.get('name') == '去打卡' for n in nodes):
            return True
        if any(str(n.get('name', '')).startswith(tuple(PAGE_THEMES)) for n in nodes):
            return True
        return all(find(nodes, name) for name in ('社区', '排行', '我的'))

    def wait_for_page(self, theme, attempts=10):
        """Ctrl+R blanks the mini-program for a moment; re-read until it renders.

        Without this pause ``navigation`` sees an empty page and tries to
        re-enter through the 工作台 sidebar while the host is still repainting.
        """
        nodes = self.nodes()
        for _ in range(attempts):
            if self.page_ready(theme, nodes):
                return nodes
            self.desktop.pause(.5)
            nodes = self.nodes()
        self.report('重新加载后页面识别较慢，按当前页面继续。')
        return nodes

    def prepare(self, theme):
        if theme not in THEMES:
            raise ValueError('请选择弘文、明德、求索或力行；矢志由地点流程处理。')
        self.history.reload()
        self.report('检查 DeepSeek 余额……')
        self.report('可用余额：' + self.api.balance())
        self.report('打开企业微信及卓越星……')
        nodes = self.navigation(theme)
        row = self.history.pending(theme)
        if row and row['status'] == '结果待核实':
            raise NeedUser('此对象有发布结果待核实，不能自动重提或替换：' + row['object'])
        selected = self.read_field(OBJECT_FIELDS[theme], nodes) if row else ''
        if row and selected and object_key(selected) == object_key(row['object']):
            self.report('继续当前待提交对象：' + row['object'])
        else:
            # Recreate the web form before selecting another target. The mini-app
            # can keep its previous upload/textarea even when its title changes.
            self.report('刷新打卡页面，清除上一条表单的文字与配图……')
            self.desktop.refresh()
            self.wait_for_page(theme)
            nodes = self.navigation(theme)
            used = self.history.used(theme, include_skipped=True)
            try:
                self.desktop.overlay_visibility(True)
                self.open_selector(nodes, theme)
                self.clear_selector_query(self.nodes())
                self.report('按列表顺序选择一个未使用对象；必要时自动下滑……')
                name, fields = self.choose(theme, used)
            finally:
                self.desktop.overlay_visibility(False)
            if row:
                self.report('改选新目标：' + name + '；旧对象材料保留为已放弃：' + row['object'])
                row = self.history.replace_pending(row['id'], name, fields)
            else:
                row = self.history.reserve(theme, name, fields)
            self.remember_position(theme, row['object'])
        self.on_row(row.copy())
        if theme == '明德之星':
            try:
                row = self.complete_teacher_fields(row)
            except MaterialUnavailable as error:
                self.skip_unavailable(row, str(error))
        if not row.get('text'):
            self.report('生成感想……')
            text = self.api.reflection(theme, row['fields'], row['fields'].get('minimum_chars', 80))
            row = self.history.update(row['id'], text=text, phase='文字已生成')
            self.on_row(row.copy())
        if theme == '明德之星':
            self.report('明德之星无需照片，跳过图片搜索和准备。')
            return row
        finder = ImageFinder(self.api, self.data_root, self.cancel, self.report, self.config.get('allow_fake_ip_dns', False))
        if not row.get('image') or not finder.ready(theme, row['object'], row['id'], row['image']):
            if row.get('image'):
                row = self.history.update(row['id'], image='', image_warning='', phase='旧图片未通过新版审查，重新搜索')
                self.on_row(row.copy())
            self.report('搜索主题配图……')
            try:
                image = finder.choose(theme, row['object'], row['id'])
            except ImageSearchUnavailable as error:
                self.history.update(row['id'], phase='配图服务不可用，保留待提交：' + str(error))
                raise NeedUser(str(error)) from error
            except ImageSelectionRequired as error:
                self.skip_unavailable(row, str(error))
            row = self.history.update(row['id'], image=image, phase='配图已保存')
        row = self.history.update(row['id'], image_warning=finder.warning(row['id'], row['image']))
        self.on_row(row.copy())
        return row

    def skip_unavailable(self, row, reason):
        self.desktop.check()
        row = self.history.update(row['id'], status='已跳过', image='',
                                  image_warning=reason, phase='资料不可用，已跳过：' + reason)
        self.on_row(row.copy())
        raise MaterialUnavailable(row['object'] + '：' + reason)

    def fill(self, row):
        try:
            self.desktop.overlay_visibility(True)
            return self._fill_visible(row)
        finally:
            self.desktop.overlay_visibility(False)

    def complete_teacher_fields(self, row):
        fields = dict(row.get('fields') or {})
        person = fields.get('teacher_name') or teacher_name(row['object']) or row['object']
        fields.update(date=today(), teacher_name=person, location=person+'老师的办公室')
        labels = ('填写教师所在学院', '教师所在学院', '学院')
        college = self.read_field(labels) or fields.get('college', '')
        if not college:
            self.report('所属学院未预填，调用 DeepSeek 补全教师资料：' + person)
            from .teachers import TeacherDirectory
            directory = TeacherDirectory(self.root, self.api, self.config.get('allow_fake_ip_dns', False))
            try:
                cached = directory.known(person)
                generated = dict(cached, cached=True) if cached else self.api.teacher_information(person)
            except TeacherInformationMissing:
                self.report('首次未提供学院，检索同济公开资料后交给 DeepSeek 再次询问：' + person)
                generated = directory.lookup(person)
                if not generated:
                    answer = self.api.teacher_followup(person)
                    message = 'DeepSeek 补充回答（尚无可核对来源）：' + (answer.get('college') or '未给出学院')
                    if answer.get('reason'):
                        message += '；' + answer['reason']
                    self.report(message)
                    raise MaterialUnavailable(message)
                generated = dict(generated, retried=True)
            college = str(generated.get('college') or '').strip()
            if not college:
                raise MaterialUnavailable('DeepSeek 未提供所属学院')
            self.report(('已核对资料结果：' if generated.get('cached') else
                         'DeepSeek 再次询问结果：' if generated.get('retried') else 'DeepSeek 补全结果：') +
                        person + ' → ' + college)
            if generated.get('source'):
                self.report('学院资料来源：' + str(generated['source']))
                fields['teacher_source'] = generated['source']
            if not fields.get('description'):
                fields['description'] = generated.get('description', '')
        fields['college'] = college
        if fields != (row.get('fields') or {}):
            row = self.history.update(row['id'], fields=fields)
            self.on_row(row.copy())
        return row

    def _fill_visible(self, row):
        theme = row['theme']
        nodes = self.nodes()
        if theme == '明德之星' and date_picker_open(nodes):
            self.fill_date(today())
            nodes = self.nodes()
        if not self.is_checkin_form(theme, nodes):
            raise NeedUser('当前仍在对象列表或不是本主题表单；已暂停，避免填入其他页面。')
        selected = self.read_field(OBJECT_FIELDS[theme], nodes) if theme in OBJECT_FIELDS else ''
        if selected and not self.object_matches(theme, selected, row['object']):
            raise NeedUser('页面已选对象与本条准备材料不同，已暂停，避免混用；请重新开始选择一个未使用对象。')
        if theme == '明德之星':
            row = self.complete_teacher_fields(row)
        original_fields = row.get('fields') or {}
        fields = dict(original_fields)
        self.report('按已选对象填写页面；页面提供的对象和预填信息直接采用。')
        missing = []
        if theme == '明德之星':
            if not self.fill_field(('填写地点', '地点'), fields.get('location')):
                missing.append('地点：' + fields.get('location', ''))
            college_labels = ('填写教师所在学院', '教师所在学院', '学院')
            college = fields.get('college')
            if college and self.fill_field(college_labels, college):
                fields['college'] = college
            elif not self.fill_field(college_labels, ''):
                missing.append('教师所在学院（部门）/校友曾就读学院：页面该必填项为空，请填写真实信息')
            # 日期控件各版本布局不同，不将随机 Enter 用到日期/发布。
            self.fill_date(today())
        if theme == '力行之星':
            labels = ('填写主办方', '主办方')
            organizer = fields.get('organizer') or self.read_field(labels)
            if organizer:
                if self.fill_field(labels, organizer):
                    fields['organizer'] = organizer
                else:
                    missing.append('主办方：' + organizer)
            elif not self.fill_field(labels, ''):
                missing.append('主办方：页面该必填项为空，请填写真实信息')
        if fields != original_fields:
            row = self.history.update(row['id'], fields=fields)
            self.on_row(row.copy())
        if not self.fill_field(('填写感悟、寄语等', '填写学习感想等', '填写感想、寄语等', '读后感', '感想', '感悟'), row['text']):
            missing.append('正文框暂未接收文字，请在表单中完成输入后继续')
        if missing:
            self.handoff('自动输入未完成：\n' + '\n'.join(missing))
        gaps = self.submission_gaps(row)
        if gaps:
            raise NeedUser('表单仍有必填内容未核实：' + '、'.join(gaps) + '。请在企业微信中补齐真实信息后重新点击“填写已有材料”；不会提交。')
        saved_fields = dict(row.get('fields') or {})
        if theme == '明德之星':
            saved_fields['college'] = self.read_field(('填写教师所在学院', '教师所在学院', '学院'))
            saved_fields['location'] = self.read_field(('填写地点', '地点'))
        elif theme == '力行之星':
            saved_fields['organizer'] = self.read_field(('填写主办方', '主办方'))
        if saved_fields != (row.get('fields') or {}):
            row = self.history.update(row['id'], fields=saved_fields)
            self.on_row(row.copy())
        if theme != '明德之星':
            self.upload(row['image'])
        self.history.update(row['id'], phase='已填写，等待系统提交结果')
        if theme == '明德之星':
            self.report('填写完成。明德之星无需照片，自动提交并等待系统结果。')
        else:
            self.report('填写完成，自动提交并等待系统结果。' + row.get('image_warning', ''))

    def submit(self, row):
        from .publishing import publish
        from .completion import finalize_completed
        result, completed = publish(self, row)
        if result not in ('success', 'already') or completed['status'] != '已完成':
            raise NeedUser('提交结果未确认，不清除材料，也不切换对象。')
        completed = finalize_completed(self.history, self.data_root, completed, self.report)
        self.on_row(completed.copy())
        self.report('本条系统结果已记录；当前材料显示将清空，刷新页面后继续下一个对象。')
        try:
            self.desktop.refresh()
        except (OSError, RuntimeError) as error:
            self.report('系统结果已记录，页面刷新失败；下条选择前将重新刷新：' + str(error))
        return result, completed
