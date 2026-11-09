import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from checkin.images import ImageFinder, ImageSearchUnavailable, public_url


class ImageTransportTests(unittest.TestCase):
    def test_fake_ip_dns_is_reported_as_transport_failure(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.3.49', 443))]
        with patch('checkin.images.socket.getaddrinfo', return_value=answers):
            with self.assertRaisesRegex(ImageSearchUnavailable, '代理'):
                public_url('https://example.com/cover.jpg')
            public_url('https://example.com/cover.jpg', allow_fake_ip_dns=True)

    def test_all_failed_downloads_cannot_be_mistaken_for_no_matching_image(self):
        candidates = [dict(url='https://example.com/cover.jpg', title='封面', page='https://example.com')]
        with tempfile.TemporaryDirectory() as directory:
            finder = ImageFinder(Mock(), Path(directory), threading.Event(), Mock())
            with patch('checkin.images.results', return_value=candidates), patch(
                    'checkin.images.download', side_effect=URLError('connection reset')):
                with self.assertRaisesRegex(ImageSearchUnavailable, '下载'):
                    finder.choose('弘文之星', '实践论', 'a' * 32)
            finder.api.review_image.assert_not_called()
