# Compiles trackhound.iss into a Windows installer. Run it after PyInstaller:
#
#     pyinstaller --noconfirm trackhound.spec
#     powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
#
# The result lands next to the build as dist\Trackhound-X.Y.Z-windows-x64-setup.exe.
# Inno Setup does the compiling; the script finds it, and offers the one command
# that installs it when it is missing.
param(
    # Overrides the version that would otherwise come from trackhound/__init__.py
    [string]$Version
)
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script = Join-Path $root "trackhound.iss"
$build = Join-Path $root "dist\Trackhound\Trackhound.exe"

if (-not (Test-Path $build)) {
    throw "No build to wrap: $build is missing. Run pyinstaller --noconfirm trackhound.spec first."
}

if (-not $Version) {
    # The same single source of truth the release workflow checks the tag against
    $init = Join-Path $root "trackhound\__init__.py"
    $found = Select-String -Path $init -Pattern '__version__ = "([^"]+)"'
    if (-not $found) { throw "No __version__ in $init" }
    $Version = $found.Matches[0].Groups[1].Value
}

$compiler = (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source
if (-not $compiler) {
    $compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe" |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $compiler) {
    throw "Inno Setup 6.3+ not found. Install it with: winget install --id JRSoftware.InnoSetup --silent"
}

Write-Output "Compiling with $compiler"
& $compiler "/DAppVersion=$Version" $script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup exited with $LASTEXITCODE" }

$installer = Join-Path $root "dist\Trackhound-$Version-windows-x64-setup.exe"
if (-not (Test-Path $installer)) { throw "Expected $installer, but it was not written" }
Write-Output ("{0} -> {1:N1} MB" -f $installer, ((Get-Item $installer).Length / 1MB))
