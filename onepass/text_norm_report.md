# text_norm.py

- 最后更新时间：2025-11-02 01:43:11 +0900

## 功能概述
实现新版的字符级规范化与对齐辅助函数，涵盖宽度统一、字符映射、空白处理、OpenCC 调用、嫌疑字符扫描，以及将文本转换为对齐友好的中日韩/拉丁序列。

## 关键职责
- `load_char_map`、`apply_char_map` 等函数负责加载并应用 JSON 配置化的字符替换规则，输出统计信息。
- `fullwidth_halfwidth_normalize`、`normalize_spaces` 等步骤组合在 `normalize_pipeline` 中形成可配置流水线。
- `run_opencc_if_available` 在存在 opencc 可执行文件时触发繁简转换。
- `normalize_for_align`、`cjk_or_latin_seq`、`build_char_index_map` 等函数用于对齐算法和字幕导出所需的字符序列处理。
