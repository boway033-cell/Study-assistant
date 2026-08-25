# 学习助手 - 一键稳定启动（后台常驻，无控制台窗口，日志写文件）
param([string]$TargetUri = "")

$root = $PSScriptRoot
$port = 8000
$pidFile = Join-Path $root "server.pid"
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$runner = Join-Path $root "server_runner.py"

function Test-StudyAssistant([int]$candidatePort) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$candidatePort/api/health" -TimeoutSec 2
        return $response.status -eq "ok" -and
            $response.app -eq "study-assistant" -and
            [int]$response.api_revision -ge 2 -and
            $response.capabilities.shelves_write -eq $true
    } catch { return $false }
}

function Get-StudyAssistantUrl([int]$candidatePort, [string]$requestedUri) {
    $base = "http://127.0.0.1:$candidatePort"
    if (-not $requestedUri -or -not $requestedUri.StartsWith("study-assistant://")) { return $base }
    try {
        $parsed = [Uri]$requestedUri
        if ($parsed.Host -ne "open") { return $base }
        $path = [Uri]::UnescapeDataString($parsed.AbsolutePath)
        if ($path -notmatch '^/[A-Za-z0-9/_-]*$') { return $base }
        $target = $base + $path
        if ($parsed.Query -match '^\?page=(\d{1,6})$') { $target += "?page=$($Matches[1])" }
        return $target
    } catch { return $base }
}

# 1. 复用 8000-8010 中已健康运行的实例；否则寻找空闲端口。
foreach ($candidate in 8000..8010) {
    if (Test-StudyAssistant $candidate) {
        $existingUrl = Get-StudyAssistantUrl -candidatePort $candidate -requestedUri $TargetUri
        Start-Process $existingUrl
        exit 0
    }
}
while ($port -le 8010 -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    $port++
}
if ($port -gt 8010) { exit 1 }
$url = "http://127.0.0.1:$port"

# 2. 由 WMI 创建独立 pythonw 进程，避免协议处理器退出后服务被一起回收。
if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $runner)) { exit 1 }
$commandLine = '"' + $py + '" "' + $runner + '" ' + $port
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine
    CurrentDirectory = $root
}
if ($created.ReturnValue -ne 0) { exit 1 }
$processId = [int]$created.ProcessId

# 3. 等待服务就绪（最长 60 秒），就绪后用实际监听 PID 写 PID 文件
$readyPid = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-StudyAssistant $port) { $readyPid = $processId; break }
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { break }
}
if ($readyPid) {
    try { Set-Content -Path $pidFile -Value $readyPid -Encoding ascii } catch {}
} else {
    # 启动失败：可能端口被占或代码错误。
    Write-Host '学习助手启动失败，请查看 server.err.log'
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# 4. 只有健康检查通过才打开，避免把“拒绝连接”页面交给用户。
if ($readyPid) {
    $readyUrl = Get-StudyAssistantUrl -candidatePort $port -requestedUri $TargetUri
    Start-Process $readyUrl
    exit 0
}
exit 1
