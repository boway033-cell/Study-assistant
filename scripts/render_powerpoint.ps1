param(
    [Parameter(Mandatory=$true)][string]$InputPptx,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [int]$Width = 1600,
    [int]$Height = 900
)

$ErrorActionPreference = 'Stop'
$application = $null
$presentation = $null
try {
    $inputPath = [IO.Path]::GetFullPath($InputPptx)
    $outputPath = [IO.Path]::GetFullPath($OutputDir)
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw 'PPTX file does not exist' }
    New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
    $application = New-Object -ComObject PowerPoint.Application
    try { $application.AutomationSecurity = 3 } catch {}
    $presentation = $application.Presentations.Open($inputPath, -1, 0, 0)
    $presentation.Export($outputPath, 'PNG', $Width, $Height)
}
finally {
    if ($presentation -ne $null) {
        try { $presentation.Close() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    }
    if ($application -ne $null) {
        try { $application.Quit() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
