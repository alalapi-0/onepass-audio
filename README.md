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

## 使用示例

```bash
# 单章：生成去口癖字幕 + 保留最后一遍 + EDL + Audition 标记
python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out

# （可选）按 EDL 导出干净音频
python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav
```

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
