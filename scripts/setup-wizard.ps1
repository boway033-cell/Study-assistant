# Windows 安装与旧版数据迁移。重复运行会验证已完成步骤并从失败处继续。
param(
    [string]$OldDataDir = "",
    [switch]$NoStart,
    [switch]$CheckOnly,
    [switch]$Unattended
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $root '.setup-state.json'
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$requirements = Join-Path $root 'requirements.txt'
$backendDir = [IO.Path]::GetFullPath((Join-Path $root 'backend'))
$dataDir = [IO.Path]::GetFullPath((Join-Path $backendDir 'data'))
$stageDir = [IO.Path]::GetFullPath((Join-Path $backendDir 'data.migration-staging'))
if (-not $dataDir.StartsWith($backendDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    -not $stageDir.StartsWith($backendDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw '迁移目标不在当前项目 backend 目录内'
}

$state = @{ requirements_hash = ''; frontend_ready = $false; migration_source = ''; migration_started_source = '' }
if (Test-Path -LiteralPath $statePath) {
    try {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        foreach ($key in @('requirements_hash', 'frontend_ready', 'migration_source', 'migration_started_source')) {
            if ($null -ne $saved.$key) { $state[$key] = $saved.$key }
        }
    } catch { Write-Warning '安装状态文件损坏；将重新验证步骤' }
}
function Save-State {
    $temporary = "$statePath.tmp"
    $state | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}
function Run-Checked([string]$label, [scriptblock]$command) {
    Write-Host "[安装] $label"
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$label 失败，退出码 $LASTEXITCODE。修复后重新运行 install.bat 即可继续。" }
}

try {
    Write-Host "学习助手安装向导：$root"
    $basePython = $null
    foreach ($candidate in @('python', 'py')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $arguments = if ($candidate -eq 'py') { @('-3.12') } else { @() }
            $probe = & $candidate @arguments -c 'import sys; print(int(sys.version_info >= (3,12) and sys.maxsize > 2**32))' 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe -eq '1') { $basePython = $candidate; break }
        }
    }
    if (-not (Test-Path -LiteralPath $venvPy) -and -not $basePython) {
        throw '未找到 64 位 Python 3.12+。请安装后重新运行 install.bat。'
    }
    if ($CheckOnly) {
        if (Test-Path -LiteralPath $venvPy) { & $venvPy (Join-Path $root 'scripts\doctor.py') }
        elseif ($basePython) { & $basePython (Join-Path $root 'scripts\doctor.py') }
        exit $LASTEXITCODE
    }
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Run-Checked '创建隔离 Python 环境' { & $basePython @arguments -m venv (Join-Path $root '.venv') }
    }
    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    if ($state.requirements_hash -ne $requirementsHash) {
        Run-Checked '安装 Python 依赖' { & $venvPy -m pip install -r $requirements }
        $state.requirements_hash = $requirementsHash
        Save-State
    }
    $dist = Join-Path $root 'frontend\dist\index.html'
    if (-not (Test-Path -LiteralPath $dist)) {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            throw '源码安装缺少前端构建产物；请安装 Node.js 22+ 后重新运行 install.bat。Release 包不需要 Node.js。'
        }
        Push-Location (Join-Path $root 'frontend')
        try {
            Run-Checked '安装前端依赖' { npm ci }
            Run-Checked '构建前端页面' { npm run build }
        } finally { Pop-Location }
    }
    $state.frontend_ready = $true
    Save-State

    if (-not $OldDataDir -and -not $Unattended -and
        (-not (Test-Path -LiteralPath (Join-Path $dataDir 'study.db')))) {
        Write-Host '如果你有旧版资料，请输入旧版 backend\data 目录的完整路径；直接回车开始全新使用。'
        $OldDataDir = Read-Host '旧数据目录（可留空）'
    }

    if ($OldDataDir) {
        $source = (Resolve-Path -LiteralPath $OldDataDir).Path
        if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw '旧数据目录不能是符号链接，请提供实际 data 目录。'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $source 'study.db'))) {
            throw '旧数据目录必须包含 study.db；请传入旧版本的 backend\data 目录。'
        }
        if ($source.TrimEnd('\') -eq $dataDir.TrimEnd('\')) { throw '旧数据与新数据目录相同，无需迁移' }
        if ($source.StartsWith($dataDir + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $dataDir.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw '旧数据目录与目标目录相互包含；请改用独立的旧版 data 目录。'
        }
        if ($source.StartsWith($stageDir, [StringComparison]::OrdinalIgnoreCase) -or
            $stageDir.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw '旧数据目录不能与迁移暂存目录重叠。'
        }
        $dataExists = Test-Path -LiteralPath $dataDir
        $dataEntries = if ($dataExists) { @(Get-ChildItem -LiteralPath $dataDir -Force) } else { @() }
        $onlyGitkeep = $dataExists -and @($dataEntries | Where-Object Name -ne '.gitkeep').Count -eq 0
        if ($dataExists -and -not $onlyGitkeep) {
            if ($state.migration_source -ne $source) {
                throw '目标 backend\data 已有资料；为防止覆盖，迁移已停止。请先手工检查目标目录。'
            }
        } elseif (-not $dataExists -or $onlyGitkeep) {
            if ((Test-Path -LiteralPath $stageDir) -and $state.migration_started_source -ne $source) {
                throw '发现属于其他迁移的暂存目录；请先检查 backend\data.migration-staging，避免混入旧资料。'
            }
            $state.migration_started_source = $source
            Save-State
            New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
            $files = @(Get-ChildItem -LiteralPath $source -Recurse -File)
            if (-not $files.Count) { throw '旧数据目录为空' }
            if (@(Get-ChildItem -LiteralPath $source -Recurse -Directory |
                Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
                throw '旧数据目录含链接目录；为防止复制越界，迁移已停止。'
            }
            $expected = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            foreach ($file in $files) {
                if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "旧数据含链接文件：$($file.FullName)" }
                $relative = $file.FullName.Substring($source.Length).TrimStart('\')
                [void]$expected.Add($relative)
                $target = [IO.Path]::GetFullPath((Join-Path $stageDir $relative))
                if (-not $target.StartsWith($stageDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '迁移路径越界' }
                New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                if ((Test-Path -LiteralPath $target) -and
                    (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash) { continue }
                $partial = "$target.partial"
                Copy-Item -LiteralPath $file.FullName -Destination $partial -Force
                if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash) {
                    throw "复制校验失败：$relative"
                }
                Move-Item -LiteralPath $partial -Destination $target -Force
            }
            foreach ($staged in @(Get-ChildItem -LiteralPath $stageDir -Recurse -File)) {
                $relative = $staged.FullName.Substring($stageDir.Length).TrimStart('\')
                if ($relative -ne '.gitkeep' -and -not $expected.Contains($relative)) {
                    throw "暂存目录含旧文件：$relative；请先人工检查，未执行迁移。"
                }
            }
            if ($onlyGitkeep) {
                $placeholder = Join-Path $dataDir '.gitkeep'
                if ((Test-Path -LiteralPath $placeholder) -and -not (Test-Path -LiteralPath (Join-Path $stageDir '.gitkeep'))) {
                    Move-Item -LiteralPath $placeholder -Destination (Join-Path $stageDir '.gitkeep')
                }
                if (Test-Path -LiteralPath $placeholder) { Remove-Item -LiteralPath $placeholder }
                # dataDir 只含占位文件且已在项目 backend 内验证；移走占位后为空目录。
                Remove-Item -LiteralPath $dataDir
            }
            Move-Item -LiteralPath $stageDir -Destination $dataDir
            $state.migration_source = $source
            $state.migration_started_source = ''
            Save-State
            Write-Host "[安装] 已迁移 $($files.Count) 个数据文件；旧目录仍保留：$source"
        }
    }
    Run-Checked '检查安装结果' { & $venvPy (Join-Path $root 'scripts\doctor.py') }
    Write-Host '[完成] 安装与诊断通过。'
    if (-not $NoStart) { & (Join-Path $root 'start.bat') }
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
