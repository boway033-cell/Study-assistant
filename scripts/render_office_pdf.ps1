param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [Parameter(Mandatory=$true)][string]$OutputPdf
)

$ErrorActionPreference = 'Stop'
$application = $null
$document = $null
try {
    $inputPath = [IO.Path]::GetFullPath($InputFile)
    $outputPath = [IO.Path]::GetFullPath($OutputPdf)
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw 'Office file does not exist' }
    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($outputPath)) | Out-Null
    $extension = [IO.Path]::GetExtension($inputPath).ToLowerInvariant()
    if ($extension -eq '.docx') {
        $application = New-Object -ComObject Word.Application
        $application.Visible = $false
        $application.DisplayAlerts = 0
        try { $application.AutomationSecurity = 3 } catch {}
        $document = $application.Documents.Open($inputPath, $false, $true, $false)
        # wdExportFormatPDF / wdFormatPDF 均为 17；先导出，旧版 COM 再回退 SaveAs。
        $document.ExportAsFixedFormat([string]$outputPath, [int]17)
        if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
            $outputRef = [ref]$outputPath
            $formatRef = [ref]17
            $document.SaveAs($outputRef, $formatRef)
        }
    }
    elseif ($extension -eq '.pptx') {
        $application = New-Object -ComObject PowerPoint.Application
        try { $application.AutomationSecurity = 3 } catch {}
        $document = $application.Presentations.Open($inputPath, -1, 0, 0)
        # ppFixedFormatTypePDF = 2；固定格式导出比 SaveAs(32) 跨版本更稳定。
        $document.ExportAsFixedFormat([string]$outputPath, [int]2)
        if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
            $document.SaveAs([string]$outputPath, [int]32)
        }
    }
    else { throw 'Only DOCX and PPTX are supported' }
    for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $outputPath -PathType Leaf); $i++) {
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) { throw 'Office did not create PDF output' }
}
finally {
    if ($document -ne $null) {
        try { $document.Close() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($application -ne $null) {
        try { $application.Quit() } catch {}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
