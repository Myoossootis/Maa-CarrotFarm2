$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$uiDir = Join-Path $projectDir '.tmp/maa_ui'
$exe = Join-Path $uiDir 'MFAAvalonia.exe'
$pythonExe = Join-Path $projectDir '.venv/Scripts/python.exe'

function Show-RunningUi {
    $running = Get-Process -Name MFAAvalonia -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe -and $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
    if (-not $running) { return $false }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CarrotUiWindow {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@
    [CarrotUiWindow]::ShowWindow($running.MainWindowHandle, 9) | Out-Null
    [CarrotUiWindow]::SetForegroundWindow($running.MainWindowHandle) | Out-Null
    return $true
}

function Test-UiStamp {
    $stampPath = Join-Path $uiDir '.prepare_stamp.json'
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $stampPath)) { return $false }
    $saved = Get-Content -LiteralPath $stampPath -Raw -Encoding UTF8 | ConvertFrom-Json
    function Same($relative, $recorded) {
        if (-not $recorded) { return $false }
        $item = Get-Item -LiteralPath (Join-Path $projectDir $relative)
        $mtime = [DateTimeOffset]::new($item.LastWriteTimeUtc).ToUnixTimeSeconds()
        return ($item.Length -eq [int64]$recorded.size) -and ($mtime -eq [int64]$recorded.mtime)
    }
    return (Same 'assets\interface.json' $saved.interface) -and
        (Same 'assets\resource\pipeline\carrot_farm.json' $saved.pipeline) -and
        (Same '.venv\Lib\site-packages\maa\bin\MaaFramework.dll' $saved.maafw_dll)
}

if (Show-RunningUi) { return }
if (-not (Test-UiStamp)) {
    & $pythonExe (Join-Path $projectDir 'tools/prepare_maa_ui.py') --ui-dir $uiDir
    if ($LASTEXITCODE -ne 0) { throw 'Maa UI preparation failed' }
}
$localDotnet = Join-Path $uiDir 'dotnet'
if (Test-Path -LiteralPath (Join-Path $localDotnet 'dotnet.exe')) {
    $env:DOTNET_ROOT = $localDotnet
    $env:DOTNET_ROOT_X64 = $localDotnet
}
# This launcher is user-invoked, so opening the requested UI visibly is expected.
# Automated acceptance must attach to an existing UI and must not call this script.
Start-Process -FilePath $exe -WorkingDirectory $uiDir
