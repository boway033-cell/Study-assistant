@echo off
chcp 65001 >nul
REM 兼容旧入口：统一使用会校验 API 版本并选择备用端口的启动器。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\runtime\auto_start.ps1"
exit /b %errorlevel%
