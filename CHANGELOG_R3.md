# R3 - EDL 音频定位与路径鲁棒化

## 新增与变更
- `retake-keep-last` 与 `all-in-one` 子命令新增 `--audio-root`、`--prefer-relative-audio/--no-prefer-relative-audio`、`--path-style` 参数，用于控制 EDL 中的路径写入策略与渲染搜索根目录，参数快照同步记录这些开关。
- EDL 写出逻辑统一由 `EDLWriteResult` 返回写入的源音频信息，始终写入 `source_audio`（统一 POSIX `/`）、`source_audio_basename`、`path_style` 字段，必要时注入占位 KEEP 段，且会在可能时将路径相对化至 `audio_root`。
- 渲染器 `resolve_source_audio` 支持多层回退（绝对路径、音频根、EDL 同目录、文件名扫描、扩展名替换）并记录尝试列表，找不到时返回 `None`（非严格模式）且由调用侧记录 `render_skipped_reason`。
- `batch_report.json` 记录 `audio_root`、`source_audio_written`、`render_skipped_reason`、`match_engine`、`timed_out` 等字段，日志快照同步输出新的参数。

## 兼容性
- 默认行为保持写入 POSIX 风格路径并优先相对化至 `--audio-root`，若用户未显式指定 `--audio-root`，`retake-keep-last` 取素材目录，`all-in-one` 取输入目录。
- 渲染阶段在音频缺失时仅跳过当条任务；`resolve_source_audio` 在严格模式下仍抛错，以兼容独立脚本流程。

## 其他
- 所有读取报告的 `read_text` 均改为 `encoding="utf-8"` / `utf-8-sig` 并使用 `errors="replace"`，减轻 GBK 等编码环境异常。
- `probe_duration` 子进程输出统一按 UTF-8 解码以避免 Windows 平台 GBK 提示。
