@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   Study Assistant - 停止器
echo ==========================================
echo.

REM 查找监听 8000 端口的进程
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    echo 正在停止进程 PID: %%p
    taskkill /PID %%p /T /F >nul 2>&1
)

REM 验证
timeout /t 2 >nul
netstat -ano | findstr ":8000.*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [警告] 进程仍在运行，请手动检查
    netstat -ano | findstr ":8000.*LISTENING"
) else (
    echo.
    echo ==========================================
    echo [OK] 服务已停止
    echo ==========================================
)
echo.
pause
