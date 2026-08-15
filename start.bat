# 保研复习助手 - 一键启动脚本
# 仅在项目目录内运行，不修改系统其他内容
@echo off
chcp 65001 >nul
cd /d %~dp0

echo ==========================================
echo   保研复习助手 - 启动器
echo ==========================================

REM 检查虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [首次运行] 正在创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.11+
        pause
        exit /b 1
    )
)

REM 检查依赖是否已安装
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

echo.
echo 启动服务: http://127.0.0.1:8000
echo 按 Ctrl+C 停止
echo.
".venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
pause
