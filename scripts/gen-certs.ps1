#!/usr/bin/env pwsh
# 说明：本脚本生成的密钥与证书仅存储在 ./keys/ 目录，且已通过 .gitignore 忽略，不会纳入版本控制。
# 如需安装 openssl，可参考：https://slproweb.com/products/Win32OpenSSL.html 或使用 Scoop/Chocolatey 等工具。

param(
    [Parameter(Mandatory = $true)]
    [string]$NodeId,  # 节点 ID，用于文件命名
    [string]$CommonName,  # 可选：证书主题 CN
    [int]$Days = 825  # 证书有效期天数，默认 825 天
)

if (-not $CommonName) {  # 若未提供 CN，则使用节点 ID
    $CommonName = $NodeId
}

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {  # 检查 openssl 是否存在
    Write-Error "未找到 openssl，请先安装后重试。"
    exit 1
}

$KeysDir = Join-Path (Get-Location) "keys"  # 设置证书目录
New-Item -ItemType Directory -Path $KeysDir -Force | Out-Null  # 确保目录存在

$CaKey = Join-Path $KeysDir "ca.key"  # CA 私钥路径
$CaCrt = Join-Path $KeysDir "ca.crt"  # CA 证书路径
$NodeKey = Join-Path $KeysDir "$NodeId.key"  # 节点私钥
$NodeCsr = Join-Path $KeysDir "$NodeId.csr"  # 节点 CSR
$NodeCrt = Join-Path $KeysDir "$NodeId.crt"  # 节点证书

if (Test-Path $NodeCrt) {  # 若节点证书已存在则退出
    Write-Warning "证书 $NodeCrt 已存在，跳过生成。"
    exit 0
}

if (-not (Test-Path $CaKey)) {  # 生成 CA 私钥
    Write-Host "[INFO] 生成 CA 私钥 $CaKey"
    & openssl genrsa -out $CaKey 4096 | Out-Null
} else {
    Write-Host "[INFO] 检测到已有 CA 私钥，跳过。"
}

if (-not (Test-Path $CaCrt)) {  # 生成 CA 自签证书
    Write-Host "[INFO] 生成自签 CA 证书 $CaCrt"
    & openssl req -x509 -new -nodes -key $CaKey -sha256 -days $Days -subj "/CN=KeyMesh-CA" -out $CaCrt | Out-Null
} else {
    Write-Host "[INFO] 检测到已有 CA 证书，跳过。"
}

if (-not (Test-Path $NodeKey)) {  # 生成节点私钥
    Write-Host "[INFO] 生成节点私钥 $NodeKey"
    & openssl genrsa -out $NodeKey 4096 | Out-Null
} else {
    Write-Host "[INFO] 节点私钥已存在，跳过。"
}

Write-Host "[INFO] 生成节点 CSR $NodeCsr"  # 生成 CSR
& openssl req -new -key $NodeKey -subj "/CN=$CommonName" -out $NodeCsr | Out-Null

Write-Host "[INFO] 使用 CA 签发节点证书 $NodeCrt"  # 使用 CA 签发证书
& openssl x509 -req -in $NodeCsr -CA $CaCrt -CAkey $CaKey -CAcreateserial -out $NodeCrt -days $Days -sha256 | Out-Null

Write-Host "[INFO] 证书生成完成："  # 输出生成文件列表
Get-Item $NodeKey, $NodeCsr, $NodeCrt | Select-Object FullName
