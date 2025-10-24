<#!
.SYNOPSIS
    OnePass 云端部署（Vultr）环境自检脚本。
.DESCRIPTION
    检测 PowerShell、OpenSSH、ssh-agent、包管理器等组件状态。
    本脚本仅执行检查与建议；自动修复由 scripts/auto_fix_env.py 完成。
    退出码：0=全部通过，1=存在警告，2=存在阻塞问题。
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

function Check-Command {
    param(
        [string]$CommandName,
        [string]$DisplayName
    )
    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        Write-Err "未检测到 $DisplayName ($CommandName)。"
        $script:hasFailure = $true
        return $false
    }
    Write-Ok "$DisplayName ($CommandName) 已安装：$($cmd.Source)"
    return $true
}

function Check-Version {
    param(
        [string]$CommandName,
        [string[]]$Arguments
    )
    $output = & $CommandName @Arguments 2>&1
    $exit = $LASTEXITCODE
    $trimmed = ($output | Out-String).Trim()
    if ($exit -eq 0) {
        Write-Info "$CommandName $($Arguments -join ' ') => $trimmed"
    } else {
        $lines = $trimmed -split "`n"
        $preview = ($lines | Select-Object -First 3) -join " "
        Write-Warn "$CommandName $($Arguments -join ' ') 退出码 $exit，输出：$preview"
        $script:hasWarning = $true
    }
}

try {
    Write-Info "PowerShell 版本：$($PSVersionTable.PSVersion.ToString())"
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        Write-Err "需要 PowerShell 7+，请安装最新版 PowerShell (pwsh)。"
        $hasFailure = $true
    }

    $platform = $PSVersionTable.Platform
    Write-Info "平台：$platform"

    if (Check-Command -CommandName 'ssh' -DisplayName 'OpenSSH 客户端 (ssh)') {
        Check-Version -CommandName 'ssh' -Arguments @('-V')
    }
    if (Check-Command -CommandName 'scp' -DisplayName 'OpenSSH 客户端 (scp)') {
        Check-Version -CommandName 'scp' -Arguments @('-V')
    }

    if (Check-Command -CommandName 'rsync' -DisplayName 'rsync') {
        Check-Version -CommandName 'rsync' -Arguments @('--version')
    } else {
        Write-Warn '未检测到 rsync。将回退为 scp，同步效率较低，但不阻塞部署。'
        $hasWarning = $true
    }

    if ($platform -eq 'Win32NT') {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        if (-not $pwshCmd) {
            Write-Warn 'pwsh 未在当前会话中可用。安装后请重启终端。'
            $hasWarning = $true
        }

        if (Get-Command Get-WindowsCapability -ErrorAction SilentlyContinue) {
            foreach ($capName in 'OpenSSH.Client~~~~0.0.1.0','OpenSSH.Server~~~~0.0.1.0') {
                $cap = Get-WindowsCapability -Online -Name $capName -ErrorAction SilentlyContinue
                if ($null -eq $cap) { continue }
                if ($cap.State -ne 'Installed') {
                    Write-Warn "$capName 状态：$($cap.State)。可执行：Add-WindowsCapability -Online -Name $capName"
                    $hasWarning = $true
                }
            }
        }

        foreach ($svc in 'ssh-agent','sshd') {
            $service = Get-Service -Name $svc -ErrorAction SilentlyContinue
            if ($service) {
                Write-Info "$svc 状态：$($service.Status) / 启动类型：$($service.StartType)"
                if ($svc -eq 'ssh-agent' -and $service.Status -ne 'Running') {
                    Write-Warn 'ssh-agent 未运行，可执行：Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent'
                    $hasWarning = $true
                }
            } elseif ($svc -eq 'ssh-agent') {
                Write-Warn '未找到 ssh-agent 服务，请确认已启用 OpenSSH Client 功能。'
                $hasWarning = $true
            }
        }

        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Warn '未检测到 winget (App Installer)。请从 Microsoft Store 安装 "App Installer" 后重试。'
            $hasWarning = $true
        } else {
            Write-Ok 'winget 已可用。'
        }
    }
    elseif ($platform -eq 'Unix') {
        if (Get-Command xcode-select -ErrorAction SilentlyContinue) {
            $null = & xcode-select -p 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn '尚未安装 Xcode Command Line Tools，可运行：xcode-select --install'
                $hasWarning = $true
            } else {
                Write-Ok 'Xcode Command Line Tools 就绪。'
            }
        }

        if (-not (Get-Command brew -ErrorAction SilentlyContinue)) {
            Write-Warn '未检测到 Homebrew。可运行：deploy/bootstrap/ensure_homebrew_macos.sh --yes'
            $hasWarning = $true
        } else {
            Write-Ok "Homebrew 版本：$(brew --version | Select-Object -First 1)"
        }

        $agentSock = $env:SSH_AUTH_SOCK
        if (-not $agentSock) {
            Write-Warn '未检测到 SSH_AUTH_SOCK。可执行：eval "$(ssh-agent -s)" && ssh-add -l'
            $hasWarning = $true
        }
    }
}
catch {
    Write-Err "检测过程中出现异常：$_"
    exit 2
}

if ($hasFailure) {
    Write-Err '检测发现阻塞项。可执行：python scripts/auto_fix_env.py --yes'
    exit 2
}
if ($hasWarning) {
    Write-Warn '检测发现警告项。可执行：python scripts/auto_fix_env.py --yes 尝试修复。'
    exit 1
}
Write-Ok '所有检查均通过。'
exit 0
