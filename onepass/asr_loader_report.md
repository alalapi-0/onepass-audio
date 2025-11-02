# asr_loader.py

- 最后更新时间：2025-11-02 01:43:11 +0900

## 功能概述
解析多种 ASR 词级 JSON 格式并统一为 `Word`/`ASRDoc` 数据结构，为对齐算法和后续处理提供干净的时间戳序列与元信息。

## 关键职责
- 定义 `Word`、`ASRDoc` 数据类，支持迭代、索引和元数据记录。
- `_word_from_raw`、`_iter_words_from_segment` 兼容不同字段命名与嵌套结构，过滤非法条目。
- `load_words` 支持 faster-whisper、FunASR 等常见格式，并在必要时排序纠错、记录修复标记。
