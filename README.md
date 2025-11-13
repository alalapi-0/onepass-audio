# OnePass Audio — 录完即净，一遍过

## 5 分钟跑通

### 快速前置

- 安装 Python 3.10 及以上版本，建议使用虚拟环境隔离依赖。
- 确保 `ffmpeg` 与 `ffprobe` 在 PATH 中（用于生成演示音频与最终渲染）。
- （可选）安装 `opencc`，以启用繁简转换提示。

### 一键跑通

- **Windows**：
  - `python scripts/smoke_test.py`
  - 或 `powershell -ExecutionPolicy Bypass -File .\scripts\demo_run.ps1`
- **Linux / macOS**：
  - `python scripts/smoke_test.py`
  - 或 `bash scripts/demo_run.sh`
    - 首次运行前请执行 `chmod +x scripts/demo_run.sh`

### 将看到的产物

- 文本类：`out/demo.keepLast.srt`、`out/demo.keepLast.txt`、`out/demo.audition_markers.csv`、`out/demo.keepLast.edl.json`
- 若系统已安装 `ffmpeg`：`out/demo.clean.wav`

### 若出现问题

- 运行环境自检：`python scripts/env_check.py --out out --verbose`
- 提交 issue 时附带 `out/env_report.json` 与 `out/logs/...` 中的日志片段，有助于快速定位问题。

### 示例数据说明

- 仓库仅收录 `materials/example/demo.txt` 与 `materials/example/demo.words.json` 两个文本示例。
- 演示音频 `materials/example/demo.wav` 由脚本在本地即时生成，不会随仓库提交。

### 轮次能力串联演示（第 1～5 轮）

1. **第 1 轮：目录骨架与交互主程序** —— 运行 `python onepass_main.py` 体验主菜单与交互流程。
2. **第 2 轮：保留最后一遍策略** —— 运行 `python scripts/retake_keep_last.py --words-json materials/example/demo.words.json --text materials/example/demo.txt --out out`，生成字幕、文本、EDL 与 Audition 标记。
3. **第 3 轮：统一 CLI 与批处理** —— 使用 `python scripts/onepass_cli.py all-in-one --materials materials --out out`，串联规范化、保留最后一遍与（可选）渲染。
4. **第 4 轮：环境自检与日志** —— `python scripts/env_check.py --out out --verbose`，核对依赖、PATH 与读写权限，同时掌握日志目录位置。
5. **第 5 轮：EDL 音频渲染** —— `python scripts/edl_render.py --edl out/demo.keepLast.edl.json --audio-root materials/example --out out --samplerate 48000 --channels 1`；若想自动补齐 `source_audio`，可先运行 `python scripts/edl_set_source.py --edl out/demo.keepLast.edl.json --source materials/example/demo.wav`。
6. **综合示例** —— `python scripts/smoke_test.py` 会自动完成上述关键步骤的最小闭环演示。

本项目是一键生成去口癖、保留“同句最后一遍”的干净字幕，并可选按剪辑清单导出干净音频的工具集（MVP）。

## 技术栈与核心组件

- **编程语言与运行时**：基于 Python 3.10+，核心逻辑集中在 `onepass/` 包内；命令行脚本依托 `argparse`、`pathlib` 等标准库构建交互。 【F:onepass_main.py†L1-L115】【F:scripts/onepass_cli.py†L1-L115】
- **文本处理**：通过自研的 `onepass.textnorm` 管线与 `rapidfuzz` 完成句子规范化与模糊匹配，对齐策略在 `onepass.align` 中实现。 【F:onepass/textnorm.py†L1-L200】【F:onepass/align.py†L1-L120】
- **音视频工具链**：调用 `ffmpeg`/`ffprobe` 进行音频探测与拼接，相关封装位于 `onepass.edl_renderer`。 【F:onepass/edl_renderer.py†L1-L200】
- **Web 可视化面板**：前端由 `web/index.html`、`web/style.css`、`web/app.js` 组成，依赖 WaveSurfer.js 完成音频波形渲染；后端采用 Flask + flask-cors 提供本地 API。 【F:web/index.html†L1-L120】【F:web/app.js†L1-L160】【F:scripts/web_panel_server.py†L1-L120】
- **命令行自动化**：`scripts/onepass_cli.py` 串联批处理、文本规范化、保留最后一遍与音频渲染；`scripts/smoke_test.py`、`scripts/demo_run.*` 负责最小演示。 【F:scripts/onepass_cli.py†L1-L120】【F:scripts/demo_run.sh†L1-L5】

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
5. **第 5 轮：EDL 音频渲染落地** —— 新增 `onepass.edl_renderer` 库模块、`scripts/edl_render.py` 命令行脚本、`scripts/edl_set_source.py` 辅助工具与 `scripts/smoke_test.py` 最小示例，实现按剪辑清单一键导出干净音频，并完善“5 分钟跑通”文档。
   - 关键决策：借助 `ffprobe` 探测时长、`ffmpeg concat` 拼接保留片段，并允许 `--dry-run` 输出命令供人工验证。
   - 典型问题：遇到旧版 EDL 中 `actions`/`segments` 字段混用，需要在 Loader 层兼容并输出清晰错误信息。
6. **第 6 轮：中文注释与整体综述** —— 针对仓库内所有代码补充中文注释，并在根目录 README 中整理环境准备、技术栈、运行步骤与 Prompt 演进纪要，确保零基础读者亦能迅速理解流程。
   - 关键决策：统一 Web 前端、Flask 服务与脚本层的注释风格，明确“交互式菜单 ↔ CLI ↔ Web 面板”三条使用路径。
   - 典型问题：在 JavaScript/CSS 中补注释需避免破坏现有打包结构，因此采用段落式注释说明状态管理、DOM 交互与样式分区。

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

## 项目运行流程详解（零基础版）

### 交互式主程序 `onepass_main.py`

1. **启动入口**：执行 `python onepass_main.py` 时，`main()` 会调用 `_print_banner()` 展示版本信息、读取默认路径，并通过 `_prompt_materials_directory()` / `_ensure_output_directory()` 引导用户选择素材与输出目录。 【F:onepass_main.py†L1-L120】【F:onepass_main.py†L404-L520】
2. **素材扫描**：`_scan_materials()` 会按照 `*.words.json` → `*.norm.txt` → `*.txt` 的优先级配对章节资源，并利用 `_choose_audio_for_stem()` 基于 `AUDIO_PRIORITY` 字典挑选音频文件。 【F:onepass_main.py†L226-L403】
3. **章节处理**：`_process_chapter()` 负责单章节流程：
   - 调用 `load_words()` 解析词级 JSON 并生成 `Word` 序列；
   - 通过 `prepare_sentences()` 清洗原稿文本，再交给 `align_sentences()` 计算模糊对齐窗口；
   - 使用 `compute_retake_keep_last()` 生成保留最后一遍的片段，随后分别调用 `export_srt()`、`export_txt()`、`export_edl_json()` 与 `export_audition_markers()` 输出字幕/文本/EDL/标记；
   - 若启用音频渲染，则利用 `normalize_segments()`、`render_audio()` 输出干净音频并记录剪辑秒数。 【F:onepass_main.py†L121-L403】【F:onepass/asr_loader.py†L1-L200】【F:onepass/pipeline.py†L1-L120】【F:onepass/align.py†L1-L160】【F:onepass/retake_keep_last.py†L1-L200】【F:onepass/edl_renderer.py†L1-L200】
4. **结果汇总**：循环结束后，`main()` 会汇总 `ChapterSummary` 列表，逐条打印保留句数、重复窗口、未对齐句子与剪切时长，并提示输出位置。 【F:onepass_main.py†L404-L520】

### 统一命令行 `scripts/onepass_cli.py`

1. **命令解析**：脚本基于 `argparse` 注册 `prep-norm`、`retake-keep-last`、`render-audio`、`all-in-one`、`review-mode` 等子命令，统一入口为 `main()`。 【F:scripts/onepass_cli.py†L1-L160】【F:scripts/onepass_cli.py†L480-L720】
2. **文本规范化 (`prep-norm`)**：`_process_single_text()` 会串行执行 `normalize_pipeline()`、`run_opencc_if_available()`、`scan_suspects()`，输出 `.norm.txt` 与 `normalize_report.csv`。 【F:scripts/onepass_cli.py†L160-L320】【F:onepass/text_norm.py†L1-L200】
3. **保留最后一遍 (`retake-keep-last`)**：`_run_retake_keep_last()` 加载词级 JSON 与原稿，调用 `compute_retake_keep_last()` 生成去口癖片段，随后导出字幕、文本、EDL 与 Audition 标记，并根据参数生成调试 CSV。 【F:scripts/onepass_cli.py†L320-L520】【F:onepass/retake_keep_last.py†L1-L200】
4. **音频渲染 (`render-audio`)**：`_run_render_audio()` 读取 EDL、通过 `resolve_source_audio()` 定位素材、调用 `render_audio()` 执行 `ffmpeg concat`，生成 `.clean.wav`。 【F:scripts/onepass_cli.py†L520-L720】【F:onepass/edl_renderer.py†L1-L200】
5. **批处理 (`all-in-one`)**：`_run_all_in_one()` 依次执行规范化、保留最后一遍与（可选）音频渲染，自动写入 `batch_report.json`。命令执行完毕后会输出统计摘要与对应 CLI 示例，便于复现。 【F:scripts/onepass_cli.py†L720-L960】

#### 中文分句模式与验收

- **三种模式**：`--split-mode punct` 仅依赖 `。！？；…` 等强标点；`--split-mode all-punct` 会在强标点之外追加 `，、：—` 等弱断点；`--split-mode punct+len`（默认）先按强标点分句，再根据 `--min-len/--max-len/--hard-max` 自动在弱标点、空白或逗号处分块，并将过短片段与邻句合并。
- **调参与护栏**：`--min-len` 控制短句合并阈值，`--max-len` 决定二次切分的软上限，`--hard-max` 则在超长句子中强制寻找空白/逗号断开；`--weak-punct-enable/--no-weak-punct-enable` 用于全局开关弱断点，`--keep-quotes/--no-keep-quotes` 用于指定括号/引号内部是否跳过弱断点。若 `.align.txt` 仍只有 1 行，流水线会自动切回 `punct+len` 并应用 `min_len=8/max_len=20/hard_max=28` 再跑一遍，同时在 `batch_report.json` 中记录 `align_guard_triggered/align_guard_failed` 以及最终使用的分句参数。
- **调试文件**：生成 `.align.txt` 时会同步写出 `.align.debug.tsv`，包含 `idx/start_char/end_char/text_preview` 四列，可快速定位句段与字符区间。
- **统计与验收**：`batch_report.json` 的 `prep_norm.items[]` 现会附带 `align_total_lines/split_mode/min_len/max_len/hard_max/weak_punct_enable` 等字段。R2 需要至少 100 行且使用 `punct+len`，可运行 `python scripts/check_r2_alignment.py out/batch_report.json` 自动列出 PASS/FAIL 并附上 `.align.txt` 与 `.align.debug.tsv` 前 5 行。

常用示例：

```bash
python scripts/onepass_cli.py prep-norm \
  --in materials/example \
  --out out/norm \
  --emit-align --split-mode punct+len --min-len 8 --max-len 24 --hard-max 32 \
  --weak-punct-enable --keep-quotes
```

### Web 可视化面板

1. **本地服务**：`scripts/web_panel_server.py` 提供 `/api/list`、`/api/file`、`/api/save_edl`、`/api/save_markers_csv` 等接口；内部通过 `_build_list_payload()` 列举 `out/` 目录成果，保存文件时校验路径防止越权。 【F:scripts/web_panel_server.py†L1-L200】【F:scripts/web_panel_server.py†L200-L360】
2. **前端结构**：`web/index.html` 定义侧边栏、波形区与区域列表；`web/style.css` 设置亮/暗配色、布局、状态徽章样式。 【F:web/index.html†L1-L120】【F:web/style.css†L1-L160】
3. **交互逻辑**：`web/app.js` 初始化 WaveSurfer 波形组件、维护区域状态机、调用本地 API 获取 `out/` 文件并支持导出手工标记。脚本在启动时自动检测服务状态，提供启动提示并允许离线浏览静态页面。 【F:web/app.js†L1-L160】【F:web/app.js†L160-L360】

### 全流程串联（建议操作顺序）

1. 使用 `python scripts/smoke_test.py` 验证依赖是否就绪并生成演示素材。
2. 若有自有文本，先运行 `python scripts/normalize_original.py --in <素材目录> --out out/norm` 生成 `.norm.txt`。
3. 运行 `python onepass_main.py`，选择“批量处理”自动输出字幕/文本/EDL/标记；如需音频同时勾选渲染。
4. 若倾向命令行批处理，可改用 `python scripts/onepass_cli.py all-in-one --materials <素材目录> --out out/批次名`。
5. 需要人工复核时，执行 `python scripts/web_panel_server.py --open` 启动本地服务，并在浏览器访问 `http://127.0.0.1:8088` 以拖动波形、导出手工标记。
6. 最终在 `out/` 目录集中整理 `.srt/.txt/.edl.json/.audition_markers.csv/.clean.wav` 等成果，并结合 `out/logs/`、`out/normalize_report.csv` 查看统计信息。

该流程覆盖了交互式菜单、命令行批处理与 Web 审听三条主线，便于根据团队分工灵活选择。

### 从零开始的进阶演示

1. 运行 `python scripts/smoke_test.py`（或 `bash scripts/demo_run.sh` / `powershell -File scripts/demo_run.ps1`）验证最小示例可以无配置跑通。
2. 打开 `out/demo.keepLast.edl.json` 与 `out/demo.audition_markers.csv`，观察“保留最后一遍”策略如何仅留下第二次朗读的片段。
3. 若需进一步体验交互流程，可执行 `python onepass_main.py`，按菜单提示选择素材目录、输出目录与是否渲染音频。
4. 结合 `python scripts/onepass_cli.py all-in-one --materials materials/example --out out/cli_demo` 感受批处理汇总与报告输出。
5. 对于自有素材，可先运行 `python scripts/normalize_original.py --in <your_dir> --out out/norm` 完成规范化，再重复以上步骤。

### 常见问题（FAQ）

- **提示缺少 opencc**：若需要繁转简，请 `pip install opencc`；如果无需该功能可忽略提示。
- **ffmpeg/ffprobe 未找到**：确认已安装 [FFmpeg](https://ffmpeg.org/)，并将其加入 `PATH`。
- **字幕为空或缺句**：检查 JSON 是否存在 `words` 字段及时间戳是否递增，可调整 `--score-threshold` 重新执行。
- **批处理配对失败**：确保文件前缀完全一致（区分大小写），或在 CLI 中修改 `--glob-words`、`--glob-text` 模式。
- **为什么 clean.wav 被剪掉很多？**：先运行 `prep-norm`（默认包含硬换行合并）生成 `.norm.txt`，再使用 `retake-keep-last` 并适当调整 `--min-sent-chars`、`--max-dup-gap-sec`、`--max-window-sec` 阈值，例如：

  ```bash
  python scripts/onepass_cli.py retake-keep-last \
    --materials materials/book1 \
    --out out/book1 \
    --min-sent-chars 12 \
    --max-dup-gap-sec 30 \
    --max-window-sec 90
  ```

## 句子级审阅模式（更安全的整句剪裁）

第七轮新增的句子级审阅模式在默认“保留最后一遍”逻辑之外提供了更保守的整句对齐流程：

- **极低误剪风险**：只有完整命中的句子才会进入 keep 段；其余句子不会被剪掉，而是生成 [REVIEW]/[LOW] 标记供 AU 人工复核。
- **更友好的默认阈值**：25 秒重录间隔、1 秒合并窗口、0.78 低置信阈值，兼顾重复朗读和安全边界。
- **高级切句规则**：不会在 `3.14`、`example.com`、`Dr.`、`《书名》`、中文引号/括号等场景下误切句，省略号（`……`、`...`）也会视为单次终止。

两种常见用法如下：

**1. 纯审阅（不剪音频）**

```bash
python scripts/onepass_cli.py retake-keep-last \
  --words-json materials/001序言01.words.json \
  --text out/norm/001序言01.norm.txt \
  --out out \
  --sentence-strict --review-only
```

- `*.sentence.edl.json` 只包含一段 `[0, T]` 的 keep；`clean.wav` 时长≈原始音频。
- `*.sentence.audition_markers.csv` 同时列出命中句（L 开头）和审阅点（R 开头）。

**2. 仅剪整句命中（更稳的自动剪辑）**

```bash
python scripts/onepass_cli.py retake-keep-last \
  --words-json materials/001序言01.words.json \
  --text out/norm/001序言01.norm.txt \
  --out out \
  --sentence-strict
```

- `*.sentence.keep.srt` / `*.sentence.keep.txt` 只保留整句命中；EDL 由多个 keep 段组成，不会出现“一刀切整篇”的情况。
- 可按需通过 `--low-conf`、`--merge-adj-gap-sec`、`--max-dup-gap-sec` 微调阈值，也可沿用默认的保守配置。

> `--sentence-strict` 关闭时仍使用旧的“行级保留最后一遍”逻辑，保持向后兼容。`--review-only` 仅在开启句子模式时生效。

## 停顿感知对齐与静音探测

第七轮新增的停顿感知流程会在导出前统一对所有 keep 段执行“吸附 → 余量 → 自动合并 → 碎片剔除”四步调整：

- **停顿吸附**：默认以 `0.45s` 的词间间隔识别自然停顿，段首尾可在 `0.20s` 范围内吸附到最近的停顿边界。
- **静音并集**：若系统可用 `ffmpeg`，会通过 `silencedetect` 获取静音区间，与词间停顿合并后统一作为吸附候选。若未安装 `ffmpeg`，则自动退回到“仅使用词间 gap”的逻辑，输出仍保持一致。
- **段首尾余量**：默认在段首、段尾分别留出 `0.08s` 与 `0.12s` 的缓冲，避免剪到吐字。
- **碎片保护**：补偿后相邻片段间隔小于 `0.06s` 会自动合并，最终片段若仍短于 `0.18s` 则尝试与前后段并入或直接丢弃。

调试时可配合 `--debug-csv path` 观察每段吸附与合并前后的时间节点。示例命令如下：

```bash
# 行级模式 + 停顿吸附 + 保护
python scripts/onepass_cli.py retake-keep-last --materials materials --out out \
  --pause-gap-sec 0.45 --pad-before 0.08 --pad-after 0.12 --overcut-mode ask

# 句子级（更稳）+ 停顿吸附 + 单段审阅（不剪）
python scripts/onepass_cli.py retake-keep-last --words-json ... --text ... --out out \
  --sentence-strict --review-only
```

运行结束后可在日志中看到 `pause_used`、`pause_snaps`、`auto_merged`、`too_short_dropped` 等统计字段，便于快速评估参数效果。

## 过裁剪保护

为了避免阈值过激导致大量有效语句被剪掉，CLI 在吸附/合并完成后会根据 `keep` 总时长计算剪切比例 `cut_ratio`：

- `--overcut-threshold`（默认 `0.60`）用于判定是否触发保护。
- `--overcut-mode ask|auto|abort` 控制处理策略：
  - **ask**：在交互模式下给出 `auto / continue / abort` 三选一；若标准输入不可交互则自动回退到 `auto`。
  - **auto**：自动放宽参数（`min_sent_chars + 4`、`max_dup_gap_sec = 15`、`pause_gap_sec = 0.55`）并重新计算。
  - **abort**：直接中止任务并返回非零退出码。
- 执行结果会记录在 `stats.overcut_guard_action`，同时写入批处理报告，方便后续复盘。

若实际素材的剪切比例本就较高，可结合 `--min-sent-chars`、`--max-dup-gap-sec` 或直接切换到 `--sentence-strict` 进一步降低风险。

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
  --glob-edl "*.keepLast.edl.json" "*.sentence.edl.json" \
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
  --render --glob-edl "*.keepLast.edl.json" "*.sentence.edl.json" \
  --samplerate 48000 --channels 1 \
  --workers 4
```

### 句子级审阅（不剪未匹配，只打点）

- **适用场景**：重录次数多、原稿存在微调导致逐行匹配不稳定时，先生成整句命中与审阅标记，再由人工在 AU/DAW 中复核；尤其适合对“误剪”极度敏感的长节目。 
- **优点**：只保留整句完全命中的片段，未匹配与低置信句一律打点提醒，EDL 默认为整段 keep ⇒ 基本零误剪风险。缺点是需要人工在打点处做二次确认。
- **输出文件**：`*.sentence.keep.srt`、`*.sentence.keep.txt`、`*.sentence.audition_markers.csv`、`*.sentence.edl.json`。标记中 `L*` 代表命中的句子，`R*` 以 `[REVIEW]`/`[LOW]` 前缀提示未匹配或低置信候选。

最小命令示例（纯审阅，不剪音频）：

```bash
python scripts/onepass_cli.py retake-keep-last \
  --words-json materials/example/demo.words.json \
  --text materials/example/demo.txt \
  --out out \
  --sentence-strict --review-only
```

若仅需调试单个素材，可使用 `scripts/sentence_review.py`：

```bash
python scripts/sentence_review.py \
  --words-json materials/example/demo.words.json \
  --text materials/example/demo.txt \
  --out out/sentence-demo \
  --sentence-strict --review-only
```

将 `*.sentence.audition_markers.csv` 导入 Adobe Audition 后，`L*` 标记精确落在匹配成功的整句上；`R*` 标记则提示缺口或低置信命中，可沿时间轴逐一复核并手动剪辑。

#### 重录去重阈值说明

- `--min-sent-chars`：句长下限，默认 12。规范化后字符数不足此值的句子不会触发“只保留最后一遍”，避免“我们/因此”等短词被误判为重复段落。
- `--max-dup-gap-sec`：相邻命中间隔阈值（秒），默认 30。仅当两次出现的起始时间差不超过该值时，才会丢弃较早的一次命中。
- `--max-window-sec`：单个 drop 段的最长持续时间（秒），默认 90。超出时会自动拆分，避免 EDL 中出现数十分钟的超长剪切。
- `--merge-adj-gap-sec`：句子级模式下用于合并相邻命中的间隙阈值（秒），默认 1.2，可避免同一段话被拆成多个 keep 段。
- `--low-conf-threshold`：句子级模式下的相似度阈值，低于该值的命中会以 `[LOW]` 标记提醒人工复核。

### 配对规则与命名约定

- 批处理模式下以词级 JSON 的文件名前缀（去掉 `.words.json`）为基准，优先寻找 `<stem>.norm.txt`，若缺失则回退 `<stem>.txt`。
- 所有生成文件都保留同一 `stem` 前缀：`<stem>.keepLast.srt/.txt/.edl.json/.audition_markers.csv` 与 `<stem>.clean.wav`。
- `batch_report.json` 会分阶段记录 `items` 与 `summary`，方便统计成功、失败、耗时及聚合指标。

### 并发参数与平台注意事项

- `--workers` 允许并发处理多个条目，未指定时默认为串行；在 Windows 平台批量调用时，请确保入口脚本带有 `if __name__ == "__main__":` 保护。
- 渲染阶段若不指定 `--samplerate`/`--channels`，将沿用 EDL 中的建议或源音频探测结果。

### 与交互入口的关系

主菜单 `[1]` 会在批量流程前询问是否先规范化原文、是否紧接着渲染干净音频，并回显等价的 CLI 命令；`[P]`、`[K]`、`[R]` 也会展示对应命令，便于将交互式操作迁移到自动化流水线。更细致的文本规范化、保留最后一遍与渲染音频说明，可继续参考下方对应章节。

## 原文规范化（可配置）

在正式对齐前先清洗原稿，可以显著降低零宽字符、兼容字和混排空白造成的错位，从而提高“保留最后一遍”匹配的成功率。项目提供了可编辑的
`config/default_char_map.json`，对常见中文排版场景进行兜底处理：

- `delete`：列出需要直接删除的字符，默认涵盖零宽空白与 BOM。
- `map`：指定需要替换的兼容字符与标点，例如弯引号、破折号、中文逗号等。
- `normalize_width` / `preserve_cjk_punct`：控制是否执行 NFKC 宽度归一，并在归一后回写全角中文标点。
- `normalize_space`：折叠多余空白、清理行首尾空格，避免对齐时出现看不见的分隔符。

脚本会在写出 `<stem>.norm.txt` 的同时默认生成 `<stem>.asr.txt`，用于驱动语音对齐或 ASR 粗分句。`--profile asr` 预设会一键启用“去换行 + 去破折号 + 去标点”的极简策略，并可通过 `--strip-punct-mode keep-eos|all` 决定是否保留句末 `。！？`。若仅需传统规范化，可在菜单选择“仅 .norm”或直接传入 `--no-emit-asr` 关闭额外输出。常用组合示例：

```bash
# 仅生成 .norm.txt，保留原始标点
python scripts/normalize_original.py --in materials/example/demo.txt --out out/norm --no-emit-asr

# 为 ASR 预处理生成极简文本，保留句末 。！？
python scripts/normalize_original.py --in materials/example/demo.txt --out out/norm --profile asr --strip-punct-mode keep-eos

# 为完全去标点的场景生成 .asr.txt
python scripts/normalize_original.py --in materials/example/demo.txt --out out/norm --profile asr --strip-punct-mode all
```

另外提供了白名单式的危险字形映射，可在确认无误后通过 `--glyph-map data/cjk_compat_safe.json` 启用。映射文件仅包含安全的兼容字符 → 常用写法替换，避免误把偏旁部首改成完整汉字。

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
- [x] 示例与 5 分钟跑通脚本（`scripts/smoke_test.py` + `scripts/demo_run.*`）

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

默认会在指定 `--out` 目录写入 `env_report.json`，其中 `summary.notes` 汇总可忽略但需要关注的提醒，`checks` 数组与终端表格保持一致。`--verbose` 会额外打印探测命令与返回码，便于人工复现。

```json
{
  "timestamp": "2025-11-05T10:00:00",
  "platform": {"system": "Windows", "release": "10", "machine": "AMD64"},
  "python": {"version": "3.11.6", "ok": true, "in_venv": true},
  "summary": {"ok": true, "notes": ["opencc 未安装，繁简转换将被跳过。"]},
  "checks": [
    {"name": "Python 版本", "status": "ok", "detail": "当前版本 3.11.6", "advice": ""}
  ]
}
```

### 日志位置与查看技巧

- 所有脚本与 CLI 会自动写入 `out/logs/YYYY-MM-DD/onepass-YYYYMMDD.log`，并同步输出到控制台。
- 建议在编辑器或终端中搜索 `[ERROR]`、`[WARNING]` 或 `exception` 关键字快速定位异常。
- 日志目录会在首次运行时自动创建，也可通过主菜单 `[E]` 快速查看实际路径。

### 常见故障及建议

- `ffmpeg` 或 `ffprobe` 未安装/未加入 PATH：按照官方指引安装，并确认命令行可直接运行 `ffmpeg -version`。
- Windows 路径包含特殊字符、未转义反斜杠或禁用长路径：优先使用不含空格/点结尾的目录，必要时在注册表启用 `LongPathsEnabled` 策略（自检会检测并提示）。
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

## 可视化控制台使用说明

1. **启动方式**
   - 在交互菜单中选择 `[W] 启动可视化控制台（本地网页）`，程序会自动寻找 8088–8090 的空闲端口并运行 `scripts/web_panel_server.py`，随后打开浏览器访问控制面板。
   - 也可手动执行 `python scripts/web_panel_server.py --port 8088`，再访问 `http://127.0.0.1:8088/web/index.html`。
2. **页面概览**
   - 左侧按 stem 聚合 `out/` 目录下的音频与标记文件，右侧展示波形、区域列表与播放控制，支持跳过“删除”区段试听。
   - 区域支持 Alt 拖拽创建、拖动/拉伸边界、批量切换状态、快捷键（Space/D/←→/Delete/Ctrl+S）等操作。
   - 截图占位：`<在此插入可视化控制台页面截图>`。
3. **导出文件与注意事项**
   - 导出的人工决策会写入 `out/<stem>.manual.edl.json` 与 `out/<stem>.manual.audition_markers.csv`；CSV 采用 UTF-8 BOM 编码，行结尾为 CRLF，适配 Audition 导入。
   - 面板仅允许读取/写入 `out/` 子目录，若解析失败会在页面弹出提示并在控制台输出详细日志。
   - 若端口被占用，主菜单会自动向上递增端口；关闭服务可在终端按 `Ctrl+C`。

## 不提交二进制/媒体的约定

`data/audio/`、`data/asr-json/`、`data/original_txt/`、`out/` 目录全部不入库，原因是涉及版权、容量与隐私数据，需在本地或受控环境中管理。

## 免责声明与隐私

仅处理你有权使用的音频与文本；请勿将受版权保护素材上传至公共仓库；建议在本地或受控环境中处理敏感数据。

## 更新日志

- 2025-11-06：补齐 `materials/example/` 文本示例，新增 `scripts/smoke_test.py`、`scripts/edl_set_source.py` 及跨平台包装脚本，完善 README《5 分钟跑通》。
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
