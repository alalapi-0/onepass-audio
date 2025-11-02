# edl_renderer.py

- 最后更新时间：2025-11-02 01:37:27 +0900

## 功能概述
封装从 EDL JSON 到最终音频导出的全过程，包括解析片段定义、定位源音频、探测音频属性、计算保留/删除段以及构建 ffmpeg 命令行执行裁剪或静音填补。

## 关键职责
- 定义 `EDLSegment`、`EDLDoc` 数据类描述 keep/drop 片段及 EDL 元信息。
- `load_edl` 兼容新旧字段结构，校验并转换为内部片段列表。
- `resolve_source_audio`、`probe_duration` 定位音频文件并获取时长、声道等参数。
- `normalize_segments`、`build_filter_complex` 将片段转换为 ffmpeg filtergraph。
- `render_audio` 负责拼装并执行 ffmpeg 命令，输出裁剪后的音频文件。
