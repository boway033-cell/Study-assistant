param([string]$Uri = "study-assistant://open")

$launcher = Join-Path $PSScriptRoot "auto_start.ps1"
if (-not (Test-Path -LiteralPath $launcher)) { exit 1 }

# auto_start.ps1 负责 URI 白名单校验、健康检查、按需启动与浏览器打开。
& powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $launcher -TargetUri $Uri
exit $LASTEXITCODE
