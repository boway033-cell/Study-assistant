# 学习助手 - Windows 按需唤醒入口
# 只在用户打开 start.bat / .url / study-assistant:// 时启动，不设置开机常驻。
param(
    [string]$TargetUri = "",
    [switch]$NoBrowser
)

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$pidFile = Join-Path $root "server.pid"
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$runner = Join-Path $PSScriptRoot "server_runner.py"
$runtimeDir = Join-Path $root "backend\data\runtime"
$runtimeUrlFile = Join-Path $runtimeDir "server.url"
$launchLog = Join-Path $runtimeDir "launcher.log"
$mutex = $null
$hasMutex = $false

function Write-LauncherLog([string]$message) {
    try {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        $line = "{0} {1}{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message, [Environment]::NewLine
        [IO.File]::AppendAllText($launchLog, $line, [Text.Encoding]::UTF8)
    } catch {}
}

function Show-StartupError([string]$message) {
    Write-LauncherLog "ERROR $message"
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($message, 0, "学习助手启动失败", 16)
    } catch {}
}

function Test-StudyAssistant([int]$candidatePort) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$candidatePort/api/health" -TimeoutSec 2
        return $response.status -eq "ok" -and
            $response.app -eq "study-assistant" -and
            [int]$response.api_revision -ge 4 -and
            $response.capabilities.shelves_write -eq $true -and
            $response.capabilities.knowledge_insights -eq $true
    } catch { return $false }
}

function Get-ListeningProcessId([int]$candidatePort) {
    try {
        return Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $candidatePort -State Listen -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty OwningProcess
    } catch { return $null }
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

function Complete-Launch([int]$candidatePort) {
    $readyUrl = Get-StudyAssistantUrl -candidatePort $candidatePort -requestedUri $TargetUri
    $listenerPid = Get-ListeningProcessId $candidatePort
    try {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        if ($listenerPid) { Set-Content -LiteralPath $pidFile -Value $listenerPid -Encoding ascii }
        Set-Content -LiteralPath $runtimeUrlFile -Value $readyUrl -Encoding utf8
    } catch {}
    Write-LauncherLog "READY url=$readyUrl pid=$listenerPid"
    if (-not $NoBrowser) { Start-Process $readyUrl }
}

try {
    # 避免连续双击同时创建两个实例。互斥量仅覆盖启动阶段，服务本身不常驻额外守护进程。
    $mutex = New-Object System.Threading.Mutex($false, "Local\StudyAssistantLauncher")
    try { $hasMutex = $mutex.WaitOne(30000) } catch [System.Threading.AbandonedMutexException] { $hasMutex = $true }
    if (-not $hasMutex) {
        Show-StartupError "另一个启动任务等待超时。请稍后再次打开“学习助手”。"
        exit 1
    }

    # 一次性读取监听快照；逐端口调用 Get-NetTCPConnection 在部分 Windows 机器上会累计十余秒。
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)

    # 复用 8000-8010 中已经健康运行的实例。
    foreach ($candidate in 8000..8010) {
        $existingListener = $listeners | Where-Object { $_.LocalPort -eq $candidate } | Select-Object -First 1
        if ($existingListener -and (Test-StudyAssistant $candidate)) {
            Complete-Launch $candidate
            exit 0
        }
    }

    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $runner)) {
        Show-StartupError "启动文件不完整。请确认项目目录中存在 .venv 和 scripts\runtime\server_runner.py。"
        exit 1
    }

    $port = $null
    $usedPorts = @($listeners | Select-Object -ExpandProperty LocalPort -Unique)
    foreach ($candidate in 8000..8010) {
        if ($usedPorts -notcontains $candidate) {
            $port = $candidate
            break
        }
    }
    if (-not $port) {
        Show-StartupError "本机 8000–8010 端口都被占用，无法安全启动。请关闭占用程序后重试。"
        exit 1
    }

    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Write-LauncherLog "START port=$port root=$root"

    # WMI 创建独立 pythonw 进程，调用协议的浏览器或 PowerShell 退出后服务仍继续运行。
    $commandLine = '"' + $py + '" "' + $runner + '" ' + $port
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $root
    }
    if ($created.ReturnValue -ne 0) {
        Show-StartupError "Windows 无法创建学习助手进程（代码 $($created.ReturnValue)）。"
        exit 1
    }

    # 不依赖包装进程 PID；只以本应用健康检查与真实监听 PID 为准。
    for ($i = 0; $i -lt 75; $i++) {
        Start-Sleep -Seconds 1
        if (Test-StudyAssistant $port) {
            Complete-Launch $port
            exit 0
        }
    }

    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    Show-StartupError "服务在 75 秒内未就绪。诊断记录已写入 backend\data\runtime\launcher.log 和 server.err.log。"
    exit 1
} catch {
    Show-StartupError ("启动过程发生异常：" + $_.Exception.Message)
    exit 1
} finally {
    if ($hasMutex -and $mutex) {
        try { $mutex.ReleaseMutex() } catch {}
    }
    if ($mutex) { $mutex.Dispose() }
}
