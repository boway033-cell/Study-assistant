@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

echo ==========================================
echo   保研复习助手 - 启动器（稳健版）
echo ==========================================
echo.

REM 1. 检查虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [首次运行] 正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.11+
        pause
        exit /b 1
    )
)

REM 2. 检查依赖
".venv\Scripts\python.exe" -c "import fastapi, pymupdf, jieba" >nul 2>&1
if errorlevel 1 (
    echo [首次运行] 正在安装依赖（约 1-3 分钟）...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)

REM 3. 交由 launcher.py：自检已在运行/端口占用 → 启动 → 自动开浏览器
echo [3/3] 检查端口并启动服务（已在运行则直接打开浏览器）...
echo.
echo 服务地址: http://127.0.0.1:8000 （如需换端口：start.bat 8001）
"%CD%\.venv\Scripts\python.exe" launcher.py %1

echo.
echo 服务已停止。
pause