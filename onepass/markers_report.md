# markers.py

- 最后更新时间：2025-11-02 01:32:41 +0900

## 功能概述
根据生成的 EDL 决策表导出 Adobe Audition 可识别的标记 CSV，帮助后期工程师在 DAW 中快速定位需要剪切或检查的区间。

## 关键职责
- 以 `write_audition_markers` 函数遍历剪切动作，为每段重复窗口生成起点、终点与区间三类标记。
- 自动创建输出目录并按 Audition 兼容格式写入 UTF-8 CSV 文件。
