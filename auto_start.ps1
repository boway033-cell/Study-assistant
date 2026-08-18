# 学习助手 - 一键稳定启动（后台常驻，无控制台窗口，日志写文件）
$root = "D:\86153\Documents\study-assistant"
$port = 8000
$url = "http://127.0.0.1:" + $port
$pidFile = Join-Path $root "server.pid"
$logFile = Join-Path $root "server.log"
$errFile = Join-Path $root "server.err.log"
$py = Join-Path $root ".venv\Scripts\python.exe"

# 1. 已在运行？直接打开浏览器
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) { Start-Process $url; exit 0 }

# 2. 用 python.exe（隐藏窗口）后台启动 uvicorn
$args = @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',$port)
$proc = Start-Process $py -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errFile -PassThru

# 3. 等待服务就绪（最长 60 秒），就绪后用实际监听 PID 写 PID 文件
$readyPid = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    $p = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1
    if ($p) { $readyPid = $p; break }
    if ($proc.HasExited) { break }
}
if ($readyPid) {
    try { Set-Content -Path $pidFile -Value $readyPid -Encoding ascii } catch {}
} else {
    # 启动失败：可能端口被占或代码错误，把错误日志尾部打出来
    Write-Host '学习助手启动失败，请查看 server.err.log'
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# 4. 打开浏览器
Start-Process $url