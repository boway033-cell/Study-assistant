@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

echo.
echo   ==========================================
echo     Study Assistant - Starting...
echo   ==========================================
echo.

REM 1. Check virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [Error] Failed to create virtual environment.
        echo         Please install Python 3.12+ from https://python.org
        pause
        exit /b 1
    )
)

REM 2. Check dependencies
".venv\Scripts\python.exe" -c "import fastapi, pymupdf, jieba" >nul 2>&1
if errorlevel 1 (
    echo [Setup] Installing dependencies (1-3 min)...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [Error] Dependency installation failed. Check your network.
        pause
        exit /b 1
    )
)

REM 3. Launch
echo [Start] Launching application...
echo.
"%CD%\.venv\Scripts\python.exe" launcher.py %1

echo.
echo Application stopped.
pause
