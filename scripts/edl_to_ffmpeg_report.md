# edl_to_ffmpeg.py

- 最后更新时间：2025-10-26 14:47:20 +0900

## 功能概述
将 EDL 剪切动作转换为 ffmpeg 拼接命令的实用脚本，支持自动探测 ffprobe、计算保留区间、切片输出临时音频并生成 concat 列表，可 dry-run 查看命令。

## 关键职责
- 校验 ffmpeg 可用性、推断 ffprobe 路径并测量源音频时长。
- 使用 `edl_to_keep_intervals` 计算保留片段，按区间调用 ffmpeg 切割并生成 concat 文件。
- `run_cmd`、`format_cmd` 封装子进程执行与打印，`CommandError` 捕获失败。
- 支持 `--dry-run`、自定义 ffmpeg 路径和输出目录，方便跨平台运行。
