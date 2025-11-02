# make_markers.py

- 最后更新时间：2025-10-29 23:28:25 +0900

## 功能概述
根据 ASR 词级 JSON 与原始文本生成“保留最后一遍”的 EDL 和 Audition 标记文件，方便快速剪除重复重录段落。

## 关键职责
- 解析命令行参数，加载词序列与原稿文本，并调用 `prepare_sentences` 生成对齐用句子。
- 使用 `align_sentences`、`build_keep_last_edl` 计算剪切区间，写出标准化 EDL JSON 与标记 CSV。
- 输出命中统计与剪切总时长，帮助评估对齐效果。
