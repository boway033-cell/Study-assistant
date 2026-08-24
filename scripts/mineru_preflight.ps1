param(
    [double]$MinimumFreeRamGB = 8,
    [double]$MinimumFreeDiskGB = 30
)

$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$workspaceDrive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($PSScriptRoot).TrimEnd(':','\')) -ErrorAction SilentlyContinue
if (-not $workspaceDrive) { $workspaceDrive = Get-PSDrive -Name D }
$python312 = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
$gpuLine = & nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1
$gpuParts = if ($gpuLine) { $gpuLine -split ',' | ForEach-Object { $_.Trim() } } else { @() }
$freeRamGB = [math]::Round($os.FreePhysicalMemory * 1KB / 1GB, 1)
$totalRamGB = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
$freeDiskGB = [math]::Round($workspaceDrive.Free / 1GB, 1)
$vramGB = if ($gpuParts.Count -ge 2) { [math]::Round(([double]$gpuParts[1]) / 1024, 1) } else { 0 }

$checks = [ordered]@{
    TotalRAM = $totalRamGB -ge 15.5
    FreeRAM = $freeRamGB -ge $MinimumFreeRamGB
    FreeDisk = $freeDiskGB -ge $MinimumFreeDiskGB
    Python312 = [bool]$python312
    PipelineVRAM = $vramGB -ge 4
    LocalVLMVRAM = $vramGB -ge 8
}
[pscustomobject]@{
    ReadyForPipeline = $checks.TotalRAM -and $checks.FreeRAM -and $checks.FreeDisk -and $checks.Python312
    TotalRAM_GB = $totalRamGB
    FreeRAM_GB = $freeRamGB
    FreeDisk_GB = $freeDiskGB
    GPU = if ($gpuParts.Count) { $gpuParts[0] } else { "not detected" }
    VRAM_GB = $vramGB
    Python312 = if ($python312) { $python312 } else { "missing" }
    PipelineGPUCapable = $checks.PipelineVRAM
    LocalVLMCapable = $checks.LocalVLMVRAM
} | Format-List

if (-not $checks.Python312) { Write-Warning "Install Python 3.12 in a separate MinerU environment." }
if (-not $checks.FreeRAM) { Write-Warning "Close memory-heavy applications before starting MinerU." }
if (-not $checks.LocalVLMVRAM) { Write-Warning "Use pipeline; do not deploy the local 8GB+ VLM backend on this GPU." }
