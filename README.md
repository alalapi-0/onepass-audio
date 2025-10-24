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

`scripts/install_deps.ps1` 会优先使用 `winget`（备用：Chocolatey）安装 ffmpeg，并调用 `python -m pip install -r requirements.txt`。该脚本可多次执行，若依赖已满足会提示“已安装”。ffmpeg 是后续音频渲染及切片脚本的基础工具，缺失会导致相关命令无法运行。

## 主程序使用说明

`onepass_main.py` 提供统一入口，可通过子命令或交互式菜单串联安装、校验、处理与渲染流程。

```bash
# 一键安装（需要 PowerShell 7）
python onepass_main.py setup

# 环境自检
python onepass_main.py validate

# 单章处理（力度 60，生成字幕/EDL/标记，不渲染音频）
python onepass_main.py process --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out --aggr 60 --dry-run

# 按 EDL 渲染音频（带轻微 crossfade 与响度归一）
python onepass_main.py render --audio data/audio/001.m4a \
  --edl out/001.keepLast.edl.json --out out/001.clean.wav --xfade --loudnorm
```

直接运行 `python onepass_main.py` 会进入菜单模式，当前提供以下选项：

1. 环境自检
2. 素材检查
3. 单章处理（去口癖 + 保留最后一遍 + 生成字幕/EDL/标记）
4. 仅渲染音频（按 EDL）
5. 退出

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

## 使用示例

```bash
# 单章：生成去口癖字幕 + 保留最后一遍 + EDL + Audition 标记
python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out

# （可选）按 EDL 导出干净音频
python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav
```

## 配置（config/default_config.json）

默认配置位于 `config/default_config.json`，字段说明如下：

| 字段 | 说明 |
| --- | --- |
| `filler_terms` | 需去除的口癖词列表，可按说话习惯增删。 |
| `gap_newline_s` | 连续词语的停顿超过该秒数时，强制换行。 |
| `max_seg_dur_s` | 单段字幕的最大时长，超出将自动拆分。 |
| `max_seg_chars` | 单段字幕允许的最大字符数。 |
| `safety_pad_s` | 导出音频时保留的安全缓冲时长。 |
| `merge_gap_s` | 停顿低于该值时合并相邻片段。 |
| `long_silence_s` | 识别为长静音的阈值，用于后续收紧。 |
| `retake_sim_threshold` | 判断重录段落的文本相似度阈值，越高越严格。 |
| `tighten_target_ms` | 收紧长静音的目标时长（毫秒）。 |

覆盖配置的方式示例如下：

```bash
# 占位示例：实际脚本在后续步骤加入 --config 参数
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out \
  --config config/default_config.json
```

或者复制默认配置：

```bash
copy config/default_config.json config/my_config.json  # Windows PowerShell/命令提示符均可
# 编辑 config/my_config.json 后再执行：
python scripts/retake_keep_last.py --json data/asr-json/001.json \
  --original data/original_txt/001.txt --outdir out \
  --config config/my_config.json
```

后续将提供 `--aggr`（aggressiveness，力度百分比）参数，用于统一调节阈值松紧度。

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
