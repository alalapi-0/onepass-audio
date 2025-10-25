# OnePass Audio — 录完即净，一遍过

本项目是一键生成**去口癖、保留“同句最后一遍”**的干净字幕，并可选按剪辑清单导出干净音频的工具集（MVP）。

## 功能清单（当前与计划）

- 去口癖（可配置词表），流畅断句（SRT/VTT/TXT）
- “同句保留最后一遍、删除前面重录”
- 生成 **EDL**（剪辑清单）与 **Adobe Audition 标记 CSV**
- （可选）按 EDL 一键导出干净音频（后续脚本补上）
- 批处理整本书与汇总报告（后续补上）

## 目录结构

```
onepass/
  config/                 # 配置文件（后续会加入 default_config.json）
  onepass/                # Python 包（后续放模块：loader/retake/clean/segment/edl/writers/markers/aggr/pipeline）
  scripts/                # 命令行脚本（后续放 env_check / retake_keep_last / edl_to_ffmpeg 等）
  data/
    asr-json/             # faster-whisper 的词级时间戳 JSON（不入库）
    audio/                # 原始音频（不入库）
    original_txt/         # 原文 TXT（不入库）
  out/                    # 所有输出产物目录（不入库）
  examples/               # 极小示例（仅文本/JSON，占位，后续补）
```

## 系统要求

- Windows 10/11，PowerShell 7+（跨平台可用 PSCore）
- Python 3.10+
- `ffmpeg` 可执行文件在 PATH（后续提供一键安装脚本）

## 安装步骤

```bash
# 建议在项目根创建虚拟环境（示例）
python -m venv .venv
.\.venv\Scripts\activate

# 安装依赖（将于“依赖与配置”步骤生成 requirements.txt）
python -m pip install -r requirements.txt
```

## 环境自检与一键安装

以下脚本帮助快速核对运行环境并自动安装缺失依赖：

```bash
# 运行环境自检（生成 out/env_report.*）
python scripts/env_check.py

# PowerShell 7+ 一键安装（ffmpeg + Python 依赖）
pwsh -File .\scripts\install_deps.ps1

# 如果遇到执行策略限制，可临时放行当前会话
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

环境自检会在终端输出每项检测结果，并写入 `out/env_report.json` 与 `out/env_report.md`。退出码含义如下：`0=全部 OK`，`1=存在 WARN（如未启用虚拟环境）`，`2=存在 FAIL（如缺少 ffmpeg 或依赖）`。

退出码说明：OK=0 / WARN=1 / FAIL=2。

`scripts/install_deps.ps1` 会优先使用 `winget`（备用：Chocolatey）安装 ffmpeg，并调用 `python -m pip install -r requirements.txt`。该脚本可多次执行，若依赖已满足会提示“已安装”。ffmpeg 是后续音频渲染及切片脚本的基础工具，缺失会导致相关命令无法运行；转写 JSON 建议由 faster-whisper（或兼容工具）生成后放入 `data/asr-json/`。

## 自动修复环境（Windows/macOS）

若想一键修复常见缺失项，可运行：

```bash
python scripts/auto_fix_env.py --yes
```

脚本会根据系统自动选择修复策略：

- **Windows**：检测/安装 PowerShell 7、OpenSSH 客户端（可选开启服务与 ssh-agent）、Git for Windows，以及 MSYS2 + rsync（可选，若失败自动降级为 scp）。依赖 `winget`/App Installer，不会覆盖现有配置。
- **macOS**：检测/安装 Homebrew、Xcode Command Line Tools、OpenSSH、rsync，并尝试启动 `ssh-agent`。如 Homebrew 缺失，会在 `--yes` 下自动调用官方安装脚本。

安装过程可能触发管理员权限（Windows 会弹出 UAC），请按照提示确认。若设备策略禁止安装、或机器处于离线状态，脚本会以 WARN/FAIL 退出并给出下一步建议。

脚本运行完毕会重新执行轻量自检，汇总每项状态（OK/WARN/FAIL）。缺少 rsync 时会自动提示改用 scp，同步性能会受到影响但不阻塞工作流程。

## 主程序使用说明

`onepass_main.py` 提供统一入口，可通过子命令或交互式菜单串联安装、校验、处理、清理与批处理流程。支持的子命令包括：`setup`、`validate`、`process`、`render`、`clean`、`regen`、`batch`、`asr`。

```
python onepass_main.py  # 进入交互式菜单
```

```bash
# 一键安装（需要 PowerShell 7）
python onepass_main.py setup

# 环境自检
python onepass_main.py validate

# 单章处理（力度 60，生成字幕/EDL/标记，不渲染音频）
python onepass_main.py process --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out --aggr 60 --dry-run

# 清理后重新生成单章（保留新产物）
python onepass_main.py regen --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out --aggr 60

# 按 EDL 渲染音频（带轻微 crossfade 与响度归一）
python onepass_main.py render --audio data/audio/001.m4a \
  --edl out/001.keepLast.edl.json --out out/001.clean.wav --xfade --loudnorm

# 批量遍历整本书（先清理 generated，再输出 summary.csv/md）
python onepass_main.py batch --aggr 60 --regen --render
```

直接运行 `python onepass_main.py` 会进入菜单模式，当前提供以下选项：

0. 批量转写音频 → 生成 ASR JSON（统一部署 CLI）
1. 环境自检
X. 一键自动修复环境（缺啥装啥；Windows/macOS）
2. 素材检查
3. 单章处理（去口癖 + 保留最后一遍 + 生成字幕/EDL/标记）
4. 仅渲染音频（按 EDL）
5. 退出
6. 重新生成（清理旧产物后重跑一章）
7. 批量生成（遍历全部章节）
8. 清理产物（按 stem 或全部）
L. 本地批量转写（旧版 CLI）

若 PowerShell 执行策略阻止脚本运行，可临时执行：

```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

子命令依赖关系如下：

- `setup` 调用 `scripts/install_deps.ps1`（步骤 #3）
- `validate` 调用 `scripts/validate_assets.py`（步骤 #5）
- `process` 调用 `scripts/retake_keep_last.py`（步骤 #7）
- `render` 调用 `scripts/edl_to_ffmpeg.py`（步骤 #8）

若对应脚本尚未生成，会显示友好提示而不会直接报错退出。

### 实时进度与日志

所有子命令与核心脚本均会实时输出阶段日志，并原样转发外部进程（如 ffmpeg、whisper-ctranslate2）的标准输出/错误；长时间无日志时，会自动打印心跳提示“仍在运行（已用时 Xs）”。

如需调整显示样式，可使用以下环境变量与参数：

- 关闭颜色输出：
  - Windows PowerShell：`$env:ONEPASS_NO_ANSI=1`
  - Windows CMD：`set ONEPASS_NO_ANSI=1`
- 全局关闭详细日志：`ONEPASS_VERBOSE=0`（或在命令后加 `--quiet`）
- 强制显示详细日志：`ONEPASS_VERBOSE=1`（或在命令后加 `--verbose`，适用于支持该选项的脚本）

## 从音频到 ASR JSON

当原始音频已准备就绪但尚未生成词级 ASR JSON 时，可使用 `scripts/asr_batch.py` 批量调用 whisper-ctranslate2 完成转写。该脚本默认扫描 `data/audio/`，并在 `data/asr-json/` 下生成 `<stem>.json`。运行前需确保已安装 `ffmpeg` 与 `whisper-ctranslate2`（可通过步骤 #3 的 `scripts/install_deps.ps1` 安装）。

常用命令示例：

```bash
# 最简单（CPU）
python scripts/asr_batch.py

# GPU + medium 模型 + 2 并发
python scripts/asr_batch.py --model medium --device cuda --workers 2
```

生成的 JSON 文件会放在 `data/asr-json/<stem>.json`，并与 `data/original_txt/<stem>.txt` 搭配使用。

ASR 工具也已接入主程序与批处理流程：

```bash
python onepass_main.py asr --audio-dir data/audio --out-dir data/asr-json
pwsh -File .\scripts\bulk_process.ps1 -AutoASR
```

主程序子命令 `asr` 会转调用 `scripts/asr_batch.py`，支持常用参数；批处理脚本开启 `-AutoASR` 后会在缺少 JSON 时自动转写音频，再继续执行去口癖、字幕和渲染流程。

## 云端部署（Vultr）向导：四步式

Vultr 云端部署向导将常见的 VPS 创建与接入流程拆解为四个步骤，并集成在 `python onepass_main.py` 的交互式菜单中：

1. **准备配置文件**：复制 `deploy/cloud/vultr/vultr.env.example` 为 `deploy/cloud/vultr/vultr.env`，填写 API Key、区域、实例规格、SSH 私钥路径等信息。切记不要把 `vultr.env` 提交到 Git。
2. **主菜单运行四步式**：在主菜单中选择 “V) 云端部署（Vultr）向导”，依次执行：
   - `1)` 检查本机环境（Python、PowerShell 7、OpenSSH、ssh-agent 等）；
   - `2)` 创建 Vultr VPS（自动上传 SSH 公钥、创建实例并轮询到 active）；
   - `3)` 准备本机接入 VPS（启动 ssh-agent、加载私钥、防火墙放行并自动探测连通性）；
   - `4)` 检查 Vultr 账户中的实例（表格列出，标记当前实例）。
3. **完成后续 ASR 流水线**：实例就绪并通过连通性检测后，可切换到你偏好的 provider（`sync`/`sshfs`/`builtin`）继续上传音频、远端转写与结果回收，例如：

   ```powershell
   # 例：使用 sync provider 的“先同步→远端跑→回收”流程
   python scripts/deploy_cli.py provider --set sync
   python scripts/deploy_cli.py upload_audio
   python scripts/deploy_cli.py run_asr --workers 1
   python scripts/deploy_cli.py fetch_outputs
   python scripts/verify_asr_words.py
   ```

### 快速选择东京可用 GPU 计划

在填写 `deploy/cloud/vultr/vultr.env` 前，可先用以下命令快速查看东京（nrt）地区、Ubuntu 22.04 模板下的可用 GPU 套餐：

```bash
# 固定 nrt + ubuntu-22.04，一键列出可用 GPU 套餐
python deploy/cloud/vultr/cloud_vultr_cli.py plans-nrt

# 自定义更多过滤（例如只看 24GB+ 且 A40/L40S）
python deploy/cloud/vultr/cloud_vultr_cli.py plans --region nrt --os ubuntu-22.04 --family "A40|L40S" --min-vram 24
```

若命令输出空表，说明东京暂时无库存，可切换到 `sgp`、`lax`、`fra` 等其他区域，或稍后再试。

### Quickstart 一步到位

```powershell
# 一步到位（默认：nrt + ubuntu-22.04；先看 plan → 选中 → 创建 → 跑 ASR）
python deploy/cloud/vultr/cloud_vultr_cli.py quickstart --family "A40|L40S" --min-vram 24

# 非交互（默认选列表第 1 项计划、默认进入 watch）
python deploy/cloud/vultr/cloud_vultr_cli.py quickstart --family "A40|L40S" --min-vram 24 --yes

# 指定 profile（例如 24GB 显存的生产跑）
python deploy/cloud/vultr/cloud_vultr_cli.py quickstart --profile prod_24g --family "A40|L40S" --min-vram 24
```

4. **安全提示**：
   - Vultr API Key 仅保存在本地 `deploy/cloud/vultr/vultr.env`，请勿上传或共享；
   - 根据预算及时关停/删除不再使用的实例；`cloud_vultr_cli.py` 支持在列表中确认当前实例信息；
   - 删除实例前请再次确认计费周期，必要时手动在 Vultr 控制台核对费用。

## 云端⇄本地互通桥（一键 ASR）

当实例已经按照四步式向导创建完成后，可直接使用 Vultr CLI 的“一键桥接”能力：自动生成同步配置、增量上传音频、远端批量转写并回收 JSON，最后校验 `segments[].words` 字段。

```powershell
# 自动写入/更新 deploy/sync/sync.env（缺失时）
python deploy/cloud/vultr/cloud_vultr_cli.py write-sync-env

# 上传音频 → 远端词级 ASR → 回收 JSON → 校验 words 字段
python deploy/cloud/vultr/cloud_vultr_cli.py asr-bridge --workers 1 --model medium
```

删除实例也提供快捷命令（包含二次确认与计费提醒）：

```powershell
python deploy/cloud/vultr/cloud_vultr_cli.py delete-current
# 或删除指定 ID
python deploy/cloud/vultr/cloud_vultr_cli.py delete --id <INSTANCE_ID>
```

需要实时观察远端推理日志，可直接在命令行 tail：

```powershell
python deploy/cloud/vultr/cloud_vultr_cli.py tail-log
```

常见排查思路：

- `sync.env` 缺失或连接参数变动：重新执行 `write-sync-env` 或手动校验 `VPS_HOST/VPS_USER/VPS_SSH_KEY/VPS_REMOTE_DIR`；
- 远端不可达/SSH 失败：在主菜单 `V)` 中先执行 `3)` 连通性检测，必要时检查安全组/防火墙；
- 虚拟环境未初始化：通过 `cloud_vultr_cli.py provision` 或 `deploy/remote_provision.sh` 重新部署依赖；
- `verify_asr_words.py` WARN：`asr-bridge` 会给出缺少 `words` 的清单，可按提示重跑或开启 `--overwrite`。

## 稳定同步→远端处理→回收结果（rsync 优先）

当本地音频体积较大、需要多次增量推送至远端 VPS 时，可使用全新的 `sync` provider 构建“一键流水线”：上传音频 → 远端批量转写 → 回收 JSON 与日志 → 本地校验。流程完全基于 PowerShell 7、rsync/scp 与现有脚本，保持幂等，可多次重复执行。

```powershell
# 0) 复制模板并填写连接参数
Copy-Item deploy/sync/sync.env.example deploy/sync/sync.env

# 1) （可选）远端初始化依赖/venv
ssh -i C:\Users\YOU\.ssh\id_rsa ubuntu@VPS "bash -lc 'cd onepass && bash deploy/remote_provision.sh'"

# 2) 同步音频到远端（增量、断点续传）
python scripts/deploy_cli.py provider --set sync
python scripts/deploy_cli.py upload_audio

# 3) 在远端批量生成词级 JSON
python scripts/deploy_cli.py run_asr --workers 1

# 4) 回收 JSON 与日志并校验
python scripts/deploy_cli.py fetch_outputs
python scripts/verify_asr_words.py
```

### FAQ（sync provider）

- **没有 rsync？** 上传/拉取脚本会自动退回 scp（带 `-C` 压缩），但无法断点续传；建议在本地安装 rsync（可随 Git for Windows 或 MinGW 获取）。
- **只想增量同步、不删远端多余文件？** 在上传命令后追加 `--no-delete` 即可：`python scripts/deploy_cli.py upload_audio --no-delete`。
- **运行速度受什么影响？** 推理速度主要由远端 GPU 型号与并发决定，网络 IO 非主要瓶颈。建议首次完成全量同步，后续多为增量传输。
- **脚本在哪里修改连接参数？** 复制 `deploy/sync/sync.env.example` 为 `deploy/sync/sync.env` 后填写 `VPS_HOST`、`VPS_USER`、密钥路径与远端目录。`USE_RSYNC_FIRST=true` 时会优先尝试 rsync。

## 接入你已有的 VPS 项目（provider=legacy）

新版部署流水线通过 `scripts/deploy_cli.py` 统一封装 provision/upload/run/fetch/status 流程，并通过 `deploy/provider.yaml` 切换 builtin（官方 ssh/scp）与 legacy（既有项目适配层）。接入步骤如下：

1. 将旧项目完整复制到 `integrations/vps_legacy/<你的目录>/`，密钥与缓存请留在本地安全位置；
2. 编辑 `deploy/provider.yaml`，在 `legacy.project_dir` 填写你的目录名称（或确保 `integrations/vps_legacy/` 下仅有一个子目录）；
3. 若旧项目提供现成脚本，可在 `legacy.hooks.*` 中填写命令模板；未配置的步骤会尝试自动探测常见脚本名，或在设置 `LEGACY_SSH_TARGET` 等环境变量后降级到内置 ssh/scp；
4. 执行以下命令切换 provider 并按顺序完成部署与转写：

```bash
# 选择 legacy 适配层
python scripts/deploy_cli.py provider --set legacy

# 可按需逐步执行，默认会实时输出远端日志
python scripts/deploy_cli.py provision
python scripts/deploy_cli.py upload_audio
python scripts/deploy_cli.py run_asr --model medium --device auto --workers 1
python scripts/deploy_cli.py fetch_outputs

# 校验 data/asr-json/*.json 是否含 segments[].words[*].start/end
python scripts/verify_asr_words.py
```

在交互式主菜单中，选项 `0)` 将引导你确认当前 provider，按需执行四步流程，并在结束后自动运行 `verify_asr_words.py`。若需要本地直接运行旧版批量转写，可选择隐藏项 `L)`。

常见问题：

- **脚本名称不匹配**：请在 `legacy.hooks.*` 中填写准确命令；
- **权限/SSH 配置**：builtin 与 fallback ssh/scp 会读取 `deploy/vps.env`，注意填写 `VPS_HOST`、`VPS_USER`、密钥路径与端口；
- **PATH/虚拟环境**：可在 `deploy/vps.env` 中设置 `VPS_VENV_ACTIVATE` 或在 legacy 项目脚本内自行处理；
- **后台日志位置**：建议在旧项目脚本里输出 tmux/session 名称，方便排查远端 whisper 进程。

## 远端直读本地音频（SSHFS 反向隧道）

当你希望远端 VPS 直接读取本地 `data/audio/` 而无需上传时，可将 provider 切换为 `sshfs`。流程依赖 Windows OpenSSH Server 与反向 SSH 隧道：

```powershell
# 1) 本地建立反向隧道（首次启用 OpenSSH Server）
pwsh -File .\deploy\sshfs\local_reverse_tunnel.ps1

# 2) 远端挂载你的本地 data/audio
ssh -i C:\Users\ASUS\.ssh\id_rsa ubuntu@your.vps.ip "bash -lc 'cd onepass && bash deploy/sshfs/remote_mount_local.sh'"

# 3) 运行批量转写（远端直接读挂载路径）
python scripts/deploy_cli.py provider --set sshfs
python scripts/deploy_cli.py run_asr --workers 1

# 4) 拉回 JSON 并校验词级时间戳
python scripts/deploy_cli.py fetch_outputs
python scripts/verify_asr_words.py
```

注意事项：

- 本地电脑需保持在线，网络波动可能导致 sshfs 掉线（脚本使用 `reconnect` 选项自动重连）；
- Windows 必须启用 **OpenSSH Server**，并在用户主目录下创建指向 `E:\onepass\data\audio` 的目录联结（`local_reverse_tunnel.ps1` 会自动处理）；
- 反向隧道依赖 `ssh -R` 暴露本地 22 端口，首次需要放行防火墙并启动 `sshd` 服务；
- 如果更偏好“上传到云端再跑”，可执行 `python scripts/deploy_cli.py provider --set builtin` 切换回默认流程。

## 素材准备与验证

素材需按 stem（不含扩展名）对齐放置在 `data/` 目录下，常见示例如下：

```
data/asr-json/001.json       ↔  data/original_txt/001.txt
                               ↘ data/audio/001.m4a  (可选)
```

支持的音频扩展名为：`.m4a`、`.wav`、`.mp3`、`.flac`。音频素材是可选项，仅在需要渲染或试听时补齐即可。

在录入素材后，可运行以下命令生成报告：

```bash
python scripts/validate_assets.py
# 强制要求音频也齐全
python scripts/validate_assets.py --audio-required
```

脚本会生成三份文件（均位于 `out/` 目录）：

- `validate_report.json`：机器可读的完整明细，可供其他工具消费；
- `validate_report.md`：人类可读的 Markdown 总览，含表格与修复建议；
- `validate_summary.csv`：以 `stem, has_json, has_txt, has_audio, ...` 为列的汇总表。

常见问题速查：

- **文件名不一致**：确保 JSON/TXT/音频的文件名（stem）完全一致，例如 `001.json` ↔ `001.txt` ↔ `001.m4a`。
- **目录缺失**：若提示缺少 `data/asr-json/` 或 `data/original_txt/`，请先创建目录再放入素材。
- **只有字幕需求**：如只需导出字幕/标记，可忽略音频缺失警告；若执行 `--audio-required` 则会被视为错误。
- **音频格式不受支持**：请转换为 `.m4a/.wav/.mp3/.flac` 中的一种后再放入 `data/audio/`。

## 单章与批处理用法

### 单章

```bash
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out --aggr 60 --dry-run
```

以上命令会生成字幕（SRT/VTT/TXT）、EDL、Audition 标记及日志，但不会触碰音频；移除 `--dry-run` 即可按配置执行收紧静音等修改。

```bash
# 更激进的句级对齐与去重（对读错但相似的整句也会识别为重复，保留最后一遍）
python scripts/retake_keep_last.py --json data/asr-json/003.json \
  --original data/original_txt/003.txt --outdir out --aggr 60 \
  --align-mode hybrid --align-sim 0.86 --keep last

# 只想保留得分最高的一遍（非常严格的内容对齐）
python scripts/retake_keep_last.py --json data/asr-json/004.json \
  --original data/original_txt/004.txt --outdir out --aggr 50 \
  --align-mode accurate --align-sim 0.90 --keep best
```

执行后会额外生成 `*.diff.md` 差异报告，逐句列出保留的朗读与删除的重录明细，方便人工复核。

### 重新生成与清理

当需要“重跑一章并覆盖旧字幕/日志”时，可先清理旧产物再重新生成。新的 `--regen` 流程会调用 `scripts/clean_outputs.py`，仅处理 `out/` 目录下的衍生文件，默认移动到 `out/.trash/<时间戳>/` 以便随时还原：

```bash
# 在生成前先清理旧产物（安全移动到 .trash/）
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out --aggr 60 --regen

# 仅清理 generated，不重新生成
python scripts/clean_outputs.py --stem 001 --what generated --trash --yes

# 硬删除所有产物（慎用，无法撤销）
python scripts/clean_outputs.py --stem 001 --what all --hard --yes
```

所有操作仅作用于 `out/` 目录，`data/audio`、`data/asr-json`、`data/original_txt` 等源素材不会被触碰。若需要彻底清空回收站，可运行：

```bash
python scripts/clean_outputs.py --all --what all --hard --yes
```

主程序也可一键执行同样的流程：`python onepass_main.py clean ...`、`python onepass_main.py regen ...`，交互式菜单第 6、8 项会提示确认后再调用脚本。

### 快照与回滚

```bash
# 生成快照（冻结当前 out/）
python scripts/snapshot.py --note "after bulk render"

# 仅为 001/002 生成快照（只含生成类，不含 .clean.wav）
python scripts/snapshot.py --stems 001,002 --what generated

# 预演（不落盘）
python scripts/snapshot.py --dry-run

# 回滚到某次快照（所有文件，遇到同名先备份到 .trash/）
python scripts/rollback.py --id 20251024-143012-ab12cd --soft

# 只回滚某几项（强制校验哈希）
python scripts/rollback.py --id 20251024-143012-ab12cd --targets 001,序言01 --verify
```

- 快照会存放在 `out/_snapshots/<run_id>/`，默认优先使用硬链接节省磁盘空间（跨盘或不支持时会自动改为复制）。
- 回滚仅覆盖 `out/` 目录下的产物，不会修改 `data/` 中的源素材；默认启用 `--soft` 会将冲突文件备份到 `out/.trash/rollback-<时间戳>/`。
- 建议不要将 `_snapshots/` 目录提交到版本控制。如需清理旧快照，可运行 `python scripts/clean_outputs.py --all --what all --hard --yes`。
- 主程序新增 `python onepass_main.py snapshot ...` 与 `python onepass_main.py rollback ...` 子命令，交互式菜单第 9、A 项也可直接创建或回滚快照。

### 渲染

```bash
python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a \
  --edl out/001.keepLast.edl.json --out out/001.clean.wav --xfade --loudnorm
```

`--xfade` 会在片段衔接处做轻微淡化淡入，`--loudnorm` 会调用 ffmpeg 的 EBU R128 响度归一（默认目标 -16 LUFS）。如遇 Windows 命令长度限制，可去掉 `--xfade` 回退到 concat 模式。

### 批处理

```powershell
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -Render
```

脚本会扫描 `data/asr-json/` 与 `data/original_txt/`，按 stem 逐章执行 `retake_keep_last.py`，并在存在音频和 EDL 时调用渲染脚本。

## 批处理与汇总

当需要处理整本书或大量章节时，可使用 PowerShell 7+ 脚本 `scripts/bulk_process.ps1` 进行批量处理与结果汇总。脚本会自动匹配 `data/asr-json/*.json`、`data/original_txt/*.txt` 以及同名音频（若存在），逐章调用 `retake_keep_last.py`，并在指定时渲染干净音频。

### 常用参数与示例

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-Aggressiveness` | 50 | 传入 `--aggr`，0–100 的力度百分比 |
| `-Render` | `False` | 若指定，存在音频且生成了 EDL 时将调用 `edl_to_ffmpeg.py` |
| `-Snapshot` | `False` | 批处理结束后自动运行 `scripts/snapshot.py` 保存快照 |
| `-DryRun` | `False` | 传给单章脚本，仅生成字幕/EDL/标记，不渲染音频 |
| `-Config` | `config/default_config.json` | 若文件存在则作为 `--config` 传入 |
| `-AudioRequired` | `False` | 若指定，同名音频缺失会直接判定为 FAIL |
| `-AudioExtPattern` | `*.m4a,*.wav,*.mp3,*.flac` | 搜索音频时匹配的扩展名列表 |

```powershell
# 仅批量生成字幕/EDL/标记（不渲染）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -DryRun

# 批量并渲染（若存在同名音频）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -Render

# 批量渲染并在结束时自动创建快照
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -Render -Snapshot

# 强制音频也必须齐全（缺则判 FAIL）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 50 -Render -AudioRequired

# 指定自定义配置
pwsh -File .\scripts\bulk_process.ps1 -Config "config\my_config.json" -Render

# 逐章先清理再重跑（安全移动到 .trash/）
pwsh -File .\scripts\bulk_process.ps1 -Regen

# 硬删除旧产物后重跑（慎用）
pwsh -File .\scripts\bulk_process.ps1 -Regen -HardDelete
```

开启 `-Regen` 后，脚本会在每章处理前调用 `scripts/clean_outputs.py --stem <stem> --what generated --trash --yes`，并在 `summary.csv` / `summary.md` 中新增 `regened` 与 `cleaned_files` 列以记录清理情况。若配合 `-HardDelete`，旧文件会被直接删除而不会进入 `.trash/`，请谨慎使用。

### 输出产物

- `out/summary.csv`：按章节列出 `stem,json_path,txt_path,audio_path,aggr,exit_retake,exit_render,has_*` 等字段，便于二次统计或导入表格工具。
- `out/summary.md`：Markdown 汇总，包含总览统计、章节状态表格及常见问题提示。

CSV 中的 `delta_s` 为渲染后音频与原始音频的时长差值；若缺少音频或渲染未执行则为空。`filler_removed`、`retake_cuts`、`long_pauses`、`shortened_ms` 源自 `out/<stem>.log` 中的统计数值。

### 退出码

- `0`：全部章节成功（OK）
- `1`：至少存在 WARN（如缺少音频、未渲染、统计缺失）
- `2`：存在 FAIL（单章或渲染失败、硬性检查未通过）

### 常见问题

- **找不到 ffmpeg**：运行 `pwsh -File .\scripts\install_deps.ps1`，或手动安装后重启终端让 PATH 生效。
- **whisper-ctranslate2 CLI 不在 PATH**：可使用 `python -m whisper_ctranslate2 ...` 形式调用。
- **命令过长（Windows --xfade 情况）**：改用默认 concat 模式（去掉 `--xfade`）或拆分章节处理。
- **中文/空格路径**：建议在英文路径下执行，或对包含空格/中文的路径加引号。
- **只要字幕/标记不渲染**：不需要 `data/audio/`，批处理中不要加 `-Render`。
- **口癖误删或保留太多**：调整 `--aggr`（力度百分比），或在配置中编辑 `filler_terms`。
- **“保留最后一遍”误判/漏判**：调节 `retake_sim_threshold`，或通过 `--aggr` 间接影响阈值。
- **单章失败定位**：查看终端日志及 `out/<stem>.log`，日志中会包含外部命令行与返回码。

## 配置（config/default_config.json）

默认配置位于 `config/default_config.json`，字段说明如下：

| 字段 | 说明 | 推荐范围/默认 |
| --- | --- | --- |
| `filler_terms` | 需去除的口癖词列表，可按说话习惯增删。 | 根据项目自定义 |
| `gap_newline_s` | 连续词语的停顿超过该秒数时强制换行。 | 0.4–0.8 |
| `max_seg_dur_s` | 单段字幕的最大时长，超出将自动拆分。 | 4–7 |
| `max_seg_chars` | 单段字幕允许的最大字符数。 | 24–32 |
| `safety_pad_s` | 导出音频时保留的安全缓冲时长。 | 0.2–0.5 |
| `merge_gap_s` | 停顿低于该值时合并相邻片段。 | 0.15–0.35 |
| `long_silence_s` | 识别为长静音的阈值，用于后续收紧。 | 默认 0.86（由力度映射） |
| `retake_sim_threshold` | 判断重录段落的文本相似度阈值，越高越严格。 | 0.82–0.93 |
| `tighten_target_ms` | 收紧长静音的目标时长（毫秒）。 | 180–600 |

覆盖配置的方式示例如下：

```bash
# 自定义配置示例：
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out \
  --config config/default_config.json --aggr 55
```

或者复制默认配置：

```bash
copy config/default_config.json config/my_config.json  # Windows PowerShell/命令提示符均可
# 编辑 config/my_config.json 后再执行：
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out \
  --config config/my_config.json --aggr 55
```

脚本的 `--aggr`（aggressiveness，力度百分比）参数会被自动限制在 0–100 之间，用于统一调节阈值松紧度。内部映射关系如下：

- `retake_sim_threshold = 0.82 + 0.11 × (aggr / 100)`，力度越大越严格；
- `long_silence_s = 1.2 - 0.7 × (aggr / 100)`，力度越大越容易识别为长静音；
- `tighten_target_ms = 500 - 320 × (aggr / 100)`，力度越大目标静音越短；
- 当力度 ≥ 60 时自动启用 `filler_strict`，口癖词表会使用更严格策略。

## Adobe Audition 标记导入

1. 打开 Adobe Audition，切换到 **Markers** 面板。
2. 选择 **Import Markers…**，并挑选 `*.audition_markers.csv` 文件。
3. 若导入时提示列头不匹配，可先在 Audition 内手动创建一条标记并导出 CSV，参考列头顺序后调整 `scripts/markers.py` 中的默认列头，或在运行前设置环境变量 `ONEPASS_AU_HEADER` 覆盖。

## 不提交二进制/媒体的约定

`data/audio/`、`data/asr-json/`、`data/original_txt/`、`out/` 等目录仅用于存放本地原始素材与工具产出，涉及版权、隐私与容量问题，**全部不入库**。提交前请确认这些目录下无实际媒体文件。

## 免责声明与隐私

仅处理你有权使用的音频与文本；请勿将受版权保护素材上传至公共仓库；处理数据建议在本地或受控环境中进行。

## 路线图

- 依赖与配置清单、默认配置模板
- 环境自检脚本与一键安装辅助
- 主程序交互式向导与基础 CLI
- 素材校验与清理工具
- 核心引擎模块化（loader/retake/clean/segment/edl/writers/markers/aggr/pipeline）
- 单章命令行脚本完善
- 音频导出渲染管线
- 批处理流程与汇总报告
- 文档完善与示例/自检集

## 环境变量快照 & 配置 Profiles

`deploy/profiles/` 目录下提供了可直接套用的运行配置（Profile），并配套 `scripts/envsnap.py` 命令行工具完成应用、快照与远端导出。典型流程如下：

```bash
# 选定配置（例如 24GB GPU 的 prod_l4_24g）并应用到 .env.active
python scripts/envsnap.py apply --profile prod_l4_24g

# 将当前激活配置上传到远端 deploy/profiles/.env.active
python scripts/envsnap.py export-remote

# 按该配置运行一键桥接，并记录快照备注
python deploy/cloud/vultr/cloud_vultr_cli.py asr-bridge --profile prod_l4_24g --note "book full run"

# 运行期间进入 watch 模式，实时镜像事件流与 manifest
python deploy/cloud/vultr/cloud_vultr_cli.py watch

# 小样测试可改用 test_subset，并只处理 001* 前缀
python scripts/envsnap.py apply --profile test_subset
python deploy/cloud/vultr/cloud_vultr_cli.py asr-bridge --profile test_subset --note "subset dry run"
```

使用要点：

- `.env.active` 为临时文件，请勿纳入版本控制；切换 profile 后可随时运行 `scripts/envsnap.py snapshot --note ...` 记录当前环境，快照会落在 `out/_runs/<run_id>/env.snapshot.json`。
- `prod_l4_24g` 默认采用 whisper large-v3 + float16 + workers=2，适合 24GB 显存实例；如显存不足，可改为 `ASR_COMPUTE=int8_float16` 或 `ASR_WORKERS=1`。
- `test_subset` 针对 001* 前缀，便于快速演练事件流、watch 镜像与回收逻辑；确认链路正常后再切换到生产配置。
- 远端 `deploy/sync/remote_run_asr.sh` 会依据 `.env.active` 生成 `out/_runs/<run_id>/manifest.json`、`events.ndjson` 与 `state.json`，watch 命令会自动镜像到本地 `out/remote_mirror/<run_id>/` 并实时展示。
- `deploy/cloud/vultr/cloud_vultr_cli.py watch` 支持 `--run <id>` 指定历史任务，也会在 asr-bridge 结束后询问是否立即进入 watch 模式。

在 `python onepass_main.py` 的“云端部署（Vultr）向导”面板中，新增了快捷选项：

- **P)** 列出可用 profile 并应用（等价于 `envsnap.py apply --profile ...`）；
- **R)** 显示当前激活配置与最近快照；
- **5)** 一键桥接时可选择 profile 并填写快照备注，命令会自动传递 `--profile/--note` 参数给 `asr-bridge`。

### 先看可用计划，再创建实例

```bash
# 仅查看东京(nrt)+Ubuntu 22.04 的 GPU 计划
python deploy/cloud/vultr/cloud_vultr_cli.py plans-nrt

# 自定义筛选：仅看 A40/L40S 且 vRAM≥24GB
python deploy/cloud/vultr/cloud_vultr_cli.py plans --region nrt --os ubuntu-22.04 --family "A40|L40S" --min-vram 24
```

若输出为空，表示东京（nrt）当前暂未提供符合条件的库存，可改用 `--region sgp`、`--region lax`、`--region fra` 等其它数据中心后再试。

