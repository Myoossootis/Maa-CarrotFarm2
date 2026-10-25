$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$targets = @(
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot "tools\__pycache__")),
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot "quantity_probe.jsonl")),
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot "assets\resource\image\crops\unlabeled")),
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot "assets\resource\image\crops\named"))
)

# The local UI is a deployed runtime, not disposable screenshots. Preserve
# active cancellation signals and the cycle lock as well.
$temporaryRoot = Join-Path $projectRoot '.tmp'
if (Test-Path -LiteralPath $temporaryRoot) {
    $targets += Get-ChildItem -LiteralPath $temporaryRoot -Force |
        Where-Object { $_.Name -notin @('maa_ui', 'maa_ui_runs', 'maa_ui_cycle.lock') } |
        ForEach-Object { [System.IO.Path]::GetFullPath($_.FullName) }
}

foreach ($target in $targets) {
    if (-not $target.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe cleanup target: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

Write-Output "Development cache cleared."
