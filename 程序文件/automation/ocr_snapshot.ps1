param([string]$ImagePath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1)
function Await($operation,[Type]$type) { $task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation)); $task.GetAwaiter().GetResult() }
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
try {
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))
if($null -eq $engine) { throw 'Chinese OCR is unavailable' }
$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$words=@(foreach($line in $result.Lines){
 $minX=100000;$minY=100000;$maxX=0;$maxY=0;$lineWords=@()
 foreach($word in $line.Words){$r=$word.BoundingRect;$lineWords+=$word.Text;$minX=[Math]::Min($minX,$r.X);$minY=[Math]::Min($minY,$r.Y);$maxX=[Math]::Max($maxX,$r.X+$r.Width);$maxY=[Math]::Max($maxY,$r.Y+$r.Height);[pscustomobject]@{text=$word.Text;x=$r.X;y=$r.Y;width=$r.Width;height=$r.Height}}
 # Chinese OCR may split a date column into separate numeric/unit words.
 # Emit joined tokens using the original measured bounds, not guessed pixels.
 $parts=@($line.Words)
 for($i=0;$i -lt $parts.Count-1;$i++){
  $a=$parts[$i];$b=$parts[$i+1]
  if($a.Text -match '^\d{1,4}$' -and $b.Text -match '^[年月日]$'){
   $ar=$a.BoundingRect;$br=$b.BoundingRect
   if([Math]::Abs(($ar.Y+$ar.Height/2)-($br.Y+$br.Height/2)) -le 12 -and ($br.X-($ar.X+$ar.Width)) -lt 40){
    $x=[Math]::Min($ar.X,$br.X);$y=[Math]::Min($ar.Y,$br.Y)
    $right=[Math]::Max($ar.X+$ar.Width,$br.X+$br.Width);$bottom=[Math]::Max($ar.Y+$ar.Height,$br.Y+$br.Height)
    [pscustomobject]@{text=($a.Text+$b.Text);x=$x;y=$y;width=($right-$x);height=($bottom-$y)}
   }
  }
 }
 if($lineWords.Count -gt 1){[pscustomobject]@{text=($lineWords -join '');x=$minX;y=$minY;width=($maxX-$minX);height=($maxY-$minY)}}
})
$json=ConvertTo-Json -InputObject @($words) -Depth 3 -Compress
[regex]::Replace($json,'[^\x00-\x7F]',[System.Text.RegularExpressions.MatchEvaluator]{param($m) '\u'+([int][char]$m.Value).ToString('x4')})
} finally { $bitmap.Dispose();$stream.Dispose() }
