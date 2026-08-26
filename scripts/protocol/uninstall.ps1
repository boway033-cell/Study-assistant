$rootKey = "HKCU:\Software\Classes\study-assistant"
if (Test-Path -LiteralPath $rootKey) {
    Remove-Item -LiteralPath $rootKey -Recurse -Force
    Write-Host "已移除 study-assistant:// 协议注册"
} else {
    Write-Host "协议尚未注册"
}
# This script only changes the current user's URL protocol registration.
