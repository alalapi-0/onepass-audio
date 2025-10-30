#!/usr/bin/env bash
# 说明：本脚本生成的任何密钥与证书文件仅保存在 ./keys/ 下，并且已被 .gitignore 忽略，绝不会提交到仓库。

set -euo pipefail  # 严格模式，避免静默失败

# 检查 openssl 是否可用，确保后续命令能执行
if ! command -v openssl >/dev/null 2>&1; then
  echo "[ERROR] openssl 未安装，请先安装后再运行。" >&2
  exit 1
fi

NODE_ID="${1:-}"  # 第一个参数为节点 ID，用于生成证书名称
CN="${2:-$NODE_ID}"  # 第二个参数可覆盖证书 CN，默认与节点 ID 相同
DAYS="${3:-825}"  # 第三个参数设置证书有效期天数，默认为 825 天

if [[ -z "${NODE_ID}" ]]; then
  echo "用法: $0 <node-id> [common-name] [days]" >&2
  exit 1
fi

KEYS_DIR="$(pwd)/keys"  # 证书输出目录
mkdir -p "${KEYS_DIR}"  # 创建目录以存放证书

CA_KEY="${KEYS_DIR}/ca.key"  # CA 私钥路径
CA_CRT="${KEYS_DIR}/ca.crt"  # CA 证书路径
NODE_KEY="${KEYS_DIR}/${NODE_ID}.key"  # 节点私钥
NODE_CSR="${KEYS_DIR}/${NODE_ID}.csr"  # 节点 CSR
NODE_CRT="${KEYS_DIR}/${NODE_ID}.crt"  # 节点证书

if [[ -f "${NODE_CRT}" ]]; then
  echo "[INFO] ${NODE_CRT} 已存在，跳过生成以避免覆盖。" >&2
  exit 0
fi

if [[ ! -f "${CA_KEY}" ]]; then
  echo "[INFO] 生成 CA 私钥 ${CA_KEY}"  # 提示生成 CA 私钥
  openssl genrsa -out "${CA_KEY}" 4096  # 创建 4096 位 CA 私钥
else
  echo "[INFO] 检测到已有 CA 私钥，跳过生成。"
fi

if [[ ! -f "${CA_CRT}" ]]; then
  echo "[INFO] 生成自签 CA 证书 ${CA_CRT}"  # 提示生成 CA 证书
  openssl req -x509 -new -nodes -key "${CA_KEY}" \  # 使用 CA 私钥生成自签证书
    -sha256 -days "${DAYS}" \  # 使用 SHA-256 签名并设置有效期
    -subj "/CN=KeyMesh-CA" \  # 指定证书主题
    -out "${CA_CRT}"  # 输出证书
else
  echo "[INFO] 检测到已有 CA 证书，跳过生成。"
fi

if [[ ! -f "${NODE_KEY}" ]]; then
  echo "[INFO] 生成节点私钥 ${NODE_KEY}"  # 提示生成节点私钥
  openssl genrsa -out "${NODE_KEY}" 4096  # 创建节点私钥
else
  echo "[INFO] 节点私钥已存在，跳过生成。"
fi

echo "[INFO] 生成节点证书签名请求 ${NODE_CSR}"  # 提示生成 CSR
openssl req -new -key "${NODE_KEY}" \  # 使用节点私钥
  -subj "/CN=${CN}" \  # 设置节点证书主题
  -out "${NODE_CSR}"  # 输出 CSR

echo "[INFO] 使用 CA 为节点证书签名 ${NODE_CRT}"  # 提示签发证书
openssl x509 -req -in "${NODE_CSR}" \  # 读取 CSR
  -CA "${CA_CRT}" -CAkey "${CA_KEY}" \  # 指定 CA 证书与私钥
  -CAcreateserial -out "${NODE_CRT}" \  # 如有需要生成序列号并输出证书
  -days "${DAYS}" -sha256  # 设置有效期并使用 SHA-256

echo "[INFO] 证书生成完成："  # 输出总结
ls -1 "${NODE_KEY}" "${NODE_CSR}" "${NODE_CRT}"  # 列出生成的文件
