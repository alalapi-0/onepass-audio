# batch_utils.py

- 最后更新时间：2025-11-01 22:32:09 +0900

## 功能概述
提供批处理脚本常用的文件匹配与报告工具，帮助根据素材文件名推导文本稿、输出相对路径以及写入 JSON 摘要。

## 关键职责
- `iter_files` 递归按多重 glob 模式收集文件并去重排序。
- `stem_from_words_json`、`find_text_for_stem` 根据 ASR JSON 推断对应文本稿。
- `safe_rel` 和 `write_json` 帮助生成报表友好的相对路径及 UTF-8 JSON 文件。
