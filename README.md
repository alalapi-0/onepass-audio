# OnePass Audio — 录完即净，一遍过

本项目是一键生成去口癖、保留“同句最后一遍”的干净字幕，并可选按剪辑清单导出干净音频的工具集（MVP）。

## 功能清单（当前与计划）

- 去口癖（可配置词表），流畅断句（SRT/VTT/TXT）
- “同句保留最后一遍、删除前面重录”
- 生成 EDL（剪辑清单）与 Adobe Audition 标记 CSV
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

## 使用示例（占位）

```bash
# 单章：生成去口癖字幕 + 保留最后一遍 + EDL + Audition 标记
python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out

# （可选）按 EDL 导出干净音频
python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a --edl out/001.keepLast.edl.json --out out/001.clean.wav
```

## 文本规范化（原文预处理）

为了提高句级与词级对齐的召回率，建议先对原稿执行统一的文本规范化：把 `序⾔/⼈类/⽹络/⼒量` 等兼容部件替换为常见写法（`序言/人类/网络/力量`），统一全半角标点，去掉 BOM、零宽字符与隐形控制符，必要时再进行繁体转简体。项目新增了独立脚本与交互入口用于批量处理 `data/original_txt/*.txt`。

常用命令如下：

```bash
# 仅查看改动（dry-run）
python scripts/normalize_texts.py --src data/original_txt --dry-run --report out/textnorm_report.md

# 原地规范化（会生成 .bak）
python scripts/normalize_texts.py --src data/original_txt --inplace --report out/textnorm_report.md --punct ascii --t2s
```

运行脚本会对每个文件执行 Unicode NFKC 归一化、去除零宽字符/BOM、兼容部件替换、标点风格统一（`ascii` | `cjk` | `keep`）、空白压缩，并可在安装 [OpenCC](https://github.com/BYVoid/OpenCC) 后追加 `--t2s` 实现繁转简。未安装 opencc 时脚本会打印提示并自动跳过。

处理完成会生成 Markdown 汇总报告（默认 `out/textnorm_report.md`），包含：

- 总替换数量与空白压缩统计；
- Top 10 “怪字符”列表（含 Unicode 编码）；
- 每个文件的变更情况与长度变化；
- 逐文件的若干个 `Before/After` 片段，使用 `…`/`⏎` 标识截断与换行。

如需扩展兼容字符映射，可在 `config/textnorm_custom_map.json` 中追加键值，例如：

```json
{ "⾃": "自", "⾏": "行" }
```

脚本默认写入 `.bak` 备份，可加 `--no-backup` 禁用。若不想覆盖原文件，可提供 `--dst` 输出目录；使用 `--dry-run` 时不会写入文件，只生成报告以便审核。

主程序（`python onepass_main.py`）的交互菜单新增 “预处理：原文规范化（NFKC + 兼容字清洗）” 项，可一键调用脚本对 `data/original_txt/` 进行处理；如选择 Dry-Run，会附加 `--dry-run` 并提示先检查报告。

## 文本规范化（适配词级对齐）

在完成基础清洗后，还需要针对 ASR 词级 `words.json` 进行“对齐友好化”增强，以减少“最后一遍”保留流程中的误差：

- 统一康熙部首/兼容字形，例如 `⼈/⼤/⽤/⾥/⾼/⻓/⻋/⻝/⻢` 等字符回写为常用字；
- 统一省略号、破折号和中英文标点，将 `...`、`--`、半角括号等写法折算为中文标点系；
- 剔除零宽空格、段内硬换行以及中文之间的额外空格，使句子与词时间戳可直接比对；
- 可选剔除目录、版权页、献词等前后缀文本，避免无效段落干扰词级覆盖率。

对应 CLI 由 `onepass.normalize_original` 模块提供，既可处理单个章节，也可批量跑整本书：

```bash
# 单章试跑：生成规范化文本与对齐感知报告
python -m onepass.normalize_original \
  --orig data/original_txt/001序言01.txt \
  --words data/asr-json/001序言01.words.json \
  --out  data/original_txt_norm/001序言01.norm.txt \
  --report out/001序言01.norm.diff.md \
  --lang zh --strip-frontmatter true --punct-style zh --number-style half

# 批量跑前 3 章（自动按文件名匹配 .words.json）
python -m onepass.normalize_original \
  --orig-dir data/original_txt \
  --words-dir data/asr-json \
  --out-dir  data/original_txt_norm \
  --report-dir out \
  --lang zh --strip-frontmatter true
```

输出内容包括：

- `*.norm.txt`：适配 ASR 词级的正文文本，兼容字、标点、空白均已统一；
- `*.norm.diff.md`：差异报告，列出未覆盖的兼容字符、标点/空白统计、Top-20 未匹配 n-gram、字符集差异与预估“对齐友好度”评分；
- 报告中会提示未入表的兼容字符，可根据建议把映射补充到 `config/compat_map_zh.json`。

规范化结果通常能显著提高 `words.json` 的覆盖率与标点一致度，句级/词级对齐器在相同阈值下会获得更高的成功率，从而让“保留最后一遍”的抽取更加稳定。

### 人工验收脚本

```bash
# 1) 仅预览
python scripts/normalize_texts.py --src data/original_txt --dry-run --report out/textnorm_report.md

# 2) 原地生效（有 .bak 备份）
python scripts/normalize_texts.py --src data/original_txt --inplace --report out/textnorm_report.md --punct ascii --t2s

# 3) 在主菜单中执行同等动作
python onepass_main.py
# 选择 “预处理：原文规范化（NFKC + 兼容字清洗）”，观察逐步反馈与最终统计
```

## 不提交二进制/媒体的约定

`data/audio/`、`data/asr-json/`、`data/original_txt/`、`out/` 目录全部不入库，原因是涉及版权、容量与隐私数据，需在本地或受控环境中管理。

## 免责声明与隐私

仅处理你有权使用的音频与文本；请勿将受版权保护素材上传至公共仓库；建议在本地或受控环境中处理敏感数据。

## 路线图

1. 补充依赖与配置文件，完善 requirements。
2. 提供环境自检与一键安装脚本。
3. 设计主程序交互向导与配置模板。
4. 构建素材验证器，确保输入文件完备。
5. 模块化核心引擎（loader/retake/clean/segment/edl/writers/markers/aggr/pipeline）。
6. 打磨单章 CLI 工作流，提供典型示例。
7. 实现音频渲染与剪辑清单联动。
8. 支持批处理整本书与汇总报告生成。
9. 丰富文档、示例与自动自检流程。
