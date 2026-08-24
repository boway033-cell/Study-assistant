param(
    [switch]$Apply
)

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$excludedSegments = @(
    "\.git\", "\.venv\", "\.pp-doclayout-venv\", "\node_modules\",
    "\backend\data\", "\frontend\dist\"
)

function Is-SafeTarget([string]$candidate) {
    $resolved = [IO.Path]::GetFullPath($candidate)
    if (-not $resolved.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) {
        return $false
    }
    foreach ($segment in $excludedSegments) {
        if ($resolved.Contains($segment)) { return $false }
    }
    return $true
}

$targets = @()
$targets += Get-ChildItem -LiteralPath $projectRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") -and
        (Is-SafeTarget $_.FullName)
    }
$targets += Get-ChildItem -LiteralPath (Join-Path $projectRoot "backend\tests") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "ui_*.png" -and (Is-SafeTarget $_.FullName) }

$targets = $targets | Sort-Object FullName -Unique
$totalBytes = 0
foreach ($target in $targets) {
    $size = if ($target.PSIsContainer) {
        (Get-ChildItem -LiteralPath $target.FullName -Recurse -Force -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    } else { $target.Length }
    $totalBytes += [long]$size
    Write-Output ("{0}  {1:N2} MB" -f $target.FullName, ($size / 1MB))
}

Write-Output ("Total: {0:N2} MB" -f ($totalBytes / 1MB))
if (-not $Apply) {
    Write-Output "Preview only. Re-run with -Apply to remove these generated files."
    exit 0
}

$failed = @()
foreach ($target in ($targets | Sort-Object { $_.FullName.Length } -Descending)) {
    if (Test-Path -LiteralPath $target.FullName) {
        try {
            Remove-Item -LiteralPath $target.FullName -Recurse -Force -ErrorAction Stop
        } catch {
            $failed += $target.FullName
            Write-Warning ("Skipped: {0} ({1})" -f $target.FullName, $_.Exception.Message)
        }
    }
}
if ($failed.Count -gt 0) {
    Write-Output ("Cleanup completed with {0} skipped target(s)." -f $failed.Count)
} else {
    Write-Output "Cleanup completed."
}
Write-Output "User data, databases, backups, models, environments and frontend dist were preserved."
