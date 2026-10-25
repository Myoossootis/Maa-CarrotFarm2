$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$uiDir = Join-Path $projectDir '.tmp/maa_ui'
$pythonExe = Join-Path $projectDir '.venv/Scripts/python.exe'
& $pythonExe (Join-Path $projectDir 'tools/prepare_maa_ui.py') --ui-dir $uiDir
if ($LASTEXITCODE -ne 0) { throw 'Maa UI preparation failed' }
$localDotnet = Join-Path $uiDir 'dotnet'
if (Test-Path -LiteralPath (Join-Path $localDotnet 'dotnet.exe')) {
    $env:DOTNET_ROOT = $localDotnet
    $env:DOTNET_ROOT_X64 = $localDotnet
}
# This launcher is user-invoked, so opening the requested UI visibly is expected.
# Automated acceptance must attach to an existing UI and must not call this script.
Start-Process -FilePath (Join-Path $uiDir 'MFAAvalonia.exe') -WorkingDirectory $uiDir
