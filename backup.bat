@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

if not exist "backend\data" (
    echo 未找到 backend\data 目录，无需备份。
    pause
    exit /b 0
)

set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%
set STAMP=%STAMP: =0%
set OUT=backup-%STAMP%.zip

echo 正在备份 backend\data 到 %OUT% ...
powershell -NoProfile -Command "Compress-Archive -Path 'backend\data' -DestinationPath '%OUT%' -Force"
if errorlevel 1 (
    echo 备份失败。
) else (
    echo 备份完成：%OUT%
    echo 位置：%~dp0%OUT%
)
pause
