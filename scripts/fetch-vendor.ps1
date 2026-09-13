# Downloads the two tools that Trackhound ships with into vendor\, where
# trackhound.spec picks them up. Run it before building a release:
#
#     powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1
#
# ffmpeg converts to mp3/opus and repackages m4a; deno is the JavaScript engine
# yt-dlp needs to get audio out of YouTube. Both are separate programs that the
# downloader starts as subprocesses.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # the progress bar makes downloads crawl in CI

$vendor = Join-Path $PSScriptRoot "..\vendor"
New-Item -ItemType Directory -Force -Path $vendor | Out-Null
$vendor = (Resolve-Path $vendor).Path
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("trackhound-vendor-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $temp | Out-Null

function Fetch-Tool {
    param($Name, $Url, $Pattern)

    $target = Join-Path $vendor "$Name.exe"
    if (Test-Path $target) {
        Write-Output "$Name.exe is already there, skipping"
        return
    }
    $archive = Join-Path $temp "$Name.zip"
    Write-Output "Downloading $Name from $Url"
    Invoke-WebRequest -Uri $Url -OutFile $archive
    $unpacked = Join-Path $temp $Name
    Expand-Archive -Path $archive -DestinationPath $unpacked -Force
    $found = Get-ChildItem -Path $unpacked -Recurse -Filter $Pattern | Select-Object -First 1
    if (-not $found) { throw "$Pattern not found in $Url" }
    Copy-Item $found.FullName $target
    Write-Output ("  {0} -> {1:N1} MB" -f $target, ($found.Length / 1MB))
}

# GPL build; ffmpeg runs as a separate process, so it stays under its own licence
Fetch-Tool -Name "ffmpeg" -Pattern "ffmpeg.exe" `
    -Url "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
# MIT, redistribution allowed
Fetch-Tool -Name "deno" -Pattern "deno.exe" `
    -Url "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

Remove-Item -Recurse -Force $temp
Get-ChildItem $vendor | Select-Object Name, @{Name = "MB"; Expression = { [math]::Round($_.Length / 1MB, 1) } }
