# KeyMesh Round 1 脚手架

## 项目简介
KeyMesh 是一个为可达网络环境设计的多节点文件共享同步工具，核心设计围绕 mTLS 安全信道与细粒度共享域控制。本轮（Round 1）仅交付 CLI 脚手架、配置模型与证书生成脚本，确保后续轮次可以在此基础上扩展传输、同步等能力。

## 能力范围（Round 1）
- 提供 `python -m keymesh` 入口，支持 `init`、`check`、`list-shares`、`run`、`add-peer` 子命令（除 `init`/`check`/`list-shares` 外为占位）。
- `config.sample.yaml` 定义多对端、多共享域的配置模型，强调路径归一化与权限校验。
- `scripts/gen-certs.*` 脚本用于生成自签 CA 与节点证书，保证密钥永不入库。
- `keymesh/config.py`、`keymesh/utils/pathing.py`、`keymesh/logging_setup.py` 具备逐行注释，便于后续维护。

## 快速开始
以下步骤假设你已在项目根目录下。

### Linux/macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m keymesh init
bash scripts/gen-certs.sh host-A
cp config.sample.yaml config.yaml
python -m keymesh check
python -m keymesh list-shares
python -m keymesh run
```

### Windows (PowerShell)
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m keymesh init
powershell -ExecutionPolicy Bypass -File .\scripts\gen-certs.ps1 -NodeId host-A
Copy-Item config.sample.yaml config.yaml
python -m keymesh check
python -m keymesh list-shares
python -m keymesh run
```

执行 `init` 后会生成 `data/` 目录结构以及 `.keymeshignore` 示例，并提示阅读 `scripts/post-init-note.txt` 获取下一步操作。`gen-certs.*` 会在 `keys/` 下生成密钥与证书文件（仅存在本地，不纳入版本控制）。`check` 将验证配置文件、证书路径与共享路径。`list-shares` 会输出配置中声明的共享域名称。`run` 在本轮中为占位，输出提示并以状态码 0 结束。

## 平台差异说明
- 证书脚本：Linux/macOS 使用 Bash 脚本并依赖系统可用的 `openssl`；Windows 使用 PowerShell 包装 `openssl`。若未安装 `openssl`，请参考脚本内链接进行安装。
- 路径分隔符：配置中的路径建议使用正斜杠 `/`，工具内部通过 `pathlib` 归一化，可自动适配 Windows 与 Linux。生成的 `.keymeshignore` 示例会根据平台正确创建。
- 权限与文件模式：Windows 与 Linux 的权限模型不同，`init` 命令只进行目录创建，不尝试修改权限；请根据实际需求手动调整。

## 安全说明
- `.gitignore` 默认忽略所有证书、私钥与日志文件，确保敏感材料不会入库。
- 配置加载会对共享路径进行越权校验，防止访问共享根目录以外的路径。
- `security.fingerprint_whitelist` 预留 mTLS 对端指纹校验功能，后续轮次将补全。

## 你已具备内网穿透/VPN 的前提
KeyMesh 默认假设你已具备可达网络（VPN、内网穿透或中继），`peers[].addr` 应填写在该网络中可达的地址与端口。脚手架不会自动建立通道，仅负责配置与校验。

## 后续轮次路线图
- **Round 2**：实现 `run` 主循环、初始握手与心跳机制。
- **Round 3**：增量文件索引、校验与差异检测策略。
- **Round 4**：数据传输、速率控制与断点续传。
- **Round 5**：完善监控、Web UI、日志聚合与自动化部署脚本。

欢迎在本轮基础上逐步迭代，实现一个可靠、安全、可维护的多节点同步平台。
