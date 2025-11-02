# edl_set_source.py

- 最后更新时间：2025-11-02 02:08:18 +0900

## 功能概述
为现有 EDL JSON 文件补充或更新 `source_audio` 字段的命令行工具，确保后续渲染或导出流程能够定位正确的源音频。

## 关键职责
- 解析 `--edl` 与 `--source` 参数，校验 EDL 文件存在性并加载 JSON。
- 使用 `onepass.logging_utils` 初始化日志记录，报告成功或失败原因。
- 将提供的音频路径以 POSIX 风格写入 `source_audio` 字段，并覆盖保存文件。
