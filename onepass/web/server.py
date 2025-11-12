"""FastAPI 服务：托管 OnePass Audio Web UI 与相关 API。"""
from __future__ import annotations

import json
import logging
import mimetypes
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import quote

import importlib.util

if importlib.util.find_spec("fastapi") is None:  # pragma: no cover - 依赖缺失时提示
    raise ModuleNotFoundError(
        "无法导入 fastapi。请先运行 `pip install -r requirements.txt` 安装依赖。"
    )

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

LOGGER = logging.getLogger("onepass.web.server")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATIC_DIR = PROJECT_ROOT / "webui"
DEFAULT_OUT_DIR = PROJECT_ROOT / "out"
DEFAULT_AUDIO_ROOT = PROJECT_ROOT / "materials"

AUDIO_EXTS = [".wav", ".m4a", ".mp3", ".flac"]
AUDIO_PRIORITY = {ext: idx for idx, ext in enumerate(AUDIO_EXTS)}
CSV_HEADER = "Name,Start,End,Duration,Comment\r\n"
EDL_TEMPLATE = json.dumps({"actions": []}, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class ServerConfig:
    """服务运行所需的配置。"""

    out_dir: Path = DEFAULT_OUT_DIR
    audio_root: Path = DEFAULT_AUDIO_ROOT
    static_dir: Path = DEFAULT_STATIC_DIR
    host: str = "127.0.0.1"
    requested_port: int = 8765
    max_retries: int = 10

    def resolve(self) -> "ServerConfig":
        return ServerConfig(
            out_dir=self.out_dir.expanduser().resolve(),
            audio_root=self.audio_root.expanduser().resolve(),
            static_dir=self.static_dir.expanduser().resolve(),
            host=self.host,
            requested_port=self.requested_port,
            max_retries=self.max_retries,
        )


@dataclass
class RunningServer:
    """记录运行中的服务器实例。"""

    config: ServerConfig
    server: "uvicorn.Server"
    thread: threading.Thread
    port: int
    reused: bool = False

    def is_alive(self) -> bool:
        return self.thread.is_alive() and not getattr(self.server, "should_exit", False)

    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.port}/"

    def open_in_browser(self, stem: Optional[str] = None, delay: float = 1.0) -> None:
        import webbrowser

        url = self.base_url()
        if stem:
            url = f"{url}?stem={quote(stem)}"
        threading.Thread(target=_open_browser_worker, args=(url, delay), daemon=True).start()

    def stop(self, timeout: float = 5.0) -> None:
        if hasattr(self.server, "should_exit"):
            self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)


_ACTIVE_SERVER: Optional[RunningServer] = None


def _open_browser_worker(url: str, delay: float) -> None:
    time.sleep(max(delay, 0.0))
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # pragma: no cover - 容错
        LOGGER.warning("自动打开浏览器失败", exc_info=True)


def _safe_stem(stem: str) -> str:
    if not stem:
        raise HTTPException(status_code=400, detail="stem 不能为空")
    if any(sep in stem for sep in ("/", "\\")):
        raise HTTPException(status_code=400, detail="stem 非法")
    if ".." in stem:
        raise HTTPException(status_code=400, detail="stem 非法")
    return stem


def _read_text(path: Path, *, default: str = "", encoding: str = "utf-8") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding=encoding)


def _read_csv(path: Path) -> str:
    if not path.exists():
        return CSV_HEADER
    return path.read_text(encoding="utf-8-sig")


def _write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def _write_csv(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig")


def _iter_materials_audio(audio_root: Path) -> Iterable[Path]:
    if not audio_root.exists():
        return []
    return (p for p in audio_root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_PRIORITY)


def _iter_out_files(out_dir: Path) -> Iterable[Path]:
    if not out_dir.exists():
        return []
    return (p for p in out_dir.rglob("*") if p.is_file())


def _select_best(existing: Optional[Path], candidate: Path) -> Path:
    if existing is None:
        return candidate
    existing_ext = existing.suffix.lower()
    candidate_ext = candidate.suffix.lower()
    if AUDIO_PRIORITY.get(candidate_ext, 999) < AUDIO_PRIORITY.get(existing_ext, 999):
        return candidate
    return existing


def _build_media_url(base: str, path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return f"/media/{base}/{relative}"


def _collect_stems(config: ServerConfig) -> Dict[str, Dict[str, Optional[str]]]:
    stems: Dict[str, Dict[str, Optional[str]]] = {}

    audio_root = config.audio_root
    for audio_path in _iter_materials_audio(audio_root):
        stem = audio_path.stem
        entry = stems.setdefault(
            stem,
            {
                "stem": stem,
                "source_audio": None,
                "clean_audio": None,
                "edl": None,
                "csv": None,
                "srt": None,
            },
        )
        best = entry["source_audio"]
        current = Path(best) if best else None
        if current is None or _select_best(current, audio_path) is audio_path:
            entry["source_audio"] = _build_media_url("materials", audio_path, audio_root)

    out_dir = config.out_dir
    for out_path in _iter_out_files(out_dir):
        name_lower = out_path.name.lower()
        if name_lower.endswith(".keeplast.edl.json"):
            stem = out_path.name[: -len(".keepLast.edl.json")]
            entry = stems.setdefault(
                stem,
                {
                    "stem": stem,
                    "source_audio": None,
                    "clean_audio": None,
                    "edl": None,
                    "csv": None,
                    "srt": None,
                },
            )
            entry["edl"] = _build_media_url("out", out_path, out_dir)
        elif name_lower.endswith(".audition_markers.csv"):
            stem = out_path.name[: -len(".audition_markers.csv")]
            entry = stems.setdefault(
                stem,
                {
                    "stem": stem,
                    "source_audio": None,
                    "clean_audio": None,
                    "edl": None,
                    "csv": None,
                    "srt": None,
                },
            )
            entry["csv"] = _build_media_url("out", out_path, out_dir)
        elif name_lower.endswith(".keeplast.srt"):
            stem = out_path.name[: -len(".keepLast.srt")]
            entry = stems.setdefault(
                stem,
                {
                    "stem": stem,
                    "source_audio": None,
                    "clean_audio": None,
                    "edl": None,
                    "csv": None,
                    "srt": None,
                },
            )
            entry["srt"] = _build_media_url("out", out_path, out_dir)
        else:
            for ext in AUDIO_EXTS:
                suffix = f".clean{ext}"
                if name_lower.endswith(suffix):
                    stem = out_path.name[: -len(suffix)]
                    entry = stems.setdefault(
                        stem,
                        {
                            "stem": stem,
                            "source_audio": None,
                            "clean_audio": None,
                            "edl": None,
                            "csv": None,
                            "srt": None,
                        },
                    )
                    current = entry["clean_audio"]
                    best_path = Path(current) if current else None
                    if best_path is None or _select_best(best_path, out_path) is out_path:
                        entry["clean_audio"] = _build_media_url("out", out_path, out_dir)
                    break

    return stems


def create_app(config: ServerConfig) -> FastAPI:
    cfg = config.resolve()
    app = FastAPI(title="OnePass Audio UI", docs_url=None, redoc_url=None)
    app.state.server_config = cfg

    static_app = StaticFiles(directory=str(cfg.static_dir))

    @app.get("/", response_class=FileResponse)
    async def get_index() -> FileResponse:
        index_path = cfg.static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="index.html 不存在")
        return FileResponse(index_path)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/list-stems")
    async def list_stems() -> JSONResponse:
        try:
            stems = _collect_stems(cfg)
        except Exception:
            LOGGER.exception("扫描 stems 失败")
            raise HTTPException(status_code=500, detail="扫描目录失败")
        ordered = [stems[key] for key in sorted(stems.keys())]
        for item in ordered:
            item.setdefault("source_audio", None)
            item.setdefault("clean_audio", None)
            item.setdefault("edl", None)
            item.setdefault("csv", None)
            item.setdefault("srt", None)
        return JSONResponse({"stems": ordered})

    @app.get("/api/stem/{stem}")
    async def get_stem(stem: str) -> JSONResponse:
        stem = _safe_stem(stem)
        edl_path = cfg.out_dir / f"{stem}.keepLast.edl.json"
        csv_path = cfg.out_dir / f"{stem}.audition_markers.csv"
        srt_path = cfg.out_dir / f"{stem}.keepLast.srt"
        try:
            edl_text = _read_text(edl_path, default=EDL_TEMPLATE)
            csv_text = _read_csv(csv_path)
            srt_text = _read_text(srt_path, default="")
        except Exception:
            LOGGER.exception("读取 stem=%s 的文件失败", stem)
            raise HTTPException(status_code=500, detail="读取文件失败")
        return JSONResponse(
            {
                "stem": stem,
                "edl_text": edl_text,
                "csv_text": csv_text,
                "srt_text": srt_text,
            }
        )

    @app.post("/api/stem/{stem}/save-edl")
    async def save_edl(stem: str, request: Request) -> JSONResponse:
        stem = _safe_stem(stem)
        payload = await request.body()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse({"ok": False, "error": "请求不是 UTF-8 编码"}, status_code=400)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return JSONResponse({"ok": False, "error": f"EDL JSON 无法解析: {exc}"}, status_code=400)
        normalized = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
        target = cfg.out_dir / f"{stem}.keepLast.edl.json"
        try:
            _write_text(target, normalized, encoding="utf-8")
        except Exception:
            LOGGER.exception("写入 EDL 失败: stem=%s", stem)
            return JSONResponse({"ok": False, "error": "写入 EDL 失败"}, status_code=500)
        LOGGER.info("保存 EDL: %s", target)
        return JSONResponse({"ok": True})

    @app.post("/api/stem/{stem}/save-csv")
    async def save_csv(stem: str, request: Request) -> JSONResponse:
        stem = _safe_stem(stem)
        payload = await request.body()
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return JSONResponse({"ok": False, "error": "请求不是 UTF-8 编码"}, status_code=400)
        if not text:
            text = CSV_HEADER
        target = cfg.out_dir / f"{stem}.audition_markers.csv"
        try:
            _write_csv(target, text)
        except Exception:
            LOGGER.exception("写入 CSV 失败: stem=%s", stem)
            return JSONResponse({"ok": False, "error": "写入 CSV 失败"}, status_code=500)
        LOGGER.info("保存 CSV: %s", target)
        return JSONResponse({"ok": True})

    @app.post("/api/upload")
    async def upload(
        stem: str = Form(...),
        type: str = Form(...),
        file: UploadFile = File(...),
    ) -> JSONResponse:
        stem = _safe_stem(stem)
        upload_type = type.strip()
        filename = Path(file.filename or "")
        if not filename.suffix:
            return JSONResponse({"ok": False, "error": "缺少文件扩展名"}, status_code=400)
        suffix = filename.suffix.lower()
        try:
            data = await file.read()
        finally:
            await file.close()
        if upload_type == "csv":
            target = cfg.out_dir / f"{stem}.audition_markers.csv"
            try:
                decoded = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                return JSONResponse({"ok": False, "error": "CSV 需要 UTF-8 编码"}, status_code=400)
            try:
                _write_csv(target, decoded)
            except Exception:
                LOGGER.exception("写入 CSV 失败: stem=%s", stem)
                return JSONResponse({"ok": False, "error": "写入 CSV 失败"}, status_code=500)
            url = _build_media_url("out", target, cfg.out_dir)
        elif upload_type == "audio_source":
            if suffix not in AUDIO_PRIORITY:
                return JSONResponse({"ok": False, "error": "不支持的音频扩展名"}, status_code=400)
            target = cfg.audio_root / f"{stem}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(data)
            except Exception:
                LOGGER.exception("写入源音频失败: stem=%s", stem)
                return JSONResponse({"ok": False, "error": "写入音频失败"}, status_code=500)
            url = _build_media_url("materials", target, cfg.audio_root)
        elif upload_type == "audio_clean":
            if suffix not in AUDIO_PRIORITY:
                return JSONResponse({"ok": False, "error": "不支持的音频扩展名"}, status_code=400)
            target = cfg.out_dir / f"{stem}.clean{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(data)
            except Exception:
                LOGGER.exception("写入干净音频失败: stem=%s", stem)
                return JSONResponse({"ok": False, "error": "写入音频失败"}, status_code=500)
            url = _build_media_url("out", target, cfg.out_dir)
        else:
            return JSONResponse({"ok": False, "error": "未知的上传类型"}, status_code=400)
        LOGGER.info("上传完成: type=%s stem=%s -> %s", upload_type, stem, url)
        return JSONResponse({"ok": True, "url": url})

    @app.get("/media/{category}/{resource_path:path}")
    async def media(category: str, resource_path: str, request: Request) -> StreamingResponse:
        if category not in {"materials", "out"}:
            raise HTTPException(status_code=404, detail="未知的资源类型")
        root = cfg.audio_root if category == "materials" else cfg.out_dir
        target = (root / resource_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="path out of scope") from exc
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        file_size = target.stat().st_size
        range_header = request.headers.get("range")
        content_type, _ = mimetypes.guess_type(target.name)
        content_type = content_type or "application/octet-stream"
        if range_header:
            start, end = _parse_range(range_header, file_size)
            return _range_response(target, start, end, file_size, content_type)
        response = FileResponse(target, media_type=content_type)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    @app.get("/{static_path:path}", include_in_schema=False)
    async def static_fallback(static_path: str, request: Request):
        if static_path.startswith("api/") or static_path.startswith("media/"):
            raise HTTPException(status_code=404, detail="未找到资源")
        response = await static_app.get_response(static_path, request.scope)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="未找到资源")
        return response

    return app


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="无效的 Range 头")
    range_spec = range_header[len("bytes=") :]
    if "," in range_spec:
        raise HTTPException(status_code=416, detail="不支持的 Range 格式")
    start_str, _, end_str = range_spec.partition("-")
    try:
        if start_str:
            start = int(start_str)
            if end_str:
                end = int(end_str)
            else:
                end = file_size - 1
        else:
            if not end_str:
                raise ValueError
            length = int(end_str)
            if length <= 0:
                raise ValueError
            start = max(file_size - length, 0)
            end = file_size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="无效的 Range 取值")
    if start >= file_size:
        raise HTTPException(status_code=416, detail="Range 起始超出文件长度")
    end = min(end, file_size - 1)
    if end < start:
        raise HTTPException(status_code=416, detail="Range 结束小于起始")
    return start, end


def _range_response(path: Path, start: int, end: int, file_size: int, content_type: str) -> StreamingResponse:
    chunk_size = 64 * 1024

    def iterator() -> Iterable[bytes]:
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                chunk = handle.read(read_size)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    response = StreamingResponse(iterator(), media_type=content_type, status_code=206)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response.headers["Content-Length"] = str(end - start + 1)
    return response


def _find_available_port(host: str, requested_port: int, max_retries: int) -> int:
    for offset in range(max_retries):
        port = requested_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                LOGGER.debug("端口 %s 已占用，尝试下一个", port)
                continue
            return port
    raise RuntimeError("无法找到可用端口")


def spawn_server(
    out_dir: Path,
    audio_root: Optional[Path] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_retries: int = 10,
    log_level: str = "info",
) -> RunningServer:
    import uvicorn

    global _ACTIVE_SERVER

    out_dir_path = Path(out_dir).expanduser()
    audio_root_path = Path(audio_root).expanduser() if audio_root is not None else DEFAULT_AUDIO_ROOT
    resolved_config = ServerConfig(
        out_dir=out_dir_path,
        audio_root=audio_root_path,
        static_dir=DEFAULT_STATIC_DIR,
        host=host,
        requested_port=port,
        max_retries=max_retries,
    ).resolve()
    actual_port = _find_available_port(host, resolved_config.requested_port, resolved_config.max_retries)
    if actual_port != resolved_config.requested_port:
        LOGGER.warning(
            "端口 %s 已占用，改用 %s", resolved_config.requested_port, actual_port
        )
    app = create_app(resolved_config)
    config = uvicorn.Config(
        app,
        host=resolved_config.host,
        port=actual_port,
        log_level=log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _runner() -> None:
        LOGGER.info("Web UI 服务启动: http://%s:%s/", resolved_config.host, actual_port)
        try:
            server.run()
        finally:
            LOGGER.info("Web UI 服务已停止")

    thread = threading.Thread(target=_runner, name="onepass-webui", daemon=True)
    thread.start()
    if not server.started.wait(timeout=10):
        raise RuntimeError("Web UI 服务启动超时")
    running = RunningServer(config=resolved_config, server=server, thread=thread, port=actual_port)
    running.reused = False
    _ACTIVE_SERVER = running
    return running


def run_server(
    out_dir: Path,
    audio_root: Optional[Path] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_retries: int = 10,
    log_level: str = "info",
    open_browser: bool = False,
    stem: Optional[str] = None,
) -> None:
    global _ACTIVE_SERVER

    running = spawn_server(
        out_dir,
        audio_root,
        host=host,
        port=port,
        max_retries=max_retries,
        log_level=log_level,
    )
    running.reused = False
    _ACTIVE_SERVER = running
    try:
        if open_browser:
            running.open_in_browser(stem=stem)
        wait_for_server(running)
    finally:
        running.stop()
        if _ACTIVE_SERVER is running:
            _ACTIVE_SERVER = None


def wait_for_server(running: RunningServer) -> None:
    global _ACTIVE_SERVER
    try:
        while running.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        LOGGER.info("收到中断信号，准备退出 Web UI 服务…")
        running.stop()
    finally:
        if not running.thread.is_alive() and _ACTIVE_SERVER is running:
            _ACTIVE_SERVER = None


def ensure_server_running(
    out_dir: Path,
    audio_root: Optional[Path] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    max_retries: int = 10,
    log_level: str = "info",
    open_browser: bool = False,
    stem: Optional[str] = None,
) -> RunningServer:
    global _ACTIVE_SERVER

    desired_out = Path(out_dir).expanduser().resolve()
    desired_root = (
        Path(audio_root).expanduser().resolve() if audio_root is not None else DEFAULT_AUDIO_ROOT.expanduser().resolve()
    )

    if _ACTIVE_SERVER and _ACTIVE_SERVER.is_alive():
        cfg = _ACTIVE_SERVER.config
        if (
            cfg.out_dir == desired_out
            and cfg.audio_root == desired_root
            and cfg.host == host
            and cfg.requested_port == port
        ):
            LOGGER.info("复用已启动的 Web UI 服务: http://%s:%s/", cfg.host, _ACTIVE_SERVER.port)
            _ACTIVE_SERVER.reused = True
            if open_browser:
                _ACTIVE_SERVER.open_in_browser(stem=stem)
            return _ACTIVE_SERVER
        LOGGER.info("停止现有 Web UI 服务以应用新配置")
        _ACTIVE_SERVER.stop()
        _ACTIVE_SERVER = None

    running = spawn_server(
        desired_out,
        audio_root=desired_root,
        host=host,
        port=port,
        max_retries=max_retries,
        log_level=log_level,
    )
    running.reused = False
    _ACTIVE_SERVER = running
    if open_browser:
        running.open_in_browser(stem=stem)
    return running
