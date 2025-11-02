# validate_assets.py

- 最后更新时间：2025-10-26 15:12:46 +0900

## 功能概述
检查 ASR JSON 与原始 TXT 素材是否存在同名 stem 的校验脚本，帮助在对齐前确认素材完整性，并在找到匹配项时给出下一步建议命令。

## 关键职责
- 校验 JSON 与 TXT 目录存在性，收集各自的文件 stem 集合。
- 统计两侧共有、仅 JSON、仅 TXT 的 stem，并按需输出示例。
- 在存在匹配 stem 时提示 `scripts/make_markers.py` 的示例调用，方便继续流水线。
