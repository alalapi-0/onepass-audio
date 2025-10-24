<#
.SYNOPSIS
    OnePass 云端部署（Vultr）本地网络准备脚本。
.DESCRIPTION
    启动 ssh-agent、加载私钥并对指定实例做连通性探测，兼容 Windows/macOS。
    参数：-PrivateKey (必填) -InstanceIp (可选)。退出码：0=成功，1=警告，2=失败。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$PrivateKey,
    [Parameter(Mandatory=$false)]
    [string]$InstanceIp,
    [Parameter(Mandatory=$false)]
    [string]$User = 'ubuntu'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info($Message) { Write-Host "[信息] $Message" -ForegroundColor Cyan }
function Write-Warn($Message) { Write-Host "[警告] $Message" -ForegroundColor Yellow }
function Write-Err($Message)  { Write-Host "[错误] $Message" -ForegroundColor Red }
function Write-Ok($Message)   { Write-Host "[完成] $Message" -ForegroundColor Green }

$hasWarning = $false

try {
    $resolvedKey = (Resolve-Path -Path $PrivateKey -ErrorAction Stop).Path
    Write-Info "使用私钥：$resolvedKey"
}
catch {
    Write-Err "无法找到私钥 $PrivateKey：$_"
    exit 2
}

$platform = $PSVersionTable.Platform
Write-Info "平台：$platform"

try {
    if ($platform -eq 'Win32NT') {
        $agentSvc = Get-Service -Name 'ssh-agent' -ErrorAction SilentlyContinue
        if ($agentSvc) {
            if ($agentSvc.Status -ne 'Running') {
                Write-Info "启动 ssh-agent 服务…"
                Start-Service ssh-agent -ErrorAction Stop
            }
            Write-Ok "ssh-agent 已启动。"
        }
        else {
            Write-Warn "未找到 ssh-agent 服务，请安装并启用 OpenSSH Client。"
            $hasWarning = $true
        }
        $serverSvc = Get-Service -Name 'sshd' -ErrorAction SilentlyContinue
        if ($serverSvc -and $serverSvc.Status -ne 'Running') {
            $resp = Read-Host '是否启动 sshd 服务? (y/N)'
            if ($resp -match '^(y|Y)') {
                Start-Service sshd -ErrorAction SilentlyContinue
                Write-Ok "sshd 已启动。"
            }
        }
        try {
            New-NetFirewallRule -DisplayName 'OnePass Vultr SSH Outbound' -Direction Outbound -Action Allow -Protocol TCP -RemotePort 22 -Profile Any -ErrorAction Stop | Out-Null
            Write-Ok "已确保防火墙允许出站 22 端口。"
        }
        catch {
            Write-Warn "防火墙规则创建失败（可能需要管理员权限），请手动确认出站 22 端口已放行。"
            $hasWarning = $true
        }
    }
    else {
        $agentInfo = & ssh-agent -s 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "启动 ssh-agent 失败，请手动执行：eval \"\$(ssh-agent -s)\""
            $hasWarning = $true
        }
        else {
            Write-Ok "ssh-agent 已启动。"
            $agentInfo -split "`n" | ForEach-Object { Write-Info $_ }
        }
    }
}
catch {
    Write-Warn "准备 ssh-agent 时遇到问题：$_"
    $hasWarning = $true
}

Write-Info "将私钥加载到 ssh-agent…"
try {
    & ssh-add $resolvedKey 2>&1 | ForEach-Object { Write-Info $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "ssh-add 返回非零状态，请确认私钥权限与口令。"
        $hasWarning = $true
    }
    else {
        Write-Ok "ssh-add 完成。"
    }
}
catch {
    Write-Warn "加载私钥失败：$_"
    $hasWarning = $true
}

if ([string]::IsNullOrWhiteSpace($InstanceIp)) {
    Write-Warn "未提供实例 IP，跳过连通性探测。"
    $hasWarning = $true
}
else {
    Write-Info "使用 ssh -o BatchMode=yes 测试连通性…"
    $attempt = 0
    $success = $false
    while ($attempt -lt 5 -and -not $success) {
        $attempt++
        Write-Info "第 $attempt 次尝试连接 $InstanceIp"
        & ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$User@$InstanceIp" exit 2>&1 | ForEach-Object { Write-Info $_ }
        if ($LASTEXITCODE -eq 0) {
            $success = $true
            Write-Ok "连通性测试成功。"
        }
        else {
            Start-Sleep -Seconds 5
        }
    }
    if (-not $success) {
        Write-Err "无法通过 SSH 连接实例 $InstanceIp，请稍后重试或检查安全组。"
        exit 2
    }
}

if ($hasWarning) { exit 1 }
exit 0
