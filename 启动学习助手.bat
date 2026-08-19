@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo   Study Assistant - 启动器
echo ==========================================
echo.

REM 检查服务是否已在运行
netstat -ano | findstr ":8000.*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] 服务已在运行
    start http://127.0.0.1:8000
    echo 浏览器已打开，关闭此窗口不会停止服务
    timeout /t 3 >nul
    exit /b 0
)

echo 正在启动服务...

REM 使用 WMI 创建独立进程（脱离当前会话，父进程为系统服务 WmiPrvSE）
set "PYTHON_PATH=%CD%\.venv\Scripts\python.exe"
set "CMD_LINE=\"!PYTHON_PATH!\" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='!CMD_LINE!';CurrentDirectory='%CD%'}; if ($r.ReturnValue -eq 0) { exit 0 } else { exit 1 }"

if %errorlevel% neq 0 (
    echo [错误] 启动失败
    pause
    exit /b 1
)

echo 等待服务就绪...

REM 等待最多 30 秒
set /a TRIES=0
:WAIT_LOOP
timeout /t 1 >nul
netstat -ano | findstr ":8000.*LISTENING" >nul 2>&1
if %errorlevel%==0 goto READY
set /a TRIES+=1
if %TRIES% lss 30 goto WAIT_LOOP

echo [错误] 启动超时
pause
exit /b 1

:READY
echo.
echo ==========================================
echo [OK] 服务已启动
echo 访问: http://127.0.0.1:8000
echo.
echo 提示:
echo   - 关闭此窗口不会停止服务
echo   - 停止服务请运行 stop.bat
echo   - 关闭电脑时服务会自动停止
echo ==========================================
echo.

start http://127.0.0.1:8000
timeout /t 5 >nul
