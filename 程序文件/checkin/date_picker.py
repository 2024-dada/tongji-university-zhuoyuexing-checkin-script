"""Read the selected wheel rows from rendered date labels, not hidden options."""
import io
import re
from statistics import median


def date_picker_open(nodes):
    names = {str(node.get('name', '')).strip() for node in nodes}
    return '确认' in names and '取消' in names


def date_matches(nodes, value):
    wanted = tuple(map(int, value.split('-')))
    pattern = re.compile(r'(?<!\d)(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})(?:日)?(?!\d)')
    for node in nodes:
        for text in (node.get('value'), node.get('name')):
            text = ''.join(str(text or '').split())
            if text.startswith(('时间', '日期')):
                text = text[2:]
            match = pattern.fullmatch(text)
            if match and tuple(map(int, match.groups())) == wanted:
                return True
    return False


def centered_picker_confirm(nodes, screenshot, origin, value):
    """Return the date picker's confirm button when its centred row is the target date.

    This is a narrow fallback for OCR that can read the wheel labels but cannot
    distinguish their selected-state contrast. The form value is still checked
    after confirmation before the workflow can continue.
    """
    from PIL import Image
    picture = Image.open(io.BytesIO(screenshot))
    left, top = origin
    right, bottom = left+picture.width, top+picture.height
    confirms = [n for n in nodes if str(n.get('name', '')).strip() == '确认'
                and n.get('width', 0) > 0 and n.get('height', 0) > 0
                and left <= n['x'] < right and top <= n['y'] < bottom]
    cancels = [n for n in nodes if str(n.get('name', '')).strip() == '取消'
               and n.get('width', 0) > 0 and n.get('height', 0) > 0]
    paired = [n for n in confirms if any(abs(c['y']-n['y']) < max(20, n['height']*2)
              and left <= c['x'] < n['x'] for c in cancels)]
    if not paired:
        return None
    confirm = min(paired, key=lambda n: (n.get('id') != 'ocr', n['width']*n['height']))
    wheel_top = confirm['y']+confirm['height']+5
    wheel_bottom = bottom-8
    if wheel_bottom-wheel_top < 60:
        return None

    desired = dict(zip('年月日', map(int, value.split('-'))))
    centered = {}
    for unit in '年月日':
        matches = []
        for node in nodes:
            match = re.fullmatch(r'(\d{1,4})'+unit, ''.join(str(node.get('name', '')).split()))
            if not match or int(match[1]) != desired[unit]:
                continue
            if node.get('width', 0) <= 0 or node.get('height', 0) <= 0:
                continue
            x, y, width, height = (node[k] for k in ('x', 'y', 'width', 'height'))
            if x < left or x+width > right or y <= wheel_top or y+height > wheel_bottom:
                continue
            matches.append(dict(node, cx=x+width/2, cy=y+height/2))
        ocr = [n for n in matches if n.get('id') == 'ocr']
        if ocr:
            matches = ocr
        distinct = []
        for row in sorted(matches, key=lambda n: n['width']*n['height']):
            if not any(abs(row['cy']-other['cy']) <= 8 and abs(row['cx']-other['cx']) <= 20
                       for other in distinct):
                distinct.append(row)
        if len(distinct) != 1:
            return None
        centered[unit] = distinct[0]

    centres = [row['cy'] for row in centered.values()]
    heights = [row['height'] for row in centered.values()]
    tolerance = max(12, median(heights)*.7)
    wheel_centre = (wheel_top+wheel_bottom)/2
    if max(centres)-min(centres) > tolerance:
        return None
    if abs(median(centres)-wheel_centre) > max(14, median(heights)*1.25):
        return None
    if not centered['年']['cx'] < centered['月']['cx'] < centered['日']['cx']:
        return None
    return confirm


def picker_rows(nodes, screenshot, origin):
    from PIL import Image
    picture = Image.open(io.BytesIO(screenshot)).convert('L')
    left, top = origin
    right, bottom = left+picture.width, top+picture.height
    buttons = [n for n in nodes if str(n.get('name', '')).strip() == '确认'
               and n.get('width', 0) > 0 and n.get('height', 0) > 0
               and left <= n['x'] < right and top <= n['y'] < bottom]
    cancels = [n for n in nodes if str(n.get('name', '')).strip() == '取消']
    paired = [n for n in buttons if any(abs(c['y']-n['y']) < max(20, n['height']*2)
              and c['x'] < n['x'] for c in cancels)]
    if not paired:
        return None
    # OCR's tight rectangle is preferable to a large accessible container.
    confirm = min(paired, key=lambda n: (n.get('id') != 'ocr', n['width']*n['height']))
    toolbar_bottom = confirm['y']+confirm['height']
    columns = {unit: [] for unit in '年月日'}
    for node in nodes:
        match = re.fullmatch(r'(\d{1,4})([年月日])', ''.join(str(node.get('name', '')).split()))
        if not match or node.get('width', 0) <= 0 or node.get('height', 0) <= 0:
            continue
        number, unit = int(match[1]), match[2]
        if not (1 <= number <= {'年': 9999, '月': 12, '日': 31}[unit]):
            continue
        x, y, width, height = (node[k] for k in ('x', 'y', 'width', 'height'))
        if y <= toolbar_bottom or x < left or x+width > right or y+height > bottom:
            continue
        row = dict(node, number=number, cx=x+width/2, cy=y+height/2)
        columns[unit].append(row)
    if any(not rows for rows in columns.values()):
        return None
    for unit, rows in columns.items():
        ocr = [n for n in rows if n.get('id') == 'ocr']
        if ocr:
            rows = ocr
        dedup = []
        for row in sorted(rows, key=lambda n: n['width']*n['height']):
            if not any(row['number'] == n['number'] and abs(row['cy']-n['cy']) <= 8
                       and abs(row['cx']-n['cx']) <= 20 for n in dedup):
                dedup.append(row)
        for row in dedup:
            crop = picture.crop((int(row['x']-left), int(row['y']-top),
                                 int(row['x']+row['width']-left), int(row['y']+row['height']-top)))
            histogram = crop.histogram()
            cutoff, accumulated = max(1, crop.width*crop.height*.08), 0
            for level, count in enumerate(histogram):
                accumulated += count
                if accumulated >= cutoff:
                    row['ink'] = level
                    break
        columns[unit] = dedup
    selected = {}
    for unit, rows in columns.items():
        ranked = sorted(rows, key=lambda n: n['ink'])
        chosen = ranked[0]
        # The selected row is dark; adjacent wheel options are faded. Hidden
        # options and an arbitrary aligned off-centre row are never proof.
        if chosen['ink'] >= 140 or (len(ranked) > 1 and ranked[1]['ink']-chosen['ink'] < 12):
            return None
        selected[unit] = chosen
    centres = [n['cy'] for n in selected.values()]
    tolerance = max(12, median(n['height'] for n in selected.values())*.7)
    if max(centres)-min(centres) > tolerance:
        return None
    if not selected['年']['cx'] < selected['月']['cx'] < selected['日']['cx']:
        return None
    gaps = []
    for rows in columns.values():
        ordered = sorted(rows, key=lambda n: n['number'])
        for a, b in zip(ordered, ordered[1:]):
            if b['number'] == a['number']+1 and b['cy'] > a['cy']+5:
                gaps.append(b['cy']-a['cy'])
    return dict(confirm=confirm, columns=columns, selected=selected,
                centre=median(centres), step=median(gaps) if gaps else None,
                top=toolbar_bottom+5, bottom=bottom-8)
