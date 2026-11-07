"""Load image search with the installed Edge engine, using a private temporary profile."""
import json
import base64
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import unicodedata
import socket
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .cleanup import validate_photo_directory


def edge_executable():
    for base in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
        value = os.environ.get(base)
        if value:
            path = Path(value) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
            if path.is_file():
                return str(path)
    raise OSError('未找到 Microsoft Edge，请安装 Edge 后重新搜索配图。')


def webdriver_executable():
    driver = Path(__file__).resolve().parents[1] / 'runtime-tools' / 'edgedriver' / 'msedgedriver.exe'
    return driver if driver.is_file() else None


def _is_link_or_junction(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _search_cache(root, create=False):
    photos = validate_photo_directory(root)
    cache = photos / '.edge-search'
    if _is_link_or_junction(cache):
        raise ValueError('Edge 搜图缓存目录是链接，已停止清理。')
    if not cache.exists():
        if not create:
            return None
        cache.mkdir(parents=True, exist_ok=True)
    if not cache.is_dir():
        raise ValueError('Edge 搜图缓存路径不是文件夹，已停止清理。')
    return cache.resolve(strict=True)


def _remove_search_profile(profile, cache):
    cache = Path(os.path.abspath(cache))
    profile = Path(os.path.abspath(profile))
    if not profile.name.startswith('session-') or _is_link_or_junction(profile):
        raise ValueError('Edge 搜图临时目录不符合安全清理范围。')
    if not profile.exists():
        return
    if profile.parent != cache or not profile.is_dir():
        raise ValueError('Edge 搜图临时目录超出安全清理范围。')
    last_error = None
    for attempt in range(6):
        try:
            shutil.rmtree(profile)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            if attempt == 2:
                _release_profile_processes(profile, cache)
            if attempt < 5:
                time.sleep(.25)
    raise OSError('无法清除 Edge 搜图临时文件：' + str(last_error)) from last_error


def _release_profile_processes(profile, cache):
    """Release only Edge processes using this generated private profile.

    The dump-dom launcher can exit before its browser children. A launcher PID
    alone is therefore insufficient to release their cache locks.
    """
    profile, cache = Path(os.path.abspath(profile)), Path(os.path.abspath(cache))
    if (profile.parent != cache or not profile.name.startswith('session-') or
            _is_link_or_junction(profile) or _is_link_or_junction(cache)):
        raise ValueError('搜图进程清理路径不在独立会话内。')
    if os.name != 'nt':
        return
    # Query only matching Edge PIDs; never expose personal Edge command lines.
    literal = str(profile).replace("'", "''")
    source = r'''
$baseProfile = '__PROFILE__'
$allowedProfiles = @($baseProfile, (Join-Path $baseProfile 'webdriver-headless'), (Join-Path $baseProfile 'webdriver-window'))
$owned = @()
foreach ($edgeProcess in @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" -ErrorAction Stop)) {
    $match = [regex]::Match([string]$edgeProcess.CommandLine, '(?:^|\s)(?:"--user-data-dir=([^"]+)"|--user-data-dir="([^"]+)"|--user-data-dir=([^\s"]+))(?=\s|$)')
    if (!$match.Success) { continue }
    $folder = @($match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value) | Where-Object { $_ } | Select-Object -First 1
    try { $folder = [IO.Path]::GetFullPath($folder).TrimEnd('\') } catch { continue }
    if ($allowedProfiles -contains $folder) { $owned += [int]$edgeProcess.ProcessId }
}
ConvertTo-Json -InputObject @($owned) -Compress
'''.replace('__PROFILE__', literal)
    encoded = base64.b64encode(source.encode('utf-16-le')).decode('ascii')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                capture_output=True, timeout=10, creationflags=flags)
        if result.returncode:
            return
        pids = json.loads(result.stdout.decode('utf-8-sig'))
        if not isinstance(pids, list) or not all(type(pid) is int and pid > 0 for pid in pids):
            return
        for pid in set(pids):
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=flags)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        # Deletion still has to succeed below; never silently retain a lock.
        return


def cleanup_stale_search_profiles(root, max_age_seconds=600):
    """Remove only old generated Edge profiles, retaining fresh concurrent sessions."""
    cache = _search_cache(root)
    if cache is None:
        return 0
    cutoff = time.time() - max(0, float(max_age_seconds))
    removed = 0
    for profile in list(cache.iterdir()):
        if (not profile.name.startswith('session-') or _is_link_or_junction(profile)
                or not profile.is_dir()):
            continue
        if profile.stat().st_mtime >= cutoff:
            continue
        _remove_search_profile(profile, cache)
        removed += 1
    return removed


def webdriver_page(address, profile, executable, driver, cancel=None, timeout=40):
    """Render in an owned Edge session through Microsoft's local WebDriver."""
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([str(driver), '--port=' + str(port), '--allowed-ips=127.0.0.1', '--silent'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = 'http://127.0.0.1:' + str(port)
    session = None
    deadline = time.monotonic() + timeout

    def check():
        if cancel is not None and cancel.is_set():
            raise RuntimeError('操作已停止。')
        if time.monotonic() >= deadline:
            raise TimeoutError('Edge WebDriver 搜图超时。')

    def request(method, path, data=None, cleanup=False):
        if not cleanup:
            check()
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, data=payload, method=method,
                                     headers={'Content-Type':'application/json'})
        limit = 3 if cleanup else max(1, min(30, deadline - time.monotonic()))
        try:
            with opener.open(req, timeout=limit) as stream:
                result = json.load(stream).get('value')
        except urllib.error.HTTPError as error:
            try:
                reason = json.load(error).get('value', {}).get('message', '')
            except (ValueError, OSError):
                reason = ''
            raise OSError('Edge WebDriver：' + (str(reason)[:800] or str(error))) from error
        if isinstance(result, dict) and result.get('error'):
            raise OSError(str(result.get('message', result['error']))[:500])
        return result

    try:
        for _ in range(50):
            try:
                if request('GET', '/status').get('ready'):
                    break
            except OSError:
                check()
                time.sleep(.1)
        else:
            raise OSError('微软 Edge WebDriver 未能就绪。')
        capabilities = {'capabilities':{'alwaysMatch':{
            'browserName':'MicrosoftEdge', 'pageLoadStrategy':'eager',
            'ms:edgeOptions':{'binary':str(executable), 'args':[
                '--headless=new', '--no-first-run', '--no-default-browser-check',
                '--lang=zh-CN', '--window-size=1440,1000', '--user-data-dir=' + str(profile / 'webdriver-headless')]}}}}
        try:
            created = request('POST', '/session', capabilities)
        except OSError as error:
            if 'failed to start: crashed' not in str(error):
                raise
            args = capabilities['capabilities']['alwaysMatch']['ms:edgeOptions']['args']
            args.remove('--headless=new')
            args[:] = ['--user-data-dir=' + str(profile / 'webdriver-window')
                       if arg.startswith('--user-data-dir=') else arg for arg in args]
            args.append('--start-minimized')
            created = request('POST', '/session', capabilities)
        session = created['sessionId']
        request('POST', '/session/' + session + '/timeouts', {'pageLoad':25000,'script':10000})
        request('POST', '/session/' + session + '/url', {'url':address})
        while True:
            source = request('GET', '/session/' + session + '/source')
            if not isinstance(source, str) or len(source) > 8_000_000:
                raise ValueError('Edge 返回的页面无效或过大。')
            if address == 'about:blank' or ('sb_form_q' in source and 'iusc' in source):
                return source
            if time.monotonic() >= deadline:
                title = re.search(r'<title[^>]*>(.*?)</title>', source, re.I | re.S)
                raise TimeoutError('Edge 搜图页面未就绪；标题：' +
                                   (title.group(1)[:150] if title else '无') +
                                   '；搜索框：' + str('sb_form_q' in source))
            check()
            time.sleep(.3)
    finally:
        if session:
            try:
                request('DELETE', '/session/' + session, cleanup=True)
            except (OSError, ValueError):
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def render_page(address, root, cancel=None, timeout=40):
    """Render scripts as Edge normally does; never reuse the user's browser profile."""
    executable = edge_executable()
    if cancel is not None and cancel.is_set():
        raise RuntimeError('操作已停止。')
    cache = _search_cache(root, create=True)
    cleanup_stale_search_profiles(root)
    profile = Path(tempfile.mkdtemp(prefix='session-', dir=cache)).resolve(strict=True)
    process = None
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen([
            executable, '--headless=new', '--dump-dom', '--no-first-run',
            '--no-default-browser-check', '--lang=zh-CN', '--window-size=1440,1000',
            '--timeout=25000', '--virtual-time-budget=12000',
            '--user-data-dir=' + str(profile), address,
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise RuntimeError('操作已停止。')
            try:
                data, errors = process.communicate(timeout=.25)
                if process.returncode or not data:
                    driver = webdriver_executable()
                    if driver:
                        return webdriver_page(address, profile, executable, driver, cancel, timeout)
                    diagnostic = (errors or b'').decode('utf-8', 'replace')[-700:]
                    raise OSError(f'Edge 图片搜索进程未返回页面（退出码 {process.returncode}）：{diagnostic}')
                if len(data) > 8_000_000:
                    raise ValueError('Edge 图片搜索页过大，已停止解析。')
                return data.decode('utf-8', 'replace')
            except subprocess.TimeoutExpired:
                continue
        raise TimeoutError('Edge 图片搜索超时，已保留打卡材料。')
    finally:
        if process is not None and process.poll() is None:
            # Terminate only this private Edge process and its own children.
            if os.name == 'nt':
                try:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=5, creationflags=flags)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                process.kill()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        # Never silently leave downloaded images in the private browser profile.
        _remove_search_profile(profile, cache)


class ImageCards(HTMLParser):
    """Read actual Bing image cards, excluding unrelated recommendation metadata."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.query = None
        self.cards = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'sb_form_q':
            self.query = attrs.get('value', '')
        if tag != 'a' or 'iusc' not in attrs.get('class', '').split():
            return
        try:
            item = json.loads(attrs.get('m', ''))
        except (ValueError, TypeError):
            return
        if not isinstance(item, dict):
            return
        url = item.get('murl')
        if not isinstance(url, str) or url in self.seen:
            return
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
                return
        except ValueError:
            return
        self.seen.add(url)
        thumbnail = item.get('turl')
        self.cards.append(dict(url=url, thumbnail=thumbnail if isinstance(thumbnail, str) else '', title=str(item.get('t') or ''),
                               page=str(item.get('purl') or ''), search_engine='Edge/Bing'))


def query_key(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).translate(
        str.maketrans('', '', '\"“”')).split()).casefold()


def iter_image_results(keyword, root, cancel=None):
    query = urllib.parse.urlencode({'q': keyword, 'setlang': 'zh-hans', 'mkt': 'zh-CN'})
    address = 'https://www.bing.com/images/search?' + query
    found, seen = [], set()
    for page in range(2):
        try:
            for attempt in range(2):
                try:
                    source = render_page(address, root, cancel)
                    parsed = ImageCards()
                    parsed.feed(source)
                    if parsed.query is None:
                        raise OSError('Edge 搜索框尚未加载，可能是网络错误或验证页面。')
                    if query_key(parsed.query) != query_key(keyword):
                        raise ValueError('Edge 搜索页的关键词与本条主题不一致，拒绝使用该页图片。')
                    if not parsed.cards:
                        raise OSError('Edge 搜索页未加载图片卡片，保留材料后重试。')
                    break
                except (OSError, ValueError, TimeoutError):
                    if attempt == 1 or (page > 0 and found):
                        raise
            for item in parsed.cards:
                if item['url'] not in seen:
                    seen.add(item['url'])
                    found.append(item)
                    yield item
                    if len(found) >= 50:
                        return
            if len(found) >= 50 or not parsed.cards:
                break
        except (OSError, ValueError, TimeoutError):
            if page == 0:
                raise
            break
        address = ('https://www.bing.com/images/search?' + query
                   + '&first=' + str(max(36, len(found) + 1)))


def image_results(keyword, root, cancel=None):
    return list(iter_image_results(keyword, root, cancel))
