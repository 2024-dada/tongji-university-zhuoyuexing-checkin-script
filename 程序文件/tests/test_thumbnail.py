import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageOps

from checkin.thumbnail import matches_uploaded_photo


class ThumbnailTests(unittest.TestCase):
    def test_stretched_preview_matches_photo_but_rejects_a_different_photo(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'approved.png'
            photo = Image.new('RGB', (900, 500))
            photo.putdata([(x * 255 // 900, y * 255 // 500, (x + y) * 255 // 1400)
                           for y in range(500) for x in range(900)])
            photo.save(path)
            for scale in (1, 1.5, 2):
                with self.subTest(scale=scale):
                    screen = Image.new('RGB', (int(800 * scale), int(600 * scale)), 'white')
                    inner = round(84 * scale)
                    screen.paste(photo.resize((inner, inner)), (round(17 * scale), round(350 * scale)))
                    label = dict(name='上传图片', x=100 + 127 * scale,
                                 y=50 + 397 * scale, width=56 * scale, height=16 * scale)
                    def capture(with_origin=False):
                        buffer = io.BytesIO()
                        screen.save(buffer, 'PNG')
                        return buffer.getvalue(), (100, 50)
                    desktop = SimpleNamespace(screenshot=capture)
                    self.assertTrue(matches_uploaded_photo(desktop, [label], str(path)))
                    # Some WeCom versions position the image and add-label
                    # differently; an exposed preview must use its own bounds.
                    displaced_label = dict(label, x=label['x'] + 40 * scale)
                    preview = dict(kind='Image', x=100 + 17 * scale, y=50 + 350 * scale,
                                   width=inner, height=inner)
                    self.assertTrue(matches_uploaded_photo(desktop, [displaced_label, preview], str(path)))
                    screen.paste(photo.transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize((inner, inner)),
                                 (round(17 * scale), round(350 * scale)))
                    self.assertFalse(matches_uploaded_photo(desktop, [label], str(path)))
                    self.assertFalse(matches_uploaded_photo(desktop, [displaced_label, preview], str(path)))

    def test_decorative_thumbnail_matches_only_the_current_photo_at_each_scale(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'approved.png'
            photo = Image.new('RGB', (900, 500))
            photo.putdata([(x * 255 // 900, y * 255 // 500, (x + y) * 255 // 1400)
                           for y in range(500) for x in range(900)])
            photo.save(path)
            for scale in (1, 1.5, 2):
                with self.subTest(scale=scale):
                    screen = Image.new('RGB', (int(800 * scale), int(600 * scale)), 'white')
                    side = round(86 * scale)
                    inner = round(84 * scale)
                    screen.paste(ImageOps.fit(photo, (inner, inner)),
                                 (round(17 * scale), round(350 * scale)))
                    label = dict(name='上传图片', x=100 + 127 * scale,
                                 y=50 + 395 * scale, width=56 * scale, height=20 * scale)
                    def capture(with_origin=False):
                        buffer = io.BytesIO()
                        screen.save(buffer, 'PNG')
                        return buffer.getvalue(), (100, 50)
                    desktop = SimpleNamespace(screenshot=capture)
                    container = dict(label, width=86 * scale, height=86 * scale,
                                     x=100 + 112 * scale, y=50 + 349 * scale)
                    for glyph_height in (20, 16):
                        text = dict(label, height=glyph_height * scale,
                                    y=label['y'] + (20 - glyph_height) / 2 * scale)
                        self.assertTrue(matches_uploaded_photo(desktop, [text, container], str(path)))
                    screen.paste('white', (0, 0, screen.width, screen.height))
                    self.assertFalse(matches_uploaded_photo(desktop, [label], str(path)))
                    screen.paste(ImageOps.fit(photo.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (side, side)),
                                 (round(16 * scale), round(349 * scale)))
                    self.assertFalse(matches_uploaded_photo(desktop, [label], str(path)))
