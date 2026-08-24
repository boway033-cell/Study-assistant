# Study Assistant - Stop Service (only a verified process owned by this workspace)
$root = $PSScriptRoot
$pidFile = Join-Path $root "server.pid"
$candidateIds = @()
if (Test-Path -LiteralPath $pidFile) {
    $stored = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($stored -match '^\d+$') { $candidateIds += [int]$stored }
}
foreach ($port in 8000..8010) {
    $candidateIds += Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}
$stopped = $false
foreach ($candidateId in ($candidateIds | Sort-Object -Unique)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$candidateId" -ErrorAction SilentlyContinue
    if (-not $processInfo) { continue }
    $owned = $processInfo.CommandLine -like "*$root*server_runner.py*" -or
             ($processInfo.CommandLine -like "*$root*" -and $processInfo.CommandLine -like "*backend.app.main:app*")
    if (-not $owned) { continue }
    Stop-Process -Id $candidateId -Force -ErrorAction SilentlyContinue
    $stopped = $true
}
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
if ($stopped) { Write-Host "[OK] Service stopped" } else { Write-Host "Service not running" }
