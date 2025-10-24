#Requires -Version 7.0
<#!
用途：自动检查并安装 OnePass Audio 项目所需的 ffmpeg 与 Python 依赖。
前置：已安装 PowerShell 7+，具备 winget 或 chocolatey（至少一个）。
示例用法：
    pwsh -File .\scripts\install_deps.ps1
!>

param()

$ErrorActionPreference = 'Stop'

Write-Host "提示：若脚本因执行策略受限，可运行 'Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force' 后重试。"

function Test-Command {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    return (Get-Command -Name $Name -ErrorAction SilentlyContinue) -ne $null
}

function Get-FFmpegVersionLine {
    if (-not (Test-Command -Name 'ffmpeg')) {
        return $null
    }

    $versionLine = (& ffmpeg -version | Select-String -Pattern '^ffmpeg version' | Select-Object -First 1)
    if ($null -ne $versionLine) {
        return $versionLine.Line
    }

    return 'ffmpeg 已安装'
}

function Ensure-Elevation {
    if (-not $IsWindows) {
        return
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "需要以管理员身份运行当前会话以使用 Chocolatey 安装 ffmpeg。"
    }
}

function Ensure-FFmpeg {
    Write-Host "==> 检查 ffmpeg ..."
    if (Test-Command -Name 'ffmpeg') {
        $versionLine = Get-FFmpegVersionLine
        if ($null -ne $versionLine) {
            Write-Host "ffmpeg 已安装：$versionLine"
        } else {
            Write-Host "ffmpeg 已安装。"
        }
        return
    }

    if (Test-Command -Name 'winget') {
        Write-Host "未检测到 ffmpeg，尝试使用 winget 安装 ..."
        winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
        if (Test-Command -Name 'ffmpeg') {
            $versionLine = Get-FFmpegVersionLine
            if ($null -ne $versionLine) {
                Write-Host "winget 安装 ffmpeg 成功：$versionLine"
            } else {
                Write-Host "winget 安装 ffmpeg 成功。"
            }
            return
        }

        throw "winget 安装 ffmpeg 后依旧不可用，请手动检查。"
    }

    if (Test-Command -Name 'choco') {
        Write-Host "未检测到 ffmpeg，尝试使用 Chocolatey 安装 ..."
        Ensure-Elevation
        choco install ffmpeg -y
        if (Test-Command -Name 'ffmpeg') {
            $versionLine = Get-FFmpegVersionLine
            if ($null -ne $versionLine) {
                Write-Host "Chocolatey 安装 ffmpeg 成功：$versionLine"
            } else {
                Write-Host "Chocolatey 安装 ffmpeg 成功。"
            }
            return
        }

        throw "Chocolatey 安装 ffmpeg 后依旧不可用，请手动检查。"
    }

    Write-Error "未检测到 winget 或 Chocolatey，无法自动安装 ffmpeg。请访问 https://ffmpeg.org/download.html 手动安装后重试。"
    throw "无法自动安装 ffmpeg"
}

function Ensure-PythonPackages {
    Write-Host "==> 安装 Python 依赖 ..."
    python -m pip install -r requirements.txt
    Write-Host "Python 依赖已安装。"
}

try {
    Ensure-FFmpeg
    Ensure-PythonPackages
    Write-Host "全部步骤完成。"
    exit 0
}
catch {
    Write-Error ("安装脚本执行失败：" + $_.Exception.Message)
    exit 2
}
