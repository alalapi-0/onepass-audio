<#
legacy_adapter.ps1
用途：在 Windows 本地 PowerShell 7 环境中为既有 VPS 项目提供适配层，统一对接 OnePass Audio 的部署流水线。
依赖：PowerShell 7+，可选模块 Microsoft.PowerShell.Utility（提供 ConvertFrom-Yaml）。
示例：
  pwsh -File legacy_adapter.ps1 run_asr -LocalAudio "data/audio" -DryRun
说明：
  * 读取 deploy/provider.yaml 的 legacy 配置确定旧项目路径与 hooks。
  * 若 legacy.project_dir 留空，会尝试在 integrations/vps_legacy/ 下自动探测唯一子目录。
  * 对于未配置 hooks 的步骤，脚本会尝试自动寻找常见脚本名；仍未找到时，可选回退到环境变量驱动的 ssh/scp。
  * 支持 --DryRun 仅打印命令。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('provision', 'upload_audio', 'run_asr', 'fetch_outputs', 'status')]
    [string]$Command,
    [string]$LocalAudio = "",
    [string]$LocalAsrJson = "",
    [string]$AudioPattern = "",
    [string]$Model = "",
    [string]$Language = "",
    [string]$Device = "",
    [string]$Compute = "",
    [int]$Workers = 1,
    [string]$SinceIso = "",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
    Write-Host "[legacy] $Message" -ForegroundColor Cyan
}

function Write-WarnMsg([string]$Message) {
    Write-Warning "[legacy] $Message"
}

function Fail([string]$Message) {
    Write-Error "[legacy] $Message"
    exit 2
}

function Read-ProviderConfig {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = Resolve-Path (Join-Path $scriptRoot '..\..\..')
    $configPath = Join-Path $repoRoot 'deploy/provider.yaml'
    if (-not (Test-Path $configPath)) {
        Fail "未找到配置文件：$configPath"
    }
    $raw = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    try {
        return ConvertFrom-Yaml -Yaml $raw
    }
    catch {
        Fail "解析 YAML 失败：$($_.Exception.Message)"
    }
}

function Resolve-LegacyProject([object]$LegacyConfig, [string]$RepoRoot) {
    $rootRelative = if ($LegacyConfig.root) { [string]$LegacyConfig.root } else { 'integrations/vps_legacy' }
    $rootPath = Resolve-Path (Join-Path $RepoRoot $rootRelative) -ErrorAction Stop
    $projectDir = [string]$LegacyConfig.project_dir
    if ([string]::IsNullOrWhiteSpace($projectDir) -or $projectDir -like '<TO_FILL*') {
        $candidates = Get-ChildItem -LiteralPath $rootPath -Directory | Where-Object { $_.Name -notlike '.*' }
        if ($candidates.Count -eq 0) {
            Fail "legacy.project_dir 未填写且在 $rootPath 下未找到子目录。"
        }
        if ($candidates.Count -gt 1) {
            $names = ($candidates | ForEach-Object { $_.Name }) -join ', '
            Fail "检测到多个候选目录：$names，请在 deploy/provider.yaml 中填写 legacy.project_dir。"
        }
        $projectDir = $candidates[0].Name
    }
    $full = Join-Path $rootPath $projectDir
    if (-not (Test-Path $full)) {
        Fail "未找到 legacy 项目目录：$full"
    }
    return (Resolve-Path $full)
}

function Join-ProjectPath([string]$ProjectRoot, [string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath)) {
        return $null
    }
    $candidate = Join-Path $ProjectRoot $RelativePath
    if (Test-Path $candidate) {
        return (Resolve-Path $candidate)
    }
    return $null
}

function Find-StepScript([string]$ProjectRoot, [string[]]$Candidates) {
    foreach ($name in $Candidates) {
        $resolved = Join-ProjectPath $ProjectRoot $name
        if ($resolved) {
            return $resolved
        }
    }
    return $null
}

function Expand-Template([string]$Template, [hashtable]$Values) {
    $result = $Template
    foreach ($key in $Values.Keys) {
        $placeholder = '{' + $key + '}'
        $result = $result.Replace($placeholder, [string]$Values[$key])
    }
    return $result
}

function Invoke-LegacyCommand([string]$Step, [string]$CommandLine, [string]$WorkingDir, [switch]$DryRun) {
    Write-Info "${Step}: $CommandLine"
    if ($DryRun) {
        Write-Info "dry-run 模式，已跳过执行。"
        return 0
    }
    Push-Location $WorkingDir
    try {
        Invoke-Expression $CommandLine
        if ($LASTEXITCODE -ne $null) {
            return [int]$LASTEXITCODE
        }
        return 0
    }
    catch {
        Write-Error "命令执行失败：$($_.Exception.Message)"
        return 2
    }
    finally {
        Pop-Location
    }
}

function Build-EnvFallback([hashtable]$Values, [string]$ProjectRoot) {
    $target = $env:LEGACY_SSH_TARGET
    if (-not $target) {
        return $null
    }
    $sshOptions = @()
    if ($env:LEGACY_SSH_PORT) {
        $sshOptions += @('-p', $env:LEGACY_SSH_PORT)
    }
    if ($env:LEGACY_SSH_KEY) {
        $sshOptions += @('-i', (Resolve-Path $env:LEGACY_SSH_KEY))
    }
    $sshPrefix = @('ssh') + $sshOptions + @($target)
    $scpPrefix = @('scp') + $sshOptions
    $sshCommand = 'ssh'
    if ($sshOptions.Count -gt 0) {
        $sshCommand = "ssh $($sshOptions -join ' ')"
    }
    return @{ ssh = $sshPrefix; scp = $scpPrefix; target = $target; sshOpt = $sshCommand }
}

$config = Read-ProviderConfig
$repoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\..')
$legacyRoot = Resolve-LegacyProject $config.legacy $repoRoot
$hooks = @{}
if ($config.legacy.hooks) {
    foreach ($key in $config.legacy.hooks.PSObject.Properties.Name) {
        $hooks[$key] = [string]$config.legacy.hooks.$key
    }
}

$remoteRoot = if ($config.common.remote_dir) { [string]$config.common.remote_dir } else { '/home/ubuntu/onepass' }
$values = @{
    local_audio     = if ($LocalAudio) { (Resolve-Path $LocalAudio) } else { '' }
    local_asr_json  = if ($LocalAsrJson) { (Resolve-Path $LocalAsrJson) } else { '' }
    remote_dir      = $remoteRoot
    remote_audio    = Join-Path $remoteRoot 'data/audio'
    remote_asr_json = Join-Path $remoteRoot 'data/asr-json'
    model           = if ($Model) { $Model } else { [string]$config.common.model }
    language        = if ($Language) { $Language } else { [string]$config.common.language }
    device          = if ($Device) { $Device } else { [string]$config.common.device }
    compute         = if ($Compute) { $Compute } else { [string]$config.common.compute }
    workers         = if ($Workers) { $Workers } else { [int]$config.common.workers }
    pattern         = if ($AudioPattern) { $AudioPattern } else { [string]$config.common.audio_pattern }
    since_iso       = $SinceIso
    project_root    = $legacyRoot
}

$fallback = Build-EnvFallback $values $legacyRoot

switch ($Command) {
    'provision' {
        $cmdLine = $null
        if ($hooks['provision']) {
            $cmdLine = Expand-Template $hooks['provision'] $values
        }
        else {
            $script = Find-StepScript $legacyRoot @('scripts/provision.ps1', 'scripts/setup.ps1', 'scripts/init.ps1', 'provision.ps1', 'setup.ps1', 'init.ps1')
            if ($script) {
                if ($script.ToString().ToLower().EndsWith('.ps1')) {
                    $cmdLine = "pwsh -File `"$script`""
                }
                elseif ($script.ToString().ToLower().EndsWith('.sh')) {
                    $cmdLine = "bash `"$script`""
                }
            }
        }
        if (-not $cmdLine) {
            Fail "未找到可执行的 provision 命令，请在 provider.yaml 的 legacy.hooks.provision 中填写。"
        }
        $code = Invoke-LegacyCommand 'provision' $cmdLine $legacyRoot $DryRun
        exit $code
    }
    'upload_audio' {
        $cmdLine = $null
        if ($hooks['upload_audio']) {
            $cmdLine = Expand-Template $hooks['upload_audio'] $values
        }
        else {
            $script = Find-StepScript $legacyRoot @('scripts/upload_audio.ps1', 'scripts/upload.ps1', 'upload_audio.ps1', 'upload.ps1', 'scripts/upload_audio.sh', 'scripts/upload.sh')
            if ($script) {
                if ($script.ToString().ToLower().EndsWith('.ps1')) {
                    $cmdLine = "pwsh -File `"$script`" --src `"$($values.local_audio)`" --dst `"$($values.remote_audio)`""
                }
                elseif ($script.ToString().ToLower().EndsWith('.sh')) {
                    $cmdLine = "bash `"$script`" `"$($values.local_audio)`" `"$($values.remote_audio)`""
                }
            }
            elseif ($fallback) {
                if (-not $values.local_audio) {
                    Fail "未提供本地音频目录。"
                }
                $cmdLine = (
                    @($fallback.scp) +
                    @('-r', "`"$($values.local_audio)`"", "$($fallback.target):`"$($values.remote_audio)`"")
                ) -join ' '
            }
        }
        if (-not $cmdLine) {
            Fail "未找到上传命令，请配置 legacy.hooks.upload_audio 或设置 LEGACY_SSH_TARGET。"
        }
        $code = Invoke-LegacyCommand 'upload_audio' $cmdLine $legacyRoot $DryRun
        exit $code
    }
    'run_asr' {
        $cmdLine = $null
        if ($hooks['run_asr']) {
            $cmdLine = Expand-Template $hooks['run_asr'] $values
        }
        else {
            $script = Find-StepScript $legacyRoot @('scripts/run_asr.ps1', 'scripts/asr.ps1', 'run_asr.ps1', 'scripts/run_asr.sh', 'scripts/asr.sh')
            if ($script) {
                if ($script.ToString().ToLower().EndsWith('.ps1')) {
                    $cmdLine = "pwsh -File `"$script`" --model $($values.model) --device $($values.device) --workers $($values.workers) --pattern `"$($values.pattern)`""
                }
                elseif ($script.ToString().ToLower().EndsWith('.sh')) {
                    $cmdLine = "bash `"$script`" --model $($values.model) --device $($values.device) --workers $($values.workers) --pattern `"$($values.pattern)`""
                }
            }
            elseif ($fallback) {
                $remoteCmd = "cd $($values.remote_dir) && python3 scripts/asr_batch.py --audio-dir data/audio --out-dir data/asr-json --pattern `"$($values.pattern)`" --model $($values.model) --language $($values.language) --device $($values.device) --compute-type $($values.compute) --workers $($values.workers)"
                $cmdLine = ((@($fallback.ssh) + @($remoteCmd)) -join ' ')
            }
        }
        if (-not $cmdLine) {
            Fail "未找到 run_asr 命令，请配置 legacy.hooks.run_asr 或提供 LEGACY_SSH_TARGET。"
        }
        $code = Invoke-LegacyCommand 'run_asr' $cmdLine $legacyRoot $DryRun
        exit $code
    }
    'fetch_outputs' {
        $cmdLine = $null
        if ($hooks['fetch_outputs']) {
            $cmdLine = Expand-Template $hooks['fetch_outputs'] $values
        }
        else {
            $script = Find-StepScript $legacyRoot @('scripts/fetch_outputs.ps1', 'fetch_outputs.ps1', 'scripts/fetch.ps1', 'scripts/fetch_outputs.sh', 'scripts/fetch.sh')
            if ($script) {
                if ($script.ToString().ToLower().EndsWith('.ps1')) {
                    $cmdLine = "pwsh -File `"$script`" --remote `"$($values.remote_asr_json)`" --local `"$($values.local_asr_json)`""
                }
                elseif ($script.ToString().ToLower().EndsWith('.sh')) {
                    $cmdLine = "bash `"$script`" `"$($values.remote_asr_json)`" `"$($values.local_asr_json)`""
                }
            }
            elseif ($fallback) {
                if (-not $values.local_asr_json) {
                    Fail "未提供本地 ASR JSON 目录。"
                }
                $remotePath = "$($fallback.target):`"$($values.remote_asr_json)/`""
                $localPath = "`"$($values.local_asr_json)`""
                $cmdParts = @('rsync', '-avz')
                if ($fallback.sshOpt) {
                    $cmdParts += @('-e', $fallback.sshOpt)
                }
                $cmdParts += @($remotePath, $localPath)
                $cmdLine = ($cmdParts) -join ' '
            }
        }
        if (-not $cmdLine) {
            Fail "未找到 fetch_outputs 命令，请配置 hooks 或设置 LEGACY_SSH_TARGET。"
        }
        $code = Invoke-LegacyCommand 'fetch_outputs' $cmdLine $legacyRoot $DryRun
        exit $code
    }
    'status' {
        $cmdLine = $null
        if ($hooks['status']) {
            $cmdLine = Expand-Template $hooks['status'] $values
        }
        elseif ($fallback) {
            $remoteCmd = "cd $($values.remote_dir) && echo '最近修改文件：' && ls -lt data/asr-json | head -n 5 && echo '' && nvidia-smi || true"
            $cmdLine = ((@($fallback.ssh) + @($remoteCmd)) -join ' ')
        }
        if (-not $cmdLine) {
            Fail "未找到 status 命令，请在 hooks.status 填写或设置 LEGACY_SSH_TARGET。"
        }
        $code = Invoke-LegacyCommand 'status' $cmdLine $legacyRoot $DryRun
        exit $code
    }
}
