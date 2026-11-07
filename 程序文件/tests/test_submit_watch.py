"""Exercise the actual Windows OCR watcher using an in-memory test image."""
import base64
import subprocess
import unittest
from pathlib import Path


class SubmitWatchTests(unittest.TestCase):
    def test_chinese_duplicate_toast_without_accessibility_nodes(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'automation' / 'watch_submit.ps1').read_text('utf-8-sig')
        source = source.split("[Console]::WriteLine('READY')")[0]
        source = source.replace(
            '$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)',
            '$root = [pscustomobject]@{Current=[pscustomobject]@{BoundingRectangle=[pscustomobject]@{X=0;Y=0;Width=1000;Height=1000}}}')
        # Replace screen capture with a synthetic toast; never touch the desktop.
        capture = '$graphics.CopyFromScreen([int]($bounds.X + $bounds.Width * .2), [int]($bounds.Y + $bounds.Height * .35), 0, 0, $picture.Size)'
        replacement = "$graphics.Clear([Drawing.Color]::Black); $font=[Drawing.Font]::new('Microsoft YaHei',28); try { $graphics.DrawString('该地点已打卡！',$font,[Drawing.Brushes]::White,40,100) } finally { $font.Dispose() }"
        self.assertIn(capture, source)
        source = source.replace(capture, replacement)
        source += "\n$text=ReadToastPixels; if ($text -notmatch '已打卡') { throw ('OCR mismatch: '+$text) }; Write-Output 'In-memory Chinese toast OCR OK'"
        path = str(root / 'automation' / 'uia_snapshot.ps1').replace("'", "''")
        command = '& {\n' + source + "\n} -WindowHandle 0 -SnapshotPath '" + path + "'"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(command.encode('utf-16-le')).decode('ascii')],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'In-memory Chinese toast OCR OK', result.stdout)
