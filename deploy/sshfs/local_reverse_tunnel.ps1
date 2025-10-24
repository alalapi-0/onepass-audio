<#!
.SYNOPSIS
    通过反向 SSH 隧道让远端 VPS 直连本地 OpenSSH Server，并可选创建音频目录的联结。
.DESCRIPTION
    读取与脚本同目录下的 sshfs.env，确保所需变量存在；必要时在用户主目录中创建目录联结，
    随后启动 ``ssh -N`` 反向隧道，保持心跳。执行失败时返回码 2。
.PARAMETER DryRun
    仅打印将要执行的操作，而不真正创建联结或启动隧道。
#>
param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info($Message) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] [信息] $Message"
}

function Write-Ok($Message) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] [完成] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] [警告] $Message" -ForegroundColor Yellow
}

function Write-Err($Message) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] [错误] $Message" -ForegroundColor Red
}

function Load-Env($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "缺少配置文件：$Path"
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Split('#', 2)[0].Trim()
        if (-not $line) { return }
        $parts = $line.Split('=', 2)
        if ($parts.Count -ne 2) { return }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not $name) { return }
        $env:$name = $value
    }
}

try {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $envPath = Join-Path $scriptDir 'sshfs.env'
    Load-Env -Path $envPath

    $required = @(
        'LOCAL_HOME',
        'LOCAL_AUDIO',
        'LOCAL_JUNCTION_NAME',
        'VPS_HOST',
        'VPS_USER',
        'VPS_SSH_KEY',
        'REVERSE_SSHD_PORT',
        'KEEPALIVE_SECS'
    )
    foreach ($name in $required) {
        if (-not $env:$name) {
            throw "缺少环境变量 $name，无法继续。"
        }
    }

    $junctionName = $env:LOCAL_JUNCTION_NAME
    if ($junctionName) {
        $junctionPath = Join-Path $env:LOCAL_HOME $junctionName
        $audioPath = $env:LOCAL_AUDIO
        if (-not (Test-Path -LiteralPath $audioPath)) {
            throw "音频目录不存在：$audioPath"
        }
        if (Test-Path -LiteralPath $junctionPath) {
            $item = Get-Item -LiteralPath $junctionPath
            $isReparse = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
            $targetMatches = $false
            if ($isReparse -and $item -is [System.IO.DirectoryInfo]) {
                try {
                    $currentTarget = ($item | Select-Object -ExpandProperty Target)
                    if ($currentTarget) {
                        $resolvedTarget = (Resolve-Path -LiteralPath $currentTarget).Path
                        $resolvedAudio = (Resolve-Path -LiteralPath $audioPath).Path
                        if ($resolvedTarget -eq $resolvedAudio) {
                            $targetMatches = $true
                        }
                    }
                } catch {
                    $targetMatches = $false
                }
            }
            if ($targetMatches) {
                Write-Info "联结已存在且指向 $audioPath，跳过创建。"
            } elseif ($DryRun) {
                Write-Warn "检测到现有路径 $junctionPath，但目标未知（DryRun 模式不修改）。"
            } else {
                throw "路径 $junctionPath 已存在且不是指向 $audioPath 的联结。"
            }
        } elseif ($DryRun) {
            Write-Info "[DryRun] 将创建目录联结：$junctionPath -> $audioPath"
        } else {
            Write-Info "创建目录联结：$junctionPath -> $audioPath"
            New-Item -ItemType Junction -Path $junctionPath -Target $audioPath | Out-Null
            Write-Ok "已创建联结。"
        }
    }

    $sshCmd = @(
        'ssh',
        '-N',
        '-o', "ServerAliveInterval=$($env:KEEPALIVE_SECS)",
        '-o', 'ServerAliveCountMax=3',
        '-i', $env:VPS_SSH_KEY,
        '-R', "$($env:REVERSE_SSHD_PORT):localhost:22",
        "$($env:VPS_USER)@$($env:VPS_HOST)"
    )

    Write-Info "反向隧道监听：$($env:VPS_HOST):$($env:REVERSE_SSHD_PORT)"
    Write-Info '首次使用需启用 Windows OpenSSH Server：'
    Write-Info '  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0'
    Write-Info '  Start-Service sshd; Set-Service -Name sshd -StartupType Automatic'
    Write-Info '  新建防火墙规则允许 TCP 22 入站'

    if ($DryRun) {
        Write-Info "[DryRun] 启动命令：`n  $($sshCmd -join ' ')"
        exit 0
    }

    if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
        throw '未找到 ssh 命令，请安装 OpenSSH 客户端。'
    }

    Write-Info "即将启动反向隧道：`n  $($sshCmd -join ' ')"
    $process = Start-Process -FilePath $sshCmd[0] -ArgumentList $sshCmd[1..($sshCmd.Count-1)] -NoNewWindow -PassThru
    if (-not $process) {
        throw '无法启动 ssh 进程。'
    }
    Write-Ok "ssh 进程已启动（PID=$($process.Id)）。按 Ctrl+C 结束脚本或手动停止 ssh 进程。"
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "ssh 进程退出码：$($process.ExitCode)"
    }
    Write-Ok '反向隧道已结束。'
    exit 0
} catch {
    Write-Err $_
    exit 2
}
