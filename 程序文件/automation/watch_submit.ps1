param([long]$WindowHandle, [string]$SnapshotPath, [switch]$WaitForArm)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
# Compile the existing MSAA reader once; avoid repeating full OCR snapshots.
$snapshotSource = Get-Content -LiteralPath $SnapshotPath -Raw -Encoding UTF8
$start = $snapshotSource.IndexOf('Add-Type -AssemblyName Accessibility')
$end = $snapshotSource.IndexOf('$root =')
if ($start -lt 0 -or $end -le $start) { throw 'MSAA reader source not found' }
Invoke-Expression $snapshotSource.Substring($start, $end - $start)
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
# Initialize Chinese OCR before publishing. Toasts are painted on screen but
# some Chromium accessibility providers never expose them as control nodes.
Add-Type -AssemblyName System.Drawing
$ocrSource = Get-Content -LiteralPath (Join-Path (Split-Path $SnapshotPath) 'ocr_snapshot.ps1') -Raw -Encoding UTF8
$ocrStart = $ocrSource.IndexOf('Add-Type -AssemblyName System.Runtime.WindowsRuntime')
$ocrEnd = $ocrSource.IndexOf('$file=Await')
Invoke-Expression $ocrSource.Substring($ocrStart, $ocrEnd - $ocrStart)
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))
if ($null -eq $engine) { throw 'Chinese OCR is unavailable' }
function ReadToastPixels {
    $bounds = $root.Current.BoundingRectangle
    $width = [int]($bounds.Width * .6)
    $height = [int]($bounds.Height * .3)
    $picture = [Drawing.Bitmap]::new($width, $height)
    $graphics = [Drawing.Graphics]::FromImage($picture)
    $memory = [IO.MemoryStream]::new()
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    $writer = [Windows.Storage.Streams.DataWriter]::new($stream)
    $bitmap = $null
    try {
        $graphics.CopyFromScreen([int]($bounds.X + $bounds.Width * .2), [int]($bounds.Y + $bounds.Height * .35), 0, 0, $picture.Size)
        $picture.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
        $writer.WriteBytes($memory.ToArray())
        $null = Await ($writer.StoreAsync()) ([uint32])
        $null = $writer.DetachStream()
        $stream.Seek(0)
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        return ($result.Text -replace '\s','')
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        $writer.Dispose(); $stream.Dispose(); $memory.Dispose(); $graphics.Dispose(); $picture.Dispose()
    }
}
$baseline = if ($WaitForArm) { ReadToastPixels } else { '' }
[Console]::WriteLine('READY')
[Console]::Out.Flush()
if ($WaitForArm) {
    if ([Console]::ReadLine() -ne 'ARM') { exit 1 }
    [Console]::WriteLine('ARMED')
    [Console]::Out.Flush()
}
$watch = [Diagnostics.Stopwatch]::StartNew()
$scan = 0
while ($watch.Elapsed.TotalSeconds -lt 45) {
    $pixelText = ReadToastPixels
    if (($pixelText -ne $baseline) -and $pixelText -match '提交成功|发布成功|打卡成功|已打卡|已经打卡|不能重复提交|请勿重复提交|请勿重复打卡|次数已用完|今日次数不足|登录已过期|请重新登录|提交失败|发布失败|打卡失败') {
        [Console]::WriteLine($pixelText)
        [Console]::Out.Flush()
        exit 0
    }
    $scan++
    # Keep OCR frequent; a complete accessibility walk between every capture
    # can outlast a short toast. MSAA is a periodic supplementary check.
    if ($scan % 10 -eq 0) {
      foreach ($node in [CheckinAccessibility]::Read($WindowHandle)) {
        if ((-not $WaitForArm -or -not $baseline.Contains($node.name)) -and $node.name -match '提交成功|发布成功|打卡成功|已打卡|已经打卡|不能重复提交|请勿重复提交|请勿重复打卡|次数已用完|今日次数不足|登录已过期|请重新登录|提交失败|发布失败|打卡失败') {
            [Console]::WriteLine($node.name)
            [Console]::Out.Flush()
            exit 0
        }
      }
    }
    Start-Sleep -Milliseconds 100
}
