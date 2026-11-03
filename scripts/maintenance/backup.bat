@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0\..\.."
rem backup.bat - launch the Python backup script.
rem DATA_DIR resolution, stop checks, staging, ZIP validation live in backup.py.
rem Auto-pause is skipped for automation flags: --no-pause, --quiet.
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "NO_PAUSE="
for %%A in (%*) do (
    if /I "%%~A"=="--no-pause" set "NO_PAUSE=1"
    if /I "%%~A"=="--quiet" set "NO_PAUSE=1"
)
"%PY%" "%~dp0backup.py" %*
set "RC=%ERRORLEVEL%"
if not defined NO_PAUSE pause
exit /b %RC%
