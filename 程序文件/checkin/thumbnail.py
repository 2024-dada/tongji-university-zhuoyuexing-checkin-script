"""Verify decorative uni-file-picker thumbnails against the approved local photo."""
import io

from PIL import Image, ImageChops, ImageOps, ImageStat

def matches_uploaded_photo(desktop, nodes, filename):
    labels = [node for node in nodes
              if str(node.get('name', '')).strip() in ('上传图片', '上传封面图')
              and node.get('width', 0) > 0 and node.get('height', 0) > 0]
    # MSAA exposes named parent containers as well as the text. Its text
    # bounding height is the glyph height, not the CSS line-height (20px).
    label = min(labels, key=lambda node: node['width'] * node['height']) if labels else None
    if not label or not filename:
        return False
    # The uni-file-picker places a square photo immediately before its add tile.
    # Four 14px Chinese characters occupy 56 CSS px. Width provides the
    # scale even when MSAA reports only the 16px glyph instead of its line box.
    characters = len(str(label.get('name', '')).strip())
    height = label['width'] / (14 * characters) * 20
    if height <= 0:
        return False
    try:
        data, (origin_x, origin_y) = desktop.screenshot(with_origin=True)
        screen = Image.open(io.BytesIO(data)).convert('RGB')
        with Image.open(filename) as source:
            source = ImageOps.exif_transpose(source).convert('RGB')
            # WeCom versions differ: some center-crop the image, others stretch
            # the whole source into the square uni-file-picker preview.
            squares = (ImageOps.fit(source, (128, 128)), source.resize((128, 128)))
        # Exclude borders and the delete icon in the upper right corner.
        references = [square.crop((20, 38, 108, 108)).resize((16, 16)) for square in squares]
        references = [reference for reference in references if sum(ImageStat.Stat(reference).stddev) >= 18]
        if not references:
            return False  # A nearly uniform patch cannot establish identity.
        x = label['x'] - origin_x
        y = label['y'] - origin_y
        regions = []
        # Prefer the actual exposed Image bounds. The add-label position is
        # only a fallback for builds whose preview is decorative HTML.
        for node in nodes:
            if (node.get('kind') == 'Image' and node.get('width', 0) > 0
                    and node.get('height', 0) > 0
                    and node['x'] + node['width'] / 2 < label['x']
                    and abs(node['y'] + node['height'] / 2 - label['y']) <= height * 4
                    and .8 <= node['width'] / node['height'] <= 1.2
                    and height * 2 <= node['width'] <= height * 8):
                for inset in (0, 1, 2):
                    regions.append((node['x'] - origin_x + inset,
                                    node['y'] - origin_y + inset,
                                    node['width'] - inset * 2, node['height'] - inset * 2))
        for ratio in (4.0, 4.15, 4.3, 4.45, 4.6):
            side = height * ratio
            left = x - side - height * 1.25
            top = y - height * 2.3
            for dx in (-.3, -.15, 0, .15, .3):
                for dy in (-.4, -.3, -.2, -.1, 0, .1, .2, .3, .4):
                    tile_x, tile_y = left + dx * height, top + dy * height
                    regions.append((tile_x, tile_y, side, side))
        for left, top, width, height in regions:
            box = (round(left + width * 20 / 128),
                   round(top + height * 38 / 128),
                   round(left + width * 108 / 128),
                   round(top + height * 108 / 128))
            if box[0] < 0 or box[1] < 0 or box[2] > screen.width or box[3] > screen.height:
                continue
            actual = screen.crop(box).resize((16, 16))
            for reference in references:
                difference = ImageStat.Stat(ImageChops.difference(reference, actual))
                # Browser/server resampling and JPEG compression can introduce
                # small differences even for the exact uploaded source.
                if sum(difference.mean) / 3 <= 15:
                    return True
    except (OSError, ValueError):
        return False
    return False
