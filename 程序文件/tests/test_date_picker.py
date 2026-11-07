import io
import unittest

from PIL import Image

from checkin.date_picker import centered_picker_confirm


def picker_image():
    output = io.BytesIO()
    Image.new('L', (600, 400), 255).save(output, 'PNG')
    return output.getvalue()


def picker_nodes(year_y=250, month_y=250, day_y=250):
    def node(name, x, y, width=60, height=20, kind='Text'):
        return dict(name=name, kind=kind, x=x, y=y, width=width, height=height)

    return [
        node('取消', 20, 100, 50, 24, 'Button'),
        node('确认', 520, 100, 50, 24, 'Button'),
        node('2026年', 70, year_y),
        node('10月', 250, month_y),
        node('13日', 430, day_y),
    ]


class CenteredDateFallbackTests(unittest.TestCase):
    def test_returns_confirm_when_target_date_is_centered_in_all_columns(self):
        nodes = picker_nodes()

        confirm = centered_picker_confirm(nodes, picker_image(), (0, 0), '2026-10-13')

        self.assertIs(confirm, nodes[1])

    def test_rejects_target_date_away_from_wheel_center(self):
        nodes = picker_nodes(year_y=210, month_y=210, day_y=210)

        self.assertIsNone(centered_picker_confirm(nodes, picker_image(), (0, 0), '2026-10-13'))

    def test_rejects_misaligned_target_date_columns(self):
        nodes = picker_nodes(day_y=290)

        self.assertIsNone(centered_picker_confirm(nodes, picker_image(), (0, 0), '2026-10-13'))

    def test_requires_all_three_date_components(self):
        nodes = picker_nodes()[:-1]

        self.assertIsNone(centered_picker_confirm(nodes, picker_image(), (0, 0), '2026-10-13'))


if __name__ == '__main__':
    unittest.main()
