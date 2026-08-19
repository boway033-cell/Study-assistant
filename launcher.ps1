# Study Assistant - Stable Launcher
# Uses WMI to create independent process (survives session termination)
$root = $PSScriptRoot
$port = 8000
$url = "http://127.0.0.1:$port"

# 1. Check if already running
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[OK] Service already running"
    Start-Process $url
    Start-Sleep -Seconds 2
    exit 0
}

# 2. Start via WMI (process parent = WmiPrvSE system service, not this session)
$py = Join-Path $root ".venv\Scripts\python.exe"
$cmd = "$py -m uvicorn backend.app.main:app --host 127.0.0.1 --port $port"
Write-Host "Starting service via WMI..."
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd
    CurrentDirectory = $root
}
if ($r.ReturnValue -ne 0) {
    Write-Host "[ERROR] WMI Create failed: $($r.ReturnValue)"
    Start-Sleep -Seconds 3
    exit 1
}
Write-Host "Process created: PID $($r.ProcessId)"

# 3. Wait for port to be ready (max 60 seconds)
Write-Host "Waiting for service..."
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "[OK] Service ready!"
        Start-Process $url
        Start-Sleep -Seconds 2
        exit 0
    }
}

# 4. Timeout - show error log
Write-Host "[ERROR] Service startup timeout"
$logFile = Join-Path $root "server.err.log"
if (Test-Path $logFile) {
    Write-Host "=== Error log (last 10 lines) ==="
    Get-Content $logFile -Tail 10
}
Start-Sleep -Seconds 3
exit 1
