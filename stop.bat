@echo off
chcp 65001 >nul
echo 正在停止保研复习助手...
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; if ($p) { Stop-Process -Id $p -Force; Write-Host 已停止服务 (PID: $p) } else { Write-Host 服务未在运行 }"
pause