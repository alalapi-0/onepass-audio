<#
.SYNOPSIS
    OnePass 云端部署（Vultr）环境自检脚本。
.DESCRIPTION
    检测当前 PowerShell、OpenSSH、ssh-agent 等组件是否就绪，兼容 Windows/macOS。
    本脚本仅打印诊断信息，不做持久性改动；退出码：0=OK，1=警告，2=失败。
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info($Message) { Write-Host "[信息] $Message" -ForegroundColor Cyan }
function Write-Warn($Message) { Write-Host "[警告] $Message" -ForegroundColor Yellow }
function Write-Err($Message)  { Write-Host "[错误] $Message" -ForegroundColor Red }
function Write-Ok($Message)   { Write-Host "[完成] $Message" -ForegroundColor Green }

$hasFailure = $false
$hasWarning = $false

try {
    Write-Info "PowerShell 版本：$($PSVersionTable.PSVersion.ToString())"
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        Write-Err "需要 PowerShell 7+，请安装最新版 PowerShell Core。"
        $hasFailure = $true
    }

    $platform = $PSVersionTable.Platform
    Write-Info "平台：$platform"

    foreach ($cmd in @('ssh','scp')) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            Write-Ok "$cmd 已安装。"
        }
        else {
            Write-Err "未检测到 $cmd，请安装 OpenSSH 客户端。"
            $hasFailure = $true
        }
    }

    if ($platform -eq 'Win32NT') {
        if (Get-Command Get-WindowsCapability -ErrorAction SilentlyContinue) {
            $client = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Client*' }
            if ($null -eq $client -or $client.State -ne 'Installed') {
                Write-Warn "OpenSSH Client 未安装，可执行：Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"
                $hasWarning = $true
            }
            $server = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Server*' }
            if ($server -and $server.State -ne 'Installed') {
                Write-Warn "OpenSSH Server 未安装，若需使用请输入：Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"
            }
        }
        $agentSvc = Get-Service -Name 'ssh-agent' -ErrorAction SilentlyContinue
        if ($agentSvc) {
            Write-Info "ssh-agent 状态：$($agentSvc.Status)"
            if ($agentSvc.Status -ne 'Running') {
                Write-Warn "可运行：Start-Service ssh-agent"
                $hasWarning = $true
            }
        }
        else {
            Write-Warn "未找到 ssh-agent 服务，请确认 OpenSSH 客户端已启用。"
            $hasWarning = $true
        }
        $serverSvc = Get-Service -Name 'sshd' -ErrorAction SilentlyContinue
        if ($serverSvc) {
            Write-Info "sshd 状态：$($serverSvc.Status)"
            if ($serverSvc.Status -ne 'Running') {
                Write-Info "如需启用可执行：Start-Service sshd"
            }
        }
    }
    elseif ($platform -eq 'Unix') {
        if (Get-Command xcode-select -ErrorAction SilentlyContinue) {
            $xcode = & xcode-select -p 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "尚未安装 Xcode Command Line Tools，可运行：xcode-select --install"
                $hasWarning = $true
            }
            else {
                Write-Ok "Xcode Command Line Tools 已就绪：$xcode"
            }
        }
        else {
            Write-Warn "未检测到 xcode-select，若为 macOS 请安装 Xcode Command Line Tools。"
            $hasWarning = $true
        }
    }
}
catch {
    Write-Err "检测过程中出现异常：$_"
    exit 2
}

if ($hasFailure) { exit 2 }
if ($hasWarning) { exit 1 }
exit 0
