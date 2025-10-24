# OnePass Audio Legacy VPS 接入说明

1. 将你现有的 VPS 自动化/部署项目整个目录复制到 `integrations/vps_legacy/` 下，例如 `integrations/vps_legacy/my-old-vps/`。
2. 在 `deploy/provider.yaml` 中设置 `legacy.project_dir` 字段为你的目录名（如 `my-old-vps`）。如果该目录下只有一个子目录，也可以留空让系统自动探测。
3. 所有密钥、证书、缓存或日志请存放在本地安全位置并确保 `.gitignore` 排除，**不要提交到仓库**。
4. 若旧项目已经提供 `provision`、`upload`、`run`、`fetch` 或 `status` 等脚本，请在 `deploy/provider.yaml` 的 `legacy.hooks.*` 字段中填写对应命令。未配置的步骤会由适配层尝试自动探测常见脚本名称或退回到内置的 ssh/scp 实现。

将旧项目放入后，即可通过 `python scripts/deploy_cli.py provider --set legacy` 切换为适配模式，统一使用新的 CLI 完成部署与 ASR 任务。
