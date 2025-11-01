# OnePass Audio — 录完即净，一遍过

本项目是一键生成去口癖、保留“同句最后一遍”的干净字幕，并可选按剪辑清单导出干净音频的工具集（MVP）。

## 构建历程（Prompt 演进纪要）

1. **第 1 轮：需求梳理与目录搭建** —— 通过最初的 Prompt 明确了“去口癖、保留最后一遍、生成 EDL”三大功能，并搭建了 `onepass/` 包与 `scripts/` 目录骨架。此阶段暴露出的难题是素材格式尚未统一，导致示例无法跑通，需要额外设计命名约定与目录结构。
   - 关键决策：统一以 `<stem>.words.json` ↔ `<stem>.txt` 为配对基准，并预留 `materials/` 目录存放样例。
   - 典型问题：部分 ASR 输出缺少 `end` 字段或时间戳乱序，必须在 Loader 层进行排序与异常提醒。
2. **第 2 轮：文本规范化与数据加载** —— 增补了 `onepass.textnorm`、`onepass.asr_loader` 等模块，围绕“词级对齐”细化了文本预处理流程。此时遇到的问题是兼容字符表不完整、OpenCC 未必可用，导致规范化后仍存在漏网字符，需要在 `config/` 中维护自定义映射并增加“缺少 opencc” 的一次性告警。
   - 关键决策：将规范化拆分为 NFKC → 去零宽 → 自定义兼容表 → 可选繁简转换 → 标点风格 → 空白压缩，方便插拔。
   - 典型问题：发现部分素材存在 BOM 与零宽字符，若不先清洗会影响 RapidFuzz 对齐得分。
3. **第 3 轮：交互主程序与批处理流程** —— 加入 `onepass_main.py` 交互入口、章节资源匹配与批量处理逻辑。过程中发现素材目录命名不一致、音频优先级选择困难，最终通过哈希表比对前缀、显式的音频格式优先级（WAV → FLAC → M4A → ...）以及“缺什么提示什么”的交互文案解决。
   - 关键决策：批处理时先扫描 JSON，再按优先顺序回落到 `.norm.txt` / `.txt`，并输出 `batch_report.json` 方便复盘。
   - 典型问题：Windows 上默认路径包含中文与空格，需在交互提示中增加引号清理与路径验证逻辑。
4. **第 4 轮：文档完善与可用性增强** —— 在最新 Prompt 中补充了运行说明、详细注释、环境准备指南，并修复了英语注释与中文内容风格不一致的问题。此阶段的主要挑战是“逐行中文注释”工作量较大，需要逐块核对关键模块，尤其是对齐与文本规范化两个核心文件。
   - 关键决策：统一采用中文注释解释每一步算法意图，让初次接触的播主也能快速理解流程。
   - 典型问题：在批量补注释时需确保不破坏 doctest/类型提示，因而采用“就地翻译 + 轻量补充”策略。
5. **第 5 轮：EDL 音频渲染落地** —— 新增 `onepass.edl_renderer` 库模块、`scripts/edl_render.py` 命令行脚本与主菜单入口，实现按剪辑清单一键导出干净音频，同时补充文档与 5 分钟跑通示例。
   - 关键决策：借助 `ffprobe` 探测时长、`ffmpeg concat` 拼接保留片段，并允许 `--dry-run` 输出命令供人工验证。
   - 典型问题：遇到旧版 EDL 中 `actions`/`segments` 字段混用，需要在 Loader 层兼容并输出清晰错误信息。

## 程序用途与最终产出

OnePass Audio 面向“单人快速录制有声内容”场景，帮助播主/讲师在一遍录音中完成去口癖、保留最后一遍重录、生成剪辑清单与字幕。完整跑完流程后将得到：

- **干净字幕**：`*.keepLast.srt`（含时间轴）、`*.keepLast.txt`（纯文本稿）。
- **剪辑清单**：`*.keepLast.edl.json`，用于后续在 NLE/DAW 中快速删除无效片段。
- **Adobe Audition 标记**：`*.keepLast.audition_markers.csv`，方便在 Audition 中直接定位重复段。
- **（可选）干净音频**：按 EDL 渲染的 `*.clean.wav`。
- **文本规范化报告**：`out/normalize_report.csv`，记录规范化统计与建议。

## 运行环境与配置要求

- 操作系统：建议 Windows 10/11（PowerShell 7+），在 macOS/Linux 下同样可运行（需替换脚本调用方式）。
- Python：3.10 及以上，推荐创建虚拟环境。
- 依赖：执行 `pip install -r requirements.txt` 安装；若需繁转简功能，额外安装 [OpenCC](https://github.com/BYVoid/OpenCC)。
- 多媒体工具：`ffmpeg` 需在 PATH 中，以便按 EDL 渲染音频。
- 字体/编码：所有文本文件使用 UTF-8 编码，避免 BOM 与零宽字符干扰。
- 可选硬件：CPU 即可完成全部流程；若有 GPU，可在导出 ASR JSON 时使用更高性能的识别模型，但本项目不直接调用 GPU。

### Python 环境快速搭建

```bash
python -m venv .venv           # 创建虚拟环境
source .venv/bin/activate      # macOS/Linux
# 或 .venv\Scripts\Activate   # Windows PowerShell
pip install -U pip setuptools  # 升级基础工具
pip install -r requirements.txt
```

如需启用繁转简：

```bash
pip install opencc
```

## 必备素材与目录约定

项目默认假定以下目录结构（可在交互式流程中指定其他路径）：

- `materials/`：放置同名的 `*.json`（词级 ASR 输出）、`*.txt`（原稿）、可选的音频文件（`.wav/.flac/.m4a/...`）。
- `data/original_txt_norm/`：存放经过 `scripts/normalize_original.py` 处理后的规范文本，命名为 `<stem>.norm.txt`。
- `out/`：输出目录，生成的字幕、文本、EDL、报告及可选音频均保存在此。
- `config/`：自定义兼容字符映射、标点策略等配置。

运行项目前请确保：

1. 每个章节具备同名的 `*.json` 与 `*.txt`；若想导出音频，还需准备同名前缀的音频文件。
2. ASR JSON 使用 faster-whisper/Funasr 等可提供词级时间戳的格式（字段见 `onepass.asr_loader`）。
3. 原稿文本已通过 `scripts/normalize_texts.py` 或 `scripts/normalize_original.py` 做过基本清洗，避免兼容字符与零宽字符干扰对齐。

### 素材文件格式速查

| 文件类型 | 必填字段 | 说明 |
| --- | --- | --- |
| `*.words.json` | `segments[].words[].{word/text,start,end}` | 每个词需包含文本与起止时间，单位推荐秒（浮点）。 |
| 原稿 `*.txt` | 纯文本 | 建议使用 UTF-8 与 Unix 换行，配合规范化脚本可生成 `<stem>.norm.txt`。 |
| 可选音频 | N/A | 支持 WAV/FLAC/M4A/AAC/MP3/OGG/WMA，程序会按优先级自动选取。 |

> **提示**：如第三方工具导出 JSON 的字段命名不同，可先编写转换脚本适配到上述结构，或扩展 `onepass.asr_loader`。

## 运行流程速览

1. **创建虚拟环境并安装依赖**（见下文“安装步骤”）。
2. **整理素材目录**：将 JSON/TXT/音频放入同一文件夹，确保命名一致。
3. **可选文本规范化**：使用 `python scripts/normalize_original.py` 或在主程序菜单选择“预处理：原文规范化”。
4. **启动主流程**：运行 `python onepass_main.py`，在主菜单选择批量处理或 `R` 进入 EDL 渲染，按提示选择素材目录、输出目录与导出参数。
5. **查看输出**：处理完成后在 `out/` 目录查看字幕、EDL、报告与可选音频，并按照日志提示核对未对齐样例。

更多细节（目录结构、文本规范化流程等）可继续参考下方原有章节。

### 从零开始的 5 分钟示例

1. 在 `materials/example/` 准备 `demo.words.json`、`demo.txt` 与 `demo.wav`。
2. （可选）运行 `python scripts/normalize_original.py --in materials/example --out out/norm` 生成 `demo.norm.txt`。
3. 执行 `python onepass_main.py`，依次确认素材目录、输出目录、是否渲染音频等选项。
4. 等待程序自动对齐并输出字幕/EDL/Markers/音频，终端会提示未对齐或低得分句子。
5. 在 `out/` 目录查收 `demo.keepLast.srt`、`demo.keepLast.edl.json`、`demo.clean.wav` 等成果文件。

### 常见问题（FAQ）

- **提示缺少 opencc**：若需要繁转简，请 `pip install opencc`；如果无需该功能可忽略提示。
- **ffmpeg/ffprobe 未找到**：确认已安装 [FFmpeg](https://ffmpeg.org/)，并将其加入 `PATH`。
- **字幕为空或缺句**：检查 JSON 是否存在 `words` 字段及时间戳是否递增，可调整 `--score-threshold` 重新执行。
- **批处理配对失败**：确保文件前缀完全一致（区分大小写），或在 CLI 中修改 `--glob-words`、`--glob-text` 模式。

## 统一命令行与整书批处理

为方便自动化集成与整书批处理，本项目新增 `scripts/onepass_cli.py`，将前三轮的独立脚本封装为四个子命令，语义保持一致：

- **`prep-norm`**：对单个文件或整个目录执行文本规范化，输出 `<stem>.norm.txt` 并追加 `out/normalize_report.csv`。
- **`retake-keep-last`**：根据词级 JSON 与原文 TXT 导出 SRT/TXT/EDL/Markers，可单文件运行，也支持目录批量配对与汇总报告。
- **`render-audio`**：读取 `*.edl.json` 并按保留片段渲染干净音频，支持递归批量模式，结果追加到 `batch_report.json` 的 `render_audio` 小节。
- **`all-in-one`**：一键串联规范化 → 保留最后一遍 → 可选渲染音频，面向“整书跑通”场景，输出统一的 `batch_report.json` 汇总。

### 常用命令示例

```bash
# 仅规范化某目录下的 TXT，并在 out/norm/ 写出 <stem>.norm.txt
python scripts/onepass_cli.py prep-norm \
  --in materials/example \
  --out out/norm \
  --char-map config/default_char_map.json \
  --opencc none \
  --glob "*.txt"

# 单文件保留最后一遍 → SRT/TXT/EDL/Markers
python scripts/onepass_cli.py retake-keep-last \
  --words-json materials/example/demo.words.json \
  --text materials/example/demo.txt \
  --out out

# 目录批量模式，按 stem 自动匹配 *.words.json ↔ *.norm.txt/ *.txt
python scripts/onepass_cli.py retake-keep-last \
  --materials materials/book1 \
  --out out/book1 \
  --glob-words "*.words.json" \
  --glob-text "*.norm.txt" "*.txt" \
  --workers 4

# 按 EDL 渲染干净音频（目录递归）
python scripts/onepass_cli.py render-audio \
  --materials out/book1 \
  --audio-root materials/book1 \
  --glob-edl "*.keepLast.edl.json" \
  --out out/book1/audio \
  --workers 4

# 整书一键流程：规范化 + 保留最后一遍 + 渲染音频
python scripts/onepass_cli.py all-in-one \
  --materials materials/book1 \
  --audio-root materials/book1 \
  --out out/book1 \
  --do-norm --opencc none --norm-glob "*.txt" \
  --glob-words "*.words.json" \
  --glob-text "*.norm.txt" "*.txt" \
  --render --glob-edl "*.keepLast.edl.json" \
  --samplerate 48000 --channels 1 \
  --workers 4
```

### 配对规则与命名约定

- 批处理模式下以词级 JSON 的文件名前缀（去掉 `.words.json`）为基准，优先寻找 `<stem>.norm.txt`，若缺失则回退 `<stem>.txt`。
- 所有生成文件都保留同一 `stem` 前缀：`<stem>.keepLast.srt/.txt/.edl.json/.audition_markers.csv` 与 `<stem>.clean.wav`。
- `batch_report.json` 会分阶段记录 `items` 与 `summary`，方便统计成功、失败、耗时及聚合指标。

### 并发参数与平台注意事项

- `--workers` 允许并发处理多个条目，未指定时默认为串行；在 Windows 平台批量调用时，请确保入口脚本带有 `if __name__ == "__main__":` 保护。
- 渲染阶段若不指定 `--samplerate`/`--channels`，将沿用 EDL 中的建议或源音频探测结果。

### 与交互入口的关系

主菜单 `[P]`、`[K]`、`[R]` 以及新增的 `[A]` 选项均会回显等价的统一 CLI 命令，便于将交互式操作迁移到自动化流水线。更细致的文本规范化、保留最后一遍与渲染音频说明，可继续参考下方对应章节。

## 原文规范化（可配置）

在正式对齐前先清洗原稿，可以显著降低零宽字符、兼容字和混排空白造成的错位，从而提高“保留最后一遍”匹配的成功率。项目提供了可编辑的
`config/default_char_map.json`，对常见中文排版场景进行兜底处理：

- `delete`：列出需要直接删除的字符，默认涵盖零宽空白与 BOM。
- `map`：指定需要替换的兼容字符与标点，例如弯引号、破折号、中文逗号等。
- `normalize_width` / `preserve_cjk_punct`：控制是否执行 NFKC 宽度归一，并在归一后回写全角中文标点。
- `normalize_space`：折叠多余空白、清理行首尾空格，避免对齐时出现看不见的分隔符。

如需繁简转换，可额外安装 OpenCC；脚本会先检测本地是否存在 `opencc` 可执行文件，缺失时会在报表中提示“跳过转换”，同时保留原文内容。

最小示例：

1. 在 `materials/example/` 目录放置 `demo.txt` 原文。
2. 运行

   ```bash
   python scripts/normalize_original.py --in materials/example/demo.txt --out out/norm --char-map config/default_char_map.json --opencc none
   ```

3. 检查 `out/norm/demo.norm.txt` 与 `out/normalize_report.csv`，前者为清洗后的文本，后者记录删除/替换次数、空白折叠、OpenCC 状态以及可疑字符示例。

推荐流程是：**先执行原文规范化**，再运行“保留最后一遍”生成字幕/EDL，最后按需调用 EDL 音频渲染。如此可以最大化减少对齐误差，并保证后续报
表可以直接复用同一份清洗结果。

## 保留最后一遍流程

### 输入与输出概览

- **输入**：词级 ASR JSON（支持 faster-whisper/Funasr 等含 `segments[].words[]` 或顶层 `words[]` 字段）、原文 TXT（一行一“句”）。
- **输出**：
  - `<stem>.keepLast.srt` —— 仅保留每行最后一次出现的字幕，时间戳顺序整理完毕；
  - `<stem>.keepLast.txt` —— 对应文本稿；
  - `<stem>.keepLast.edl.json` —— action=`"keep"` 的 EDL，可直接喂给 `scripts/edl_render.py`；
  - `<stem>.audition_markers.csv` —— Adobe Audition 标记。

### 词级 JSON 支持的字段

常见结构示例（可直接与本仓库提供的 `materials/example/demo.words.json` 对照）：

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 2.2,
      "words": [
        {"start": 0.0, "end": 0.6, "word": "第一"},
        {"start": 0.6, "end": 1.2, "word": "行"},
        {"start": 1.2, "end": 1.8, "word": "文本"}
      ]
    }
  ]
}
```

字段名 `word`/`text` 皆可，内部会自动 `strip()` 前后空格并跳过缺失时间戳的词。若时间戳不是递增，会自动稳定排序并在元数据中记录修复信息。

### 原文 TXT 的建议格式

- 建议“一行一段”，方便识别重录；
- 保持与录音内容的实际顺序一致，避免跨行换位；
- 可保留标点，适配层会在内部统一规范化。

### 回退匹配与局限

当严格子串匹配失败时，会回退到最长公共子串（LCS）策略；若命中长度 ≥ 原行的 80%，则视为近似成功。对于极端情况（大量口误、缺词或文本顺序与录音严重不符），仍可能需要人工干预或重新导出 ASR JSON。

### 最小示例

仓库已收录 `materials/example/demo.txt` 与 `materials/example/demo.words.json`，演示“第二行重复录制，仅保留最后一遍”的效果：

```bash
python scripts/retake_keep_last.py \
  --words-json materials/example/demo.words.json \
  --text materials/example/demo.txt \
  --out out
```

成功后可在 `out/` 中查看 `demo.keepLast.srt`、`demo.keepLast.txt`、`demo.keepLast.edl.json` 与 `demo.audition_markers.csv`。若需立即渲染音频，可继续执行：

```bash
python scripts/edl_render.py \
  --edl out/demo.keepLast.edl.json \
  --audio-root materials \
  --out out
```

或在主菜单中选择 `[K]` 进入单文件流程，再将生成的 EDL 交给 `[R]` 选项完成音频裁剪。

## 功能清单（当前与计划）

- [x] 去口癖（可配置词表），流畅断句（SRT/VTT/TXT）
- [x] ASR 适配层 + 保留最后一遍策略
- [x] 生成 EDL（剪辑清单）与 Adobe Audition 标记 CSV
- [x] 按 EDL 一键导出干净音频
- [x] 原文规范化（可配置）
- [x] 统一命令行与批处理报告（含整书汇总）
- [x] 环境自检与统一日志

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

## 环境自检与常见故障排查

为了便于排查依赖、权限与路径问题，本轮新增了 `scripts/env_check.py` 自检脚本。它会检测 Python 版本、虚拟环境、`ffmpeg`/`ffprobe`/`opencc` 可用性、平台信息以及 `out/`、`materials/` 等目录的读写权限，并在终端输出摘要表格与修复建议。

### 快速运行

```bash
python scripts/env_check.py --out out --verbose
```

默认会在指定 `--out` 目录写入 `env_report.json`，其中包含各检查项的状态、建议与补充提示。`--verbose` 会额外打印探测命令与返回码，便于人工复现。

### 日志位置与查看技巧

- 所有脚本与 CLI 会自动写入 `out/logs/YYYY-MM-DD/onepass-YYYYMMDD.log`，并同步输出到控制台。
- 建议在编辑器或终端中搜索 `[ERROR]`、`[WARNING]` 或 `exception` 关键字快速定位异常。
- 日志目录会在首次运行时自动创建，也可通过主菜单 `[E]` 快速查看实际路径。

### 常见故障及建议

- `ffmpeg` 或 `ffprobe` 未安装/未加入 PATH：按照官方指引安装，并确认命令行可直接运行 `ffmpeg -version`。
- Windows 路径包含特殊字符、未转义反斜杠或禁用长路径：优先使用不含空格/点结尾的目录，必要时开启 `LongPathsEnabled` 策略。
- 输出目录不可写：将 `--out` 指向当前用户有读写权限的路径，或调整 ACL/权限设置。
- `opencc` 未安装：自检会给出提示，繁简转换会被跳过；可按需安装 OpenCC 后重跑流程。

遇到问题时，建议先运行环境自检并附上 `env_report.json` 与最新日志片段，有助于快速定位问题。主菜单新增的 `[E]` 选项会引导完成以上步骤。

## 按 EDL 渲染干净音频

### 交互式入口

1. 运行 `python onepass_main.py`，主菜单输入 `R` 进入“按 EDL 渲染干净音频”。
2. 拖拽或输入 `*.edl.json` 文件路径，随后指定 `audio_root`（默认 `materials/`）与输出目录（默认 `out/`，不存在会自动创建）。
3. 可选填写目标采样率与声道数；留空则沿用源音频的参数。支持勾选 Dry-Run 仅打印命令。
4. 程序会展示解析到的源音频、输出路径、保留片段数量与累计时长，并打印等价 CLI 命令。确认后即调用 `scripts/edl_render.py` 完成渲染。
5. 渲染成功后终端会再次提示输出路径及保留时长，方便与剪辑日志核对。

### CLI 调用示例

```bash
python scripts/edl_render.py \
  --edl materials/demo/demo.keepLast.edl.json \
  --audio-root materials \
  --out out \
  --samplerate 48000 \
  --channels 1
```

`--samplerate/--channels` 可省略，此时会沿用 EDL 中的建议设置或源音频原始参数。加上 `--dry-run` 可仅打印最终 `ffmpeg` 命令。

### EDL JSON 结构与兼容性

- **新式结构（推荐）**：

```json
{
  "source_audio": "materials/demo/demo.wav",
  "samplerate": 48000,
  "channels": 1,
  "segments": [
    {"start": 0.50, "end": 2.20, "action": "keep"},
    {"start": 3.00, "end": 4.40, "action": "keep"},
    {"start": 5.80, "end": 8.10, "action": "keep"}
  ]
}
```

- **旧式兼容写法**（仅提供 `keep: true/false` 时也可自动转换）：

```json
{
  "audio": "materials/demo/demo.wav",
  "segments": [
    {"start": 0.50, "end": 2.20, "keep": true},
    {"start": 2.20, "end": 3.00, "keep": false},
    {"start": 3.00, "end": 4.40, "keep": true},
    {"start": 4.40, "end": 5.80, "keep": false},
    {"start": 5.80, "end": 8.10, "keep": true}
  ]
}
```

若只提供 `actions` 列表（例如旧版 `*.keepLast.edl.json`），渲染器会自动将 `cut` 片段取补集并生成保留区间。

### 常见问题排查

- **找不到 ffmpeg/ffprobe**：请先安装 [FFmpeg](https://ffmpeg.org/download.html) 并将其加入 PATH。Windows 用户可使用 [ffmpeg.org](https://ffmpeg.org/download.html) 或 [Gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 提供的预编译包。
- **路径含空格或中文**：保持引号或使用拖拽输入。渲染器内部统一使用 `pathlib.Path`，可跨平台处理。
- **采样率/声道不一致**：若保留片段来自不同格式的源文件，建议显式传入 `--samplerate` 与 `--channels`，或在交互式入口中填写目标参数。
- **EDL 全为 drop 动作**：渲染器会自动取补集；若最终保留时长为 0，会明确报错，提示检查剪辑清单。

### 5 分钟跑通最小示例

1. **准备虚拟环境**（可与主流程共用）：

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows 使用 .\.venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```

2. **生成示例音频**（输出到 `materials/demo/demo.wav`，若目录不存在请先创建，macOS/Linux 可执行 `mkdir -p materials/demo`，Windows 使用 `mkdir materials\demo`）：

   ```bash
   mkdir -p materials/demo
   ffmpeg -hide_banner -y -f lavfi -i "sine=frequency=440:duration=2" \
     -f lavfi -t 0.8 -i anullsrc=r=48000:cl=mono \
     -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1[aout]" -map "[aout]" materials/demo/demo.wav
   ```

3. **编写示例 EDL**（保存为 `materials/demo/demo.keepLast.edl.json`，内容可直接复制上方“新式结构”示例）。

4. **执行渲染**：

   ```bash
   python scripts/edl_render.py \
     --edl materials/demo/demo.keepLast.edl.json \
     --audio-root materials \
     --out out
   ```

   成功后将在 `out/demo.clean.wav` 获得去噪后的音频，并在终端看到片段数量与累计保留时长。

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

## 小程序使用说明

- 首页显示「关卡、目标、得分、总分、最佳分」
- 通过滑动进行操作，通关弹窗询问进入下一关
- 点击「重开本关」可重置当前关卡
- 逻辑与 Web 版复用同一套核心 API（CJS 版）

## 不提交二进制/媒体的约定

`data/audio/`、`data/asr-json/`、`data/original_txt/`、`out/` 目录全部不入库，原因是涉及版权、容量与隐私数据，需在本地或受控环境中管理。

## 免责声明与隐私

仅处理你有权使用的音频与文本；请勿将受版权保护素材上传至公共仓库；建议在本地或受控环境中处理敏感数据。

## 更新日志

- 2025-11-05：新增 `scripts/env_check.py` 环境自检脚本、统一日志工具，并在主菜单/CLI 接入日志，补充排障文档。
- 2025-11-04：新增 `scripts/onepass_cli.py` 统一命令行、`onepass/batch_utils.py` 批处理工具以及主菜单 `[A]` 一键流水线入口，覆盖整书批处理与报告输出。
- 2025-11-03：新增 `config/default_char_map.json`、`scripts/normalize_original.py` 与主菜单 `[P]` 原文规范化入口，提供可配置管线与归一报表。
- 2025-11-02：新增统一 ASR 适配层、`scripts/retake_keep_last.py`、主菜单 `[K]` 单文件流程与示例素材，提供“保留最后一遍”一站式导出。
- 2025-11-01：新增 `onepass.edl_renderer` 模块与 `scripts/edl_render.py`，主菜单支持按 EDL 渲染干净音频，并补充文档示例与最小跑通流程。

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
