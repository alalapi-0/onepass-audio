# retake_keep_last.py

- 最后更新时间：2025-11-01 23:50:31 +0900

## 功能概述
“保留最后一遍”整合导出脚本，读取词级 ASR JSON 与原文 TXT，调用核心算法生成字幕、纯文本、Audition 标记与 EDL，支持记录源音频和音频参数。

## 关键职责
- 解析必要的输入路径与可选音频元数据，使用统一日志系统记录流程。
- 调用 `load_words` 与 `compute_retake_keep_last` 获取匹配结果和统计信息。
- 根据输入自动推导输出文件前缀，写入 SRT、TXT、CSV 标记和 EDL JSON，并输出匹配摘要。
