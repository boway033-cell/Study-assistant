# 归档兼容入口：统一交由仓库根目录的稳定启动器处理。
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$stableLauncher = Join-Path $root "scripts\runtime\auto_start.ps1"
& $stableLauncher
exit $LASTEXITCODE
