# R5 更新说明

## 新增功能概览
- 引入 `serve-web` 子命令，可直接扫描 out/ 成果并启动本地 FastAPI 服务，默认提供静态界面与 REST API。
- `all-in-one` 命令新增 `--serve` / `--open-browser`，流水线完成后自动启动控制台并（可选）打开浏览器。
- 新增 `onepass/web_server.py`，基于 FastAPI 提供音频流、EDL/CSV/SRT 解析、导出及后端渲染接口，支持路径 token 映射与跨平台文件名。
- 前端重构：`web/index.html`、`web/style.css`、`web/app.js`，加入 stem 列表、波形预览、多音频模式（原始/预览/剪辑成品）、区域表格与字幕面板；支持软跳播、批量状态操作、后端导出与渲染。
- `onepass/edl_renderer.py` 新增 `build_filter_pipeline`，在构造 ffmpeg filter_complex 时支持分块拼接以规避超长命令。
- requirements 增补 `fastapi`、`uvicorn`、`python-multipart` 以满足服务端依赖。

## API 摘要
- `GET /api/list`：按 stem 聚合成果，返回音频/EDL/CSV/SRT/TXT/对齐文件信息与安全 token。
- `GET /api/edl/{stem}` / `GET /api/csv/{stem}` / `GET /api/srt/{stem}`：读取并解析 EDL、Audition CSV、SRT。
- `GET /api/audio/{token}`：基于 token 流式返回音频文件。
- `POST /api/export/edl` / `POST /api/export/csv`：保存前端编辑后的区域数据，回写到 out/。
- `POST /api/render`：读取 keep 段生成 ffmpeg filter 并输出 `out/clean/{stem}.clean.wav`（支持 `force` 重渲染）。
- `GET /api/debug/{stem}`：返回调试对齐文件（若存在）。

## 使用方式
1. 启动服务
   ```bash
   python scripts/onepass_cli.py serve-web \
     --out /path/to/out \
     --audio-root /path/to/materials \
     --host 127.0.0.1 --port 5173 --open-browser
   ```
2. 一键流水线后自动打开控制台
   ```bash
   python scripts/onepass_cli.py all-in-one ... --serve --open-browser
   ```
3. 前端页面左侧选择 stem，右侧波形即可预览；支持 Alt+拖拽建区、D 切换状态、Ctrl+S 导出 EDL。
4. 点击 “生成剪辑音频” 触发后端 `/api/render`，生成的剪辑文件可在“剪辑音频文件”标签播放。

## 注意事项
- 服务端统一使用 token 替代绝对路径，避免前端访问任意文件。
- Windows/Unix 文件名与中文路径均在后端统一转 POSIX 表示。
- `--open-browser` 依赖系统默认浏览器，若失败可在日志查看 URL 手动访问。
- 如需跨端调试，可在 `serve-web` 命令增加 `--cors`，允许 `http://localhost:*` 访问。
- 音频根目录的全量扫描结果默认缓存 5 秒，避免 `/api/list` 高频访问导致磁盘遍历过重，如需立即刷新可等待片刻或手动导入音频。
- `all-in-one --serve` 流水线若遇到 Web 服务启动失败，将记录错误但保持整体命令返回成功，可手动执行 `serve-web` 重试。
