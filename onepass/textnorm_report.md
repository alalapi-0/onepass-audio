# textnorm.py

- 最后更新时间：2025-11-02 01:32:41 +0900

## 功能概述
提供旧版文本规范化 API 的兼容实现，包含句子拆分、空白压缩、token 化以及字符映射配置，供对齐流程和其他脚本沿用既有接口。

## 关键职责
- 定义 `Sentence` 数据类，并暴露 `split_sentences`、`normalize_sentence`、`tokenize_for_match` 等核心函数。
- 提供 `TextNormConfig`、`DEFAULT_COMPAT_MAP` 以及加载自定义映射、扫描可疑字符等辅助工具。
- 支持中英标点互换、NFKC 归一与零宽字符提示，满足不同语言场景下的文本清洗需求。
