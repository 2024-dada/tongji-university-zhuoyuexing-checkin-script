import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from checkin.edge_search import webdriver_page


class WebDriverRenderingTests(unittest.TestCase):
    def test_private_session_renders_page_and_is_closed(self):
        calls = []
        def respond(request, timeout=None):
            calls.append((request.method, request.full_url, json.loads(request.data) if request.data else None))
            if request.full_url.endswith('/status'):
                value = {'ready':True}
            elif request.full_url.endswith('/session'):
                value = {'sessionId':'private'}
            elif request.full_url.endswith('/source'):
                value = '<html><head></head><body></body></html>'
            else:
                value = None
            return io.BytesIO(json.dumps({'value':value}).encode())
        process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as folder:
            with patch('checkin.edge_search.subprocess.Popen',return_value=process), patch('urllib.request.OpenerDirector.open',side_effect=respond):
                page = webdriver_page('about:blank',Path(folder),'edge.exe','driver.exe')
        self.assertIn('<html>',page)
        caps = next(data for method,url,data in calls if method=='POST' and url.endswith('/session'))
        options = caps['capabilities']['alwaysMatch']['ms:edgeOptions']
        self.assertTrue(any(a.startswith('--user-data-dir=') for a in options['args']))
        self.assertFalse(any('ignore-certificate' in a or 'no-sandbox' in a for a in options['args']))
        self.assertTrue(any(method=='DELETE' and url.endswith('/session/private') for method,url,_ in calls))
        process.terminate.assert_called_once()
