@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-wizard.ps1" %*
set "status=%ERRORLEVEL%"
echo.
if not "%status%"=="0" echo 安装未完成。修正提示后再次运行 install.bat 将继续。
pause
exit /b %status%
