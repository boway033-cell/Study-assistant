# 学习助手 - 安全停止
$root = "D:\86153\Documents\study-assistant"
$port = 8000
$pidFile = Join-Path $root "server.pid"

# 优先用 PID 文件 + 进程归属校验
if (Test-Path $pidFile) {
    $pidVal = (Get-Content $pidFile -Raw).Trim()
    $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $pidVal) -ErrorAction SilentlyContinue
    if ($p) {
        if ($p.CommandLine -match "uvicorn|study-assistant") {
            Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Write-Host "已停止学习助手 (PID $pidVal)"
            exit 0
        }
        Write-Host "PID $pidVal 不是学习助手进程，未停止"
        exit 1
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "PID 已失效，清理 PID 文件"
}

# 兜底：按端口找 uvicorn/python 进程
$p = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
if ($p) {
    $pr = Get-Process -Id $p -ErrorAction SilentlyContinue
    if ($pr -and $pr.ProcessName -like "*python*") {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Host "已停止学习助手 (PID $p, 端口 $port)"
    } else {
        Write-Host "端口 $port 被 $($pr.ProcessName) 占用，未停止"
    }
} else {
    Write-Host "学习助手未在运行"
}
Start-Sleep -Seconds 1