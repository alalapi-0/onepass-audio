# smoke_test.py

- 最后更新时间：2025-11-02 02:08:18 +0900

## 功能概述
最小可复现实例脚本，串联保留最后一遍导出、写入 source_audio、可选音频渲染等步骤，对仓库内示例素材执行全流程自检，验证依赖配置是否正确。

## 关键职责
- 检查示例 JSON/TXT 是否存在，提示 opencc、ffmpeg、ffprobe 的可用状态。
- 依次调用 `retake_keep_last.py`、`edl_set_source.py`、`edl_render.py`，并在缺少 ffmpeg/ffprobe 时跳过渲染。
- 使用统一日志记录和 `_run_command` 封装子进程执行，遇到错误时输出友好提示并中断。
- 创建演示音频（若可用）并报告生成的字幕、文本、标记、EDL 和干净音频路径。
