$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectDir '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Project Python runtime is missing. Run setup first.' }
$interface = Get-Content -LiteralPath (Join-Path $projectDir 'assets/interface.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$wantedVersion = [string]$interface.version
$hudScript = Join-Path $projectDir 'tools/farm_hud.py'
$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*$hudScript*" -and $_.CommandLine -like "*--app-version $wantedVersion*" } |
    ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
    Where-Object { $_.MainWindowTitle -eq '萝卜家园自动化' -and $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($running) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CarrotAutomationWindow {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@
    [CarrotAutomationWindow]::ShowWindow($running.MainWindowHandle, 9) | Out-Null
    [CarrotAutomationWindow]::SetForegroundWindow($running.MainWindowHandle) | Out-Null
    return
}
& $pythonExe -X utf8 $hudScript --launch --page autorun
if ($LASTEXITCODE -ne 0) { throw 'Automation UI launch failed.' }
