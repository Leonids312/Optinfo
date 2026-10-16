# Rebuild the simple Optinfo diffraction-ring application icon using native drawing.
Add-Type -AssemblyName System.Drawing
$iconRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function New-OptinfoIcon([int]$size) {
    $bitmap = [System.Drawing.Bitmap]::new($size, $size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#257568'))
    foreach ($fraction in @(0.30, 0.53, 0.76)) {
        $diameter = $size * $fraction
        $offset = ($size - $diameter) / 2
        $pen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#F7F8F4'), [single]($size * 0.025))
        $graphics.DrawEllipse($pen, [single]$offset, [single]$offset, [single]$diameter, [single]$diameter)
        $pen.Dispose()
    }
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $graphics.FillEllipse($brush, [single]($size * 0.44), [single]($size * 0.44), [single]($size * 0.12), [single]($size * 0.12))
    $brush.Dispose()
    $graphics.Dispose()
    return $bitmap
}
function Save-OptinfoPng([string]$path, [int]$size) {
    $bitmap = New-OptinfoIcon $size
    $bitmap.Save((Join-Path $iconRoot $path), [System.Drawing.Imaging.ImageFormat]::Png)
    $bitmap.Dispose()
}
foreach ($entry in @(@('mdpi',48), @('hdpi',72), @('xhdpi',96), @('xxhdpi',144), @('xxxhdpi',192))) {
    Save-OptinfoPng "android/app/src/main/res/mipmap-$($entry[0])/ic_launcher.png" $entry[1]
}
Save-OptinfoPng 'web/favicon.png' 32
Save-OptinfoPng 'web/icons/Icon-192.png' 192
Save-OptinfoPng 'web/icons/Icon-512.png' 512
Save-OptinfoPng 'web/icons/Icon-maskable-192.png' 192
Save-OptinfoPng 'web/icons/Icon-maskable-512.png' 512
$sizes = @(16,24,32,48,64,128,256)
$payloads = @()
foreach ($size in $sizes) {
    $bitmap = New-OptinfoIcon $size
    $stream = [System.IO.MemoryStream]::new()
    $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $payloads += ,$stream.ToArray()
    $stream.Dispose()
    $bitmap.Dispose()
}
$iconPath = Join-Path $iconRoot 'windows/runner/resources/app_icon.ico'
$file = [System.IO.File]::Create($iconPath)
$writer = [System.IO.BinaryWriter]::new($file)
$writer.Write([uint16]0)
$writer.Write([uint16]1)
$writer.Write([uint16]$sizes.Length)
$offset = 6 + 16 * $sizes.Length
for ($index = 0; $index -lt $sizes.Length; $index++) {
    $dimension = if ($sizes[$index] -eq 256) { 0 } else { $sizes[$index] }
    $writer.Write([byte]$dimension)
    $writer.Write([byte]$dimension)
    $writer.Write([byte]0)
    $writer.Write([byte]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]32)
    $writer.Write([uint32]$payloads[$index].Length)
    $writer.Write([uint32]$offset)
    $offset += $payloads[$index].Length
}
foreach ($payload in $payloads) { $writer.Write([byte[]]$payload) }
$writer.Dispose()
$file.Dispose()
