"""提供可视化标注面板所需的本地 Flask 服务。"""  # 模块用途：暴露 Web API 与静态资源
from __future__ import annotations  # 允许使用 Python 3.11+ 的联合类型注解

import argparse  # 解析命令行参数，支持自定义端口
import csv  # 保存 Audition 标记 CSV
import json  # 序列化/反序列化前端请求
from pathlib import Path  # 统一处理路径拼接与解析
from typing import Any, Iterable  # 类型提示，提升可读性

from flask import Flask, Response, jsonify, request, send_from_directory  # Flask 基础组件
from flask_cors import CORS  # 允许跨域访问，方便前端调用
from werkzeug.serving import make_server  # 手动控制 WSGI 服务器生命周期

ROOT_DIR = Path(__file__).resolve().parents[1]  # 项目根目录（包含 web/ 与 out/）
WEB_ROOT = ROOT_DIR / "web"  # 前端静态资源所在目录
OUT_ROOT = ROOT_DIR / "out"  # 输出文件目录，提供下载与列表数据
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}  # 支持的音频扩展名
MARKER_EXTENSIONS = {".audition_markers.csv", ".markers.csv", ".edl.json", ".srt"}  # 支持的标记类文件

app = Flask(__name__)  # 初始化 Flask 应用
app.json.ensure_ascii = False  # 允许直接输出中文 JSON
CORS(app)  # 开启跨域，便于静态页在 file:// 或其它端口访问


def _safe_resolve(base: Path, relative_path: str) -> Path:
    """确保 ``relative_path`` 位于 ``base`` 子目录下。"""  # 用于防止路径穿越攻击

    target = (base / Path(relative_path)).resolve()  # 解析用户输入的相对路径
    base_resolved = base.resolve()  # 预先解析基准目录
    if target == base_resolved:  # 允许访问目录本身
        return target
    try:
        target.relative_to(base_resolved)  # 检查目标是否位于基准目录内
    except ValueError as exc:  # pragma: no cover - 防穿越
        raise ValueError("路径越界") from exc  # 一旦越界立即拒绝
    return target


def _iter_out_files() -> Iterable[Path]:
    """遍历 out/ 目录下所有文件并返回相对路径。"""  # 供列表接口使用

    if not OUT_ROOT.exists():  # 目录不存在时返回空列表
        return []
    return [path.relative_to(OUT_ROOT) for path in OUT_ROOT.rglob("*") if path.is_file()]  # 仅保留文件


def _derive_stem(name: str) -> str:
    """从文件名推导 stem，用于在侧边栏聚合同一章节。"""

    return name.split(".")[0] if "." in name else name  # 兼容没有扩展名的情况


def _build_list_payload() -> dict[str, Any]:
    """整理 out/ 目录下的音频与标记文件，按 stem 聚合。"""

    groups: dict[str, dict[str, Any]] = {}  # 使用 stem 作为键存放音频与标记列表
    for relative in _iter_out_files():  # 遍历所有成果文件
        rel_str = relative.as_posix()  # 统一转为 POSIX 路径供前端展示
        name = relative.name  # 取出文件名
        lower = name.lower()  # 小写匹配扩展名
        stem = _derive_stem(name)  # 获取当前文件的 stem
        entry = groups.setdefault(stem, {"stem": stem, "audio": [], "markers": []})  # 初始化章节容器
        if any(lower.endswith(ext) for ext in AUDIO_EXTENSIONS):  # 将音频文件加入 audio 列表
            entry["audio"].append(f"out/{rel_str}")
        elif any(lower.endswith(ext) for ext in MARKER_EXTENSIONS):  # 其它成果归为 markers
            entry["markers"].append(f"out/{rel_str}")
    ordered: list[dict[str, Any]] = []
    for stem in sorted(groups.keys()):  # 按字母序排序，便于前端展示
        group = groups[stem]
        group["audio"] = sorted(group["audio"])  # 确保列表内部也按名称排序
        group["markers"] = sorted(group["markers"])
        ordered.append(group)
    return {"ok": True, "groups": ordered}  # 返回统一格式给前端


@app.route("/api/ping")
def api_ping() -> Any:
    """返回存活状态，供前端快速检测服务是否在线。"""

    return jsonify({"ok": True})


@app.route("/api/list")
def api_list() -> Any:
    """列出 out/ 目录内的所有音频与标记成果。"""

    try:
        payload = _build_list_payload()
    except Exception as exc:  # pragma: no cover - 容错
        return jsonify({"ok": False, "error": str(exc)}), 500
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/file")
def api_file() -> Any:
    """读取文本文件内容并返回给前端查看。"""

    rel = request.args.get("path")
    if not rel:
        return jsonify({"ok": False, "error": "缺少 path 参数"}), 400
    try:
        target = _safe_resolve(OUT_ROOT, rel)
    except ValueError:
        return jsonify({"ok": False, "error": "path out of scope"}), 403
    if not target.exists() or not target.is_file():
        return jsonify({"ok": False, "error": "file not found"}), 404
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "文件不是 UTF-8 文本"}), 415
    response = Response(content, mimetype="text/plain; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    print(f"[READ] {request.remote_addr or '-'} -> {target.relative_to(ROOT_DIR)}", flush=True)
    return response


def _validate_stem(stem: str) -> bool:
    """校验 stem 字符串是否安全，避免覆盖到意外文件。"""

    if not stem or "/" in stem or "\\" in stem:
        return False
    if ".." in stem:
        return False
    return True


@app.route("/api/save_edl", methods=["POST"])
def api_save_edl() -> Any:
    """保存手工标注的 EDL JSON。"""

    payload = request.get_json(silent=True) or {}
    stem = payload.get("stem", "")
    if not isinstance(stem, str) or not _validate_stem(stem):
        return jsonify({"ok": False, "error": "stem 非法"}), 400
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return jsonify({"ok": False, "error": "actions 必须为列表"}), 400
    filtered: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if end <= start:
            continue
        filtered.append({"type": "cut", "start": float(start), "end": float(end), "reason": item.get("reason", "manual")})
    out_path = OUT_ROOT / f"{stem}.manual.edl.json"  # 输出路径以 manual 标识人工编辑
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    out_path.write_text(json.dumps({"actions": filtered}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # 保留可读格式
    print(f"[SAVE] {request.remote_addr or '-'} -> {out_path.relative_to(ROOT_DIR)}", flush=True)
    response = jsonify({"ok": True, "path": out_path.relative_to(ROOT_DIR).as_posix()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/save_markers_csv", methods=["POST"])
def api_save_markers_csv() -> Any:
    """保存手工标注的 Audition CSV。"""

    payload = request.get_json(silent=True) or {}
    stem = payload.get("stem", "")
    if not isinstance(stem, str) or not _validate_stem(stem):
        return jsonify({"ok": False, "error": "stem 非法"}), 400
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "rows 必须为非空二维数组"}), 400
    normalized: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            return jsonify({"ok": False, "error": "rows 中存在非列表元素"}), 400
        normalized.append([str(cell) for cell in row])
    out_path = OUT_ROOT / f"{stem}.manual.audition_markers.csv"  # 输出文件统一添加 manual 前缀
    out_path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:  # 使用带 BOM 的 UTF-8 兼容 Excel
        writer = csv.writer(f, lineterminator="\r\n")  # 统一换行符，方便跨平台打开
        writer.writerows(normalized)  # 逐行写入标记数据
    print(f"[SAVE] {request.remote_addr or '-'} -> {out_path.relative_to(ROOT_DIR)}", flush=True)
    response = jsonify({"ok": True, "path": out_path.relative_to(ROOT_DIR).as_posix()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/web/")
@app.route("/web/<path:filename>")
def serve_web(filename: str | None = None) -> Any:
    """提供前端静态资源，如 index.html、app.js、style.css。"""

    target = "index.html" if not filename else filename
    try:
        _safe_resolve(WEB_ROOT, target)
    except ValueError:
        return jsonify({"ok": False, "error": "非法路径"}), 403
    return send_from_directory(WEB_ROOT, target)


@app.route("/out/<path:filename>")
def serve_out(filename: str) -> Any:
    """允许前端直接下载 out/ 中的成果文件。"""

    try:
        _safe_resolve(OUT_ROOT, filename)
    except ValueError:
        return jsonify({"ok": False, "error": "非法路径"}), 403
    return send_from_directory(OUT_ROOT, filename, as_attachment=False)


def _parse_args() -> argparse.Namespace:
    """解析命令行参数，允许用户自定义监听端口。"""

    parser = argparse.ArgumentParser(description=__doc__)  # 使用模块文档作为帮助信息
    parser.add_argument("--port", type=int, default=8088, help="监听端口")  # 默认为 8088
    return parser.parse_args()


def main() -> None:
    """启动 WSGI 服务器，并在端口被占用时自动递增尝试。"""

    args = _parse_args()  # 读取命令行参数
    port = args.port  # 起始端口
    server = None  # WSGI 服务器句柄
    while port <= 8090:  # 最多尝试三个端口
        try:
            server = make_server("127.0.0.1", port, app)  # 创建 WSGI 服务器实例
            break
        except OSError as exc:  # pragma: no cover - 端口被占用
            if getattr(exc, "errno", None) in {48, 98}:  # 常见的“地址已被使用”错误码
                print(f"Port {port} in use, trying {port + 1}", flush=True)
                port += 1
                continue
            raise  # 其它异常直接抛出
    if server is None:  # 全部端口均被占用
        raise SystemExit("无法启动服务: 端口 8088-8090 均被占用")
    print(f"Serving web panel on http://127.0.0.1:{port}", flush=True)  # 打印最终可用端口
    try:
        server.serve_forever()  # 阻塞运行直到收到中断
    except KeyboardInterrupt:  # pragma: no cover - 手动停止
        print("Shutting down web panel...", flush=True)
    finally:
        server.shutdown()  # 停止接受新连接
        server.server_close()  # 释放底层 socket


if __name__ == "__main__":  # 允许 python scripts/web_panel_server.py 直接启动服务
    main()
