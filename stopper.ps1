# Study Assistant - Stop Service
$port = 8000
$p = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
if ($p) {
    # Kill process tree
    taskkill /PID $p /T /F 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    # Verify
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[WARNING] Process still running"
    } else {
        Write-Host "[OK] Service stopped"
    }
} else {
    Write-Host "Service not running"
}
Start-Sleep -Seconds 2
