<#
deploy_to_vps.ps1
用途：在本地 Windows PowerShell 环境中将 data/audio/ 同步到远程 VPS。
依赖：PowerShell 7+、OpenSSH scp/rsync；配置文件 deploy/vps.env。
示例：
  pwsh -File deploy/deploy_to_vps.ps1 -AudioDir data/audio -RemoteDir /home/ubuntu/onepass/data/audio
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AudioDir,
    [Parameter(Mandatory = $true)]
    [string]$RemoteDir,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
    Write-Error "[deploy] $Message"
    exit 2
}

function Load-EnvFile {
    $configDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $envPath = Join-Path $configDir 'vps.env'
    if (-not (Test-Path $envPath)) {
        Fail "未找到 $envPath，请复制 vps.env.example 并填写连接信息。"
    }
    $result = @{}
    foreach ($lineRaw in Get-Content -LiteralPath $envPath) {
        $line = $lineRaw.Trim()
        if (-not $line) { continue }
        if ($line.StartsWith('#')) { continue }
        $pair = $line.Split('=', 2)
        if ($pair.Length -ne 2) { continue }
        $result[$pair[0]] = $pair[1]
    }
    foreach ($required in @('VPS_HOST', 'VPS_USER')) {
        if (-not $result[$required]) {
            Fail "vps.env 缺少字段：$required"
        }
    }
    return $result
}

function Build-ScpCommand($EnvData, [string]$Source, [string]$Target) {
    $cmd = @('scp', '-r')
    if ($EnvData['VPS_SSH_PORT']) {
        $cmd += @('-P', $EnvData['VPS_SSH_PORT'])
    }
    if ($EnvData['VPS_SSH_KEY']) {
        $cmd += @('-i', (Resolve-Path $EnvData['VPS_SSH_KEY']))
    }
    $cmd += $Source
    $cmd += "$($EnvData['VPS_USER'])@$($EnvData['VPS_HOST']):$Target"
    return $cmd
}

$envData = Load-EnvFile
$audioPath = Resolve-Path $AudioDir
if (-not (Test-Path $audioPath)) {
    Fail "音频目录不存在：$AudioDir"
}
$remote = $RemoteDir

Write-Host "[deploy] 上传目录：$audioPath -> $remote" -ForegroundColor Cyan
$cmd = Build-ScpCommand $envData "$audioPath" $remote
$cmdLine = $cmd -join ' '
Write-Host "[deploy] 命令：$cmdLine" -ForegroundColor DarkCyan
if ($DryRun) {
    Write-Host "[deploy] dry-run 模式，未执行上传。" -ForegroundColor Yellow
    exit 0
}
& $cmd[0] @($cmd[1..($cmd.Length - 1)])
exit $LASTEXITCODE
