# 手动自检清单

按顺序执行以下步骤，可从 0 验证示例数据与真实章节流程。所有命令均在仓库根目录（`onepass/`）运行。

1. 创建虚拟环境并安装依赖

   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```

   预期：依赖安装成功，无报错。

2. 环境自检

   ```bash
   python scripts/env_check.py
   ```

   预期：生成 `out/env_report.json` 与 `out/env_report.md`，缺失项会给出修复建议。

3. 运行主程序（菜单）

   ```bash
   python onepass_main.py
   ```

   预期：终端出现 1–5 的菜单；选择 `1` 会调用环境自检。

4. 用 demo 跑单章（不渲染）

   ```bash
   python scripts/retake_keep_last.py --json examples/demo.json \
     --original examples/demo.txt --outdir out --aggr 50 --dry-run
   ```

   预期：在 `out/` 生成 `demo.keepLast.clean.srt/.vtt/.txt`、`demo.keepLast.edl.json`、`demo.keepLast.audition_markers.csv`、`demo.log`。

5. 检查 SRT/VTT

   预期：`*.srt` 使用 `HH:MM:SS,mmm` 时间格式；`*.vtt` 以 `WEBVTT` 开头并使用 `HH:MM:SS.mmm`。

6. 检查 EDL

   预期：`out/demo.keepLast.edl.json` 中 `version=1`，`actions` 数组存在且包含 `tighten_pause` 或为空数组。

7. Audition 标记导入（可选）

   操作：在 Adobe Audition 打开 Markers 面板 → `Import Markers…` → 选择 `out/demo.keepLast.audition_markers.csv`。

   预期：标记的时间与名称正确显示。

8. 准备真实章节素材并验证

   ```bash
   python scripts/validate_assets.py
   ```

   预期：生成 `out/validate_report.json`、`out/validate_report.md`、`out/validate_summary.csv`；缺项给出补齐指引。

9. 真实章节单章处理

   ```bash
   python scripts/retake_keep_last.py --json data/asr-json/001.json \
     --original data/original_txt/001.txt --outdir out --aggr 60
   ```

   预期：生成 5+1 件套（字幕/文本/EDL/标记/日志），`out/001.log` 内含统计摘要。

10. （可选）渲染干净音频

    ```bash
    python scripts/edl_to_ffmpeg.py --audio data/audio/001.m4a \
      --edl out/001.keepLast.edl.json --out out/001.clean.wav
    ```

    预期：输出 `out/001.clean.wav`，终端打印 ffmpeg 命令与耗时统计。

11. 批处理整本书

    ```powershell
    pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -Render
    ```

    预期：生成 `out/summary.csv` 与 `out/summary.md`；失败章节在表格中标记。

12. 错误处理测试

    - 将某个 JSON 暂时改名导致不匹配，再运行 `python scripts/validate_assets.py`：预期退出码为 2，并提示缺失文件。
    - 暂时移除（或屏蔽） `ffmpeg`，再运行渲染脚本：预期终端报错并以退出码 2 结束。

13. 返回码核对

    观察 `scripts/env_check.py`、`scripts/validate_assets.py`、`scripts/bulk_process.ps1` 的退出码：全部 OK 时为 0，存在 WARN 时为 1，出现 FAIL 时为 2。

14. 仓库清洁度

    ```bash
    git status --short
    ```

    预期：仅出现代码改动，不包含 `data/*`、`out/*` 或媒体文件。
