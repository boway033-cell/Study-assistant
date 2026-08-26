param([switch]$NoPrompt)

$scheme = "study-assistant"
$rootKey = "HKCU:\Software\Classes\$scheme"
$commandKey = Join-Path $rootKey "shell\open\command"
$handler = Join-Path $PSScriptRoot "handler.ps1"

if (-not (Test-Path -LiteralPath $handler)) {
    throw "找不到协议处理器：$handler"
}

New-Item -Path $commandKey -Force | Out-Null
Set-Item -Path $rootKey -Value "URL:Study Assistant Protocol"
New-ItemProperty -Path $rootKey -Name "URL Protocol" -Value "" -PropertyType String -Force | Out-Null
$command = '"powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $handler + '" "%1"'
Set-Item -Path $commandKey -Value $command

Write-Host "已注册：study-assistant://open"
Write-Host "处理器：$handler"
if (-not $NoPrompt) { Read-Host "按 Enter 关闭" | Out-Null }
