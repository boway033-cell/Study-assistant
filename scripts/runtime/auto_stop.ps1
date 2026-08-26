# Compatibility wrapper for the workspace-safe runtime stopper.
if ($PSScriptRoot) {
    $baseDir = $PSScriptRoot
} else {
    $baseDir = $PWD.Path
}
$stopperPath = Join-Path -Path $baseDir -ChildPath "stopper.ps1"
if (-not (Test-Path -LiteralPath $stopperPath -PathType Leaf)) {
    Write-Error "stopper.ps1 not found"
    exit 1
}
& $stopperPath
exit $LASTEXITCODE
