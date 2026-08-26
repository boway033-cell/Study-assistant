@echo off
chcp 65001 >nul
REM 兼容旧入口：仅停止经过工作区路径验证的学习助手进程。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\runtime\stopper.ps1"
exit /b %errorlevel%
