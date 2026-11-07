"""Locate the visible Chromium scrollbar thumb; never infer an end from card count."""
def locate_scrollbar(picture, bounds, horizontal=True):
    image = picture.convert('RGB')
    left, top, right, bottom = map(int, bounds)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    length = right - left if horizontal else bottom - top
    if length < 100:
        return None
    pixels = image.load()
    rows = []
    for cross in range(top, bottom) if horizontal else range(left, right):
        values = [pixels[pos, cross] if horizontal else pixels[cross, pos]
                  for pos in (range(left, right) if horizontal else range(top, bottom))]
        white = sum(min(rgb) >= 230 and max(rgb) - min(rgb) <= 12 for rgb in values)
        grey = [max(rgb) - min(rgb) <= 8 and 115 <= sum(rgb) / 3 <= 185 for rgb in values]
        start = 0
        runs = []
        for i, present in enumerate(grey + [False]):
            if present and (i == 0 or not grey[i - 1]):
                start = i
            if not present and i and grey[i - 1]:
                runs.append((start, i))
        if not runs:
            continue
        a, b = max(runs, key=lambda run: run[1] - run[0])
        if b - a < 35 or white + (b - a) < length * .90:
            continue
        rows.append((cross, a, b))
    # A solid band across several adjacent pixels distinguishes thumbs from text.
    bands = []
    for row in rows:
        if bands and row[0] == bands[-1][-1][0] + 1 and abs(row[1] - bands[-1][-1][1]) <= 3 and abs(row[2] - bands[-1][-1][2]) <= 3:
            bands[-1].append(row)
        else:
            bands.append([row])
    bands = [band for band in bands if len(band) >= 3]
    if not bands:
        return None
    band = max(bands, key=lambda group: group[len(group)//2][2] - group[len(group)//2][1])
    cross, a, b = band[len(band)//2]
    offset = left if horizontal else top
    values = [pixels[pos, cross] if horizontal else pixels[cross, pos]
              for pos in (range(left, right) if horizontal else range(top, bottom))]
    neutral = [max(rgb)-min(rgb) <= 12 and min(rgb) >= 115 for rgb in values]
    track_start, track_end = a, b
    while track_start > 0 and neutral[track_start-1]:
        track_start -= 1
    while track_end < length and neutral[track_end]:
        track_end += 1
    if not horizontal:
        # White sticky headers can share the track's background. Its small
        # arrow runs identify the actual ends, rather than the crop's edges.
        grey = [max(rgb)-min(rgb) <= 8 and 115 <= sum(rgb)/3 <= 185 for rgb in values]
        runs = []
        start = 0
        for i, present in enumerate(grey + [False]):
            if present and (i == 0 or not grey[i-1]):
                start = i
            if not present and i and grey[i-1] and 2 <= i-start <= 30:
                runs.append((start, i))
        before = [run for run in runs if run[1] < a]
        after = [run for run in runs if run[0] > b]
        if before:
            track_start = max(0, before[-1][0]-8)
        if after:
            track_end = min(length, after[0][1]+8)
    if horizontal:
        return dict(left=offset+track_start, right=offset+track_end, y=cross, thumb_left=offset+a, thumb_right=offset+b)
    return dict(top=offset+track_start, bottom=offset+track_end, x=cross, thumb_top=offset+a, thumb_bottom=offset+b)
