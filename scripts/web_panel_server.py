"""提供可视化标注面板所需的本地 Flask 服务。"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.serving import make_server

ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT_DIR / "web"
OUT_ROOT = ROOT_DIR / "out"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
MARKER_EXTENSIONS = {".audition_markers.csv", ".markers.csv", ".edl.json", ".srt"}

app = Flask(__name__)
app.json.ensure_ascii = False
CORS(app)


def _safe_resolve(base: Path, relative_path: str) -> Path:
    """确保 ``relative_path`` 位于 ``base`` 子目录下。"""

    target = (base / Path(relative_path)).resolve()
    base_resolved = base.resolve()
    if target == base_resolved:
        return target
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:  # pragma: no cover - 防穿越
        raise ValueError("路径越界") from exc
    return target


def _iter_out_files() -> Iterable[Path]:
    if not OUT_ROOT.exists():
        return []
    return [path.relative_to(OUT_ROOT) for path in OUT_ROOT.rglob("*") if path.is_file()]


def _derive_stem(name: str) -> str:
    return name.split(".")[0] if "." in name else name


def _build_list_payload() -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for relative in _iter_out_files():
        rel_str = relative.as_posix()
        name = relative.name
        lower = name.lower()
        stem = _derive_stem(name)
        entry = groups.setdefault(stem, {"stem": stem, "audio": [], "markers": []})
        if any(lower.endswith(ext) for ext in AUDIO_EXTENSIONS):
            entry["audio"].append(f"out/{rel_str}")
        elif any(lower.endswith(ext) for ext in MARKER_EXTENSIONS):
            entry["markers"].append(f"out/{rel_str}")
    ordered: list[dict[str, Any]] = []
    for stem in sorted(groups.keys()):
        group = groups[stem]
        group["audio"] = sorted(group["audio"])
        group["markers"] = sorted(group["markers"])
        ordered.append(group)
    return {"ok": True, "groups": ordered}


@app.route("/api/ping")
def api_ping() -> Any:
    return jsonify({"ok": True})


@app.route("/api/list")
def api_list() -> Any:
    try:
        payload = _build_list_payload()
    except Exception as exc:  # pragma: no cover - 容错
        return jsonify({"ok": False, "error": str(exc)}), 500
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/file")
def api_file() -> Any:
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
    if not stem or "/" in stem or "\\" in stem:
        return False
    if ".." in stem:
        return False
    return True


@app.route("/api/save_edl", methods=["POST"])
def api_save_edl() -> Any:
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
    out_path = OUT_ROOT / f"{stem}.manual.edl.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"actions": filtered}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[SAVE] {request.remote_addr or '-'} -> {out_path.relative_to(ROOT_DIR)}", flush=True)
    response = jsonify({"ok": True, "path": out_path.relative_to(ROOT_DIR).as_posix()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/save_markers_csv", methods=["POST"])
def api_save_markers_csv() -> Any:
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
    out_path = OUT_ROOT / f"{stem}.manual.audition_markers.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n")
        writer.writerows(normalized)
    print(f"[SAVE] {request.remote_addr or '-'} -> {out_path.relative_to(ROOT_DIR)}", flush=True)
    response = jsonify({"ok": True, "path": out_path.relative_to(ROOT_DIR).as_posix()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/web/")
@app.route("/web/<path:filename>")
def serve_web(filename: str | None = None) -> Any:
    target = "index.html" if not filename else filename
    try:
        _safe_resolve(WEB_ROOT, target)
    except ValueError:
        return jsonify({"ok": False, "error": "非法路径"}), 403
    return send_from_directory(WEB_ROOT, target)


@app.route("/out/<path:filename>")
def serve_out(filename: str) -> Any:
    try:
        _safe_resolve(OUT_ROOT, filename)
    except ValueError:
        return jsonify({"ok": False, "error": "非法路径"}), 403
    return send_from_directory(OUT_ROOT, filename, as_attachment=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8088, help="监听端口")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    port = args.port
    server = None
    while port <= 8090:
        try:
            server = make_server("127.0.0.1", port, app)
            break
        except OSError as exc:  # pragma: no cover - 端口被占用
            if getattr(exc, "errno", None) in {48, 98}:
                print(f"Port {port} in use, trying {port + 1}", flush=True)
                port += 1
                continue
            raise
    if server is None:
        raise SystemExit("无法启动服务: 端口 8088-8090 均被占用")
    print(f"Serving web panel on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - 手动停止
        print("Shutting down web panel...", flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
