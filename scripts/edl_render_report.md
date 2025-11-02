# edl_render.py

- 最后更新时间：2025-11-02 01:37:27 +0900

## 功能概述
按 EDL JSON 渲染干净音频的命令行脚本，自动定位源音频、归一化片段并调用 `onepass.edl_renderer.render_audio` 生成裁剪结果，支持 dry-run 查看命令。

## 关键职责
- 解析 EDL、音频根目录、输出目录、采样率、声道和 dry-run 等参数。
- 使用日志工具记录流程，校验参数合法性并捕获异常。
- 调用 `load_edl`、`resolve_source_audio`、`probe_duration`、`normalize_segments` 获取渲染所需数据。
- 根据是否 dry-run 执行或仅输出 ffmpeg 命令，并打印保留片段数量与累计时长。
