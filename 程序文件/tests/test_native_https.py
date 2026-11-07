import json
import subprocess
import threading
import unittest
import urllib.error
from unittest.mock import patch

from checkin.deepseek import APIError, DeepSeek, NoBalance


class NativeHttpsTests(unittest.TestCase):
    def test_windows_uses_native_directly_and_keeps_key_out_of_arguments(self):
        reply = subprocess.CompletedProcess([], 0, b'{"ok":true}\n200', b'')
        with patch('urllib.request.urlopen') as first, \
             patch('shutil.which', return_value='curl.exe'), \
             patch('subprocess.run', return_value=reply) as native:
            api = DeepSeek('private-test-key')
            self.assertEqual(api.request('responses', {'input': '读后感'}), {'ok': True})
            self.assertEqual(api.request('responses', {'input': '第二次'}), {'ok': True})
        first.assert_not_called()
        self.assertEqual(native.call_count, 2)
        args, kwargs = native.call_args
        self.assertNotIn(api.key, str(args))
        self.assertIn(api.key, kwargs['input'].decode('utf-8'))
        self.assertNotIn('--insecure', args[0])
        self.assertLessEqual(kwargs['timeout'], 95)

    def test_native_402_uses_balance_error_without_printing_secret_or_server_body(self):
        reply = subprocess.CompletedProcess([], 0, b'{"error":"private-test-key"}\n402', b'')
        with patch('shutil.which', return_value='curl.exe'), patch('subprocess.run', return_value=reply):
            api = DeepSeek('private-test-key')
            with self.assertRaises(NoBalance) as caught:
                api.request('responses', {})
        self.assertNotIn(api.key, str(caught.exception))

    def test_regular_http_error_does_not_retry_or_switch_transport(self):
        error = urllib.error.HTTPError('https://api.deepseek.com/responses', 401, 'invalid', {}, None)
        with patch('shutil.which', return_value=None), patch('urllib.request.urlopen', side_effect=error), patch('subprocess.run') as native:
            with self.assertRaisesRegex(APIError, 'key'):
                DeepSeek('private-test-key').request('responses', {})
        native.assert_not_called()

    def test_non_tls_network_failure_does_not_launch_another_transport(self):
        with patch('shutil.which', return_value=None), patch('urllib.request.urlopen', side_effect=TimeoutError()), patch('subprocess.run') as native:
            with self.assertRaises(APIError):
                DeepSeek('private-test-key').request('responses', {})
        native.assert_not_called()

    def test_certificate_error_is_not_bypassed_with_another_transport(self):
        reply = subprocess.CompletedProcess([], 60, b'\n000', b'certificate failed')
        with patch('shutil.which', return_value='curl.exe'), patch('urllib.request.urlopen') as alternative, patch('subprocess.run', return_value=reply) as native:
            with self.assertRaises(APIError):
                DeepSeek('private-test-key').request('responses', {})
        native.assert_called_once()
        alternative.assert_not_called()
        self.assertNotIn('--insecure', native.call_args.args[0])

    def test_receive_disconnect_retries_once_then_returns_success(self):
        replies = [subprocess.CompletedProcess([], 56, b'\n000', b'reset'),
                   subprocess.CompletedProcess([], 0, b'{"ok":true}\n200', b'')]
        with patch('shutil.which', return_value='curl.exe'), patch('subprocess.run', side_effect=replies) as native:
            self.assertEqual(DeepSeek('private-test-key').request('responses', {}), {'ok': True})
        self.assertEqual(native.call_count, 2)

    def test_persistent_disconnect_has_two_attempt_limit(self):
        reply = subprocess.CompletedProcess([], 56, b'\n000', b'reset')
        with patch('shutil.which', return_value='curl.exe'), patch('subprocess.run', return_value=reply) as native:
            with self.assertRaisesRegex(APIError, '56'):
                DeepSeek('private-test-key').request('responses', {})
        self.assertEqual(native.call_count, 2)

    def test_stop_between_attempts_cancels_retry(self):
        cancel = threading.Event()
        def disconnected(*args, **kwargs):
            cancel.set()
            return subprocess.CompletedProcess([], 56, b'\n000', b'reset')
        with patch('shutil.which', return_value='curl.exe'), patch('subprocess.run', side_effect=disconnected) as native:
            with self.assertRaisesRegex(APIError, '停止'):
                DeepSeek('private-test-key', cancel=cancel).request('responses', {})
        native.assert_called_once()


if __name__ == '__main__':
    unittest.main()
