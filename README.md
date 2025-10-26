# OnePass Audio — 从“词级 JSON + 原文 TXT”生成 AU 标记与 EDL（最小骨架）

OnePass Audio 是一个面向本地离线使用的极简工具集，目标是基于词级时间戳 JSON 与原文 TXT 生成 AU 标记 CSV 与 EDL 文件。本轮仅提供基础目录结构、依赖清单、忽略策略与占位用法，后续轮次将逐步实现对齐与导出功能。

## 安装

```
python -m venv .venv
..venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 目录约定

- `data/asr-json/`：放置词级时间戳 JSON（faster-whisper 风格）
- `data/original_txt/`：放置原文 TXT
- `data/audio/`：放置对应音频
- `out/`：输出目录

## 基本用法占位

下一轮将提供以下脚本与功能：

```
# 生成 AU 标记 CSV 与 EDL（下一轮提供脚本）

python scripts/make_markers.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out

# 按 EDL 渲染音频（可选，第三轮提供脚本）

python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav
```

## 隐私与合规

仅处理拥有使用权的文本与音频，不上传任何隐私数据。

## 版本路线图

第二轮将实现对齐与导出器，第三轮实现按 EDL 渲染，第四轮提供轻量素材校验。
