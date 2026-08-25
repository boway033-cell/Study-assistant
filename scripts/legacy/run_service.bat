@echo off
cd /d "%~dp0\..\.."
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 > server.log 2> server.err.log
