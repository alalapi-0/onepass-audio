# retake_keep_last.py

- 最后更新时间：2025-11-02 01:43:11 +0900

## 功能概述
实现“保留最后一遍”策略的核心算法：将原文行与规范化后的 ASR 词序列匹配，找出每行最后一次出现的时间区间，并生成字幕、纯文本、Audition 标记及 EDL 输出。

## 关键职责
- 定义 `KeepSpan`、`RetakeResult` 数据类，描述保留行及整体统计。
- 提供多种辅助函数用于规范化词、构建字符索引、匹配最长公共子串和区间映射。
- `compute_retake_keep_last` 逐行匹配原稿，区分严格命中与回退匹配，并统计未匹配行。
- `export_srt`、`export_txt`、`export_audition_markers`、`export_edl_json` 等函数输出不同格式成果。
