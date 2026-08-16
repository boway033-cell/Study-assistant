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

REM 核对 PID 是否仍属于本项目服务（命令行含 uvicorn 且属本目录），再安全停止
powershell -NoProfile -Command "$pidVal = (Get-Content 'server.pid' -Raw).Trim(); $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $pidVal) -ErrorAction SilentlyContinue; if (-not $p) { Write-Host ('PID ' + $pidVal + ' 已不存在，清理陈旧 PID 文件'); Remove-Item 'server.pid' -Force } elseif ($p.CommandLine -notmatch 'uvicorn|study-assistant') { Write-Host ('PID ' + $pidVal + ' 已不属于本项目（进程: ' + $p.Name + '），拒绝结束以保护其他程序'); } else { Stop-Process -Id $p.ProcessId -Force; Write-Host ('已停止学习助手服务 (PID: ' + $p.ProcessId + ')'); Remove-Item 'server.pid' -Force }"

pause
