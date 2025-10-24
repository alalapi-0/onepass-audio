<#
fetch_outputs.ps1
用途：从远端 VPS 下载 data/asr-json/ 到本地。
依赖：PowerShell 7+、OpenSSH scp/rsync；配置文件 deploy/vps.env。
示例：
  pwsh -File deploy/fetch_outputs.ps1 -RemoteDir /home/ubuntu/onepass/data/asr-json -LocalDir data/asr-json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteDir,
    [Parameter(Mandatory = $true)]
    [string]$LocalDir,
    [string]$Since,
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

$envData = Load-EnvFile
$localPath = Resolve-Path -LiteralPath $LocalDir -ErrorAction SilentlyContinue
if (-not $localPath) {
    $created = New-Item -ItemType Directory -Path $LocalDir -Force
    $localPath = Resolve-Path -LiteralPath $created.FullName
}
Write-Host "[deploy] 下载目录：$RemoteDir -> $localPath" -ForegroundColor Cyan
if ($Since) {
    Write-Host "[deploy] 提示：当前实现会同步全部文件，Since=$Since 仅用于提示。" -ForegroundColor Yellow
}
$target = "$($envData['VPS_USER'])@$($envData['VPS_HOST'])"
$cmd = @('rsync')
if ($envData['VPS_RSYNC_FLAGS']) {
    $flags = $envData['VPS_RSYNC_FLAGS'].Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)
    $cmd += $flags
} else {
    $cmd += @('-avz')
}
if ($envData['VPS_SSH_KEY'] -or $envData['VPS_SSH_PORT']) {
    $sshOptions = @()
    if ($envData['VPS_SSH_KEY']) {
        $sshOptions += "-i $(Resolve-Path $envData['VPS_SSH_KEY'])"
    }
    if ($envData['VPS_SSH_PORT']) {
        $sshOptions += "-p $($envData['VPS_SSH_PORT'])"
    }
    if ($sshOptions.Count -gt 0) {
        $cmd += @("-e", "ssh $($sshOptions -join ' ')")
    }
}
$cmd += "$target:$RemoteDir/"
$cmd += "$localPath".ToString()
$cmdLine = $cmd -join ' '
Write-Host "[deploy] 命令：$cmdLine" -ForegroundColor DarkCyan
if ($DryRun) {
    Write-Host "[deploy] dry-run 模式，未执行下载。" -ForegroundColor Yellow
    exit 0
}
& $cmd[0] @($cmd[1..($cmd.Length - 1)])
exit $LASTEXITCODE
