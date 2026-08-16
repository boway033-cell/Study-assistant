@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

if not exist "server.pid" (
    echo [提示] 未找到 server.pid，服务可能不是通过 start.bat 启动的。
    echo 端口 8000 当前占用情况（仅供参考，不会强制结束）：
    powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($p) { Write-Host ('  监听 8000 的 PID: ' + ($p -join ',') + '（如需停止请手动确认）') } else { Write-Host '  端口 8000 当前无监听' }"
    pause
    exit /b 0
)

set /p PID=<server.pid
echo 正在停止学习助手（PID: %PID%）...
taskkill /PID %PID% /T /F >nul 2>&1
if errorlevel 1 (
    echo 进程 %PID% 可能已退出。
) else (
    echo 已停止服务。
)
del server.pid >nul 2>&1
pause
