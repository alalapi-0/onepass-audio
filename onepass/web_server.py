"""FastAPI 微服务，为本地可视化控制台提供数据接口。"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .edl_renderer import (
    build_filter_pipeline,
    load_edl,
    normalize_segments,
    probe_duration,
    resolve_source_audio,
)

LOGGER = logging.getLogger("onepass.web")


mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("text/csv; charset=utf-8", ".csv")


AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg", ".wma"}
STEM_SUFFIXES = [
    ".keepLast.edl.json",
    ".sentence.edl.json",
    ".edl.json",
    ".audition_markers.csv",
    ".markers.csv",
    ".keepLast.srt",
    ".sentence.srt",
    ".srt",
    ".keepLast.align.txt",
    ".align.txt",
    ".keepLast.txt",
    ".sentence.txt",
    ".txt",
    ".clean.wav",
    ".clean.m4a",
    ".clean.mp3",
    ".clean.flac",
    ".clean.aac",
    ".clean.ogg",
    ".clean.wma",
    ".wav",
    ".m4a",
    ".mp3",
    ".flac",
    ".aac",
    ".ogg",
    ".wma",
    ".json",
]

EDL_PRIORITY = [
    "{stem}.keepLast.edl.json",
    "{stem}.sentence.edl.json",
    "{stem}.edl.json",
]

CSV_PRIORITY = [
    "{stem}.audition_markers.csv",
    "{stem}.markers.csv",
    "{stem}.csv",
]

SRT_PRIORITY = [
    "{stem}.srt",
    "{stem}.sentence.srt",
]

TXT_PRIORITY = [
    "{stem}.keepLast.txt",
    "{stem}.sentence.txt",
    "{stem}.txt",
]

ALIGN_PRIORITY = [
    "{stem}.align.txt",
    "{stem}.sentence.align.txt",
]

AUDIO_CACHE_TTL = 5.0  # 秒，音频根目录扫描缓存刷新周期


def _json_response(payload: Any, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(content=payload, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def _normalise_stem(name: str) -> str:
    lowered = name.lower()
    for suffix in STEM_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return name[: -len(suffix)]
    if "." in name:
        return name.split(".")[0]
    return name


def _safe_stem(value: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail="stem 不能为空")
    if any(char in value for char in "\\/\0"):
        raise HTTPException(status_code=400, detail="stem 包含非法字符")
    if ".." in value:
        raise HTTPException(status_code=400, detail="stem 不允许包含 ..")
    return value


def _sorted_unique(items: Iterable[Path]) -> list[Path]:
    seen: dict[str, Path] = {}
    for item in items:
        key = str(item).lower()
        if key not in seen:
            seen[key] = item
    return sorted(seen.values())


@dataclass(slots=True)
class PathTokenStore:
    """维护绝对路径与短 token 的映射。"""

    _token_to_path: dict[str, Path] = field(default_factory=dict)
    _path_to_token: dict[Path, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, path: Path) -> str:
        try:
            absolute = path.expanduser().resolve(strict=False)
        except OSError:
            absolute = path.expanduser()
        with self._lock:
            if absolute in self._path_to_token:
                return self._path_to_token[absolute]
            digest = hashlib.sha1(str(absolute).encode("utf-8", "surrogatepass")).hexdigest()
            self._path_to_token[absolute] = digest
            self._token_to_path[digest] = absolute
            return digest

    def resolve(self, token: str) -> Path:
        with self._lock:
            if token not in self._token_to_path:
                raise KeyError(token)
            return self._token_to_path[token]


@dataclass(slots=True)
class StemBundle:
    stem: str
    edl: list[Path] = field(default_factory=list)
    csv: list[Path] = field(default_factory=list)
    srt: list[Path] = field(default_factory=list)
    txt: list[Path] = field(default_factory=list)
    align: list[Path] = field(default_factory=list)
    audio_outputs: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class WebContext:
    out_dir: Path
    audio_root: Path | None
    web_dir: Path
    token_store: PathTokenStore
    _audio_cache: dict[str, Any] = field(default_factory=dict)
    _audio_cache_lock: threading.Lock = field(default_factory=threading.Lock)

    def posix_from_out(self, path: Path) -> str:
        relative = path.relative_to(self.out_dir)
        return relative.as_posix()

    def ensure_inside_out(self, path: Path) -> Path:
        target = path.expanduser().resolve(strict=False)
        try:
            target.relative_to(self.out_dir)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="path out of scope") from exc
        return target

    def get_audio_map(self, *, force: bool = False) -> dict[str, list[Path]]:
        if not self.audio_root or not self.audio_root.exists():
            return {}
        now = time.monotonic()
        with self._audio_cache_lock:
            if not force:
                cached = self._audio_cache.get("value")
                timestamp = self._audio_cache.get("timestamp", 0.0)
                if cached is not None and now - timestamp < AUDIO_CACHE_TTL:
                    return cached
            mapping = _collect_audio_root_map(self.audio_root)
            self._audio_cache["value"] = mapping
            self._audio_cache["timestamp"] = now
            return mapping

    def invalidate_audio_cache(self) -> None:
        with self._audio_cache_lock:
            self._audio_cache.clear()


def _gather_out_files(context: WebContext) -> dict[str, StemBundle]:
    bundles: dict[str, StemBundle] = {}
    if not context.out_dir.exists():
        return bundles
    for path in context.out_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        stem = _normalise_stem(name)
        key = stem.lower()
        bundle = bundles.setdefault(key, StemBundle(stem=stem))
        lower = name.lower()
        if lower.endswith(".edl.json"):
            bundle.edl.append(path)
        elif lower.endswith(".csv"):
            bundle.csv.append(path)
        elif lower.endswith(".srt"):
            bundle.srt.append(path)
        elif lower.endswith(".align.txt"):
            bundle.align.append(path)
        elif lower.endswith(".txt"):
            bundle.txt.append(path)
        elif path.suffix.lower() in AUDIO_SUFFIXES:
            bundle.audio_outputs.append(path)
    for bundle in bundles.values():
        bundle.edl = _sorted_unique(bundle.edl)
        bundle.csv = _sorted_unique(bundle.csv)
        bundle.srt = _sorted_unique(bundle.srt)
        bundle.txt = _sorted_unique(bundle.txt)
        bundle.align = _sorted_unique(bundle.align)
        bundle.audio_outputs = _sorted_unique(bundle.audio_outputs)
    return bundles


def _collect_audio_root_map(audio_root: Path | None) -> dict[str, list[Path]]:
    if not audio_root or not audio_root.exists():
        return {}
    results: dict[str, list[Path]] = {}
    for path in audio_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        stem = _normalise_stem(path.name)
        results.setdefault(stem.lower(), []).append(path)
    for key in list(results.keys()):
        results[key] = _sorted_unique(results[key])
    return results


def _pick_best_file(paths: list[Path], priority: list[str], stem: str) -> Path | None:
    if not paths:
        return None
    lookup = {path.name.lower(): path for path in paths}
    for pattern in priority:
        name = pattern.format(stem=stem).lower()
        if name in lookup:
            return lookup[name]
    return paths[0]


def _load_csv_regions(csv_path: Path) -> list[dict[str, Any]]:
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = csv_path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    data_rows = rows[1:]

    def _index(*candidates: str) -> int | None:
        for candidate in candidates:
            if candidate in header:
                return header.index(candidate)
        return None

    idx_name = _index("name", "marker name", "title")
    idx_start = _index("start", "start time", "in")
    idx_duration = _index("duration")
    idx_end = _index("end", "out")
    idx_description = _index("description", "comment", "notes")
    idx_type = _index("type", "state")

    regions: list[dict[str, Any]] = []
    for row_index, row in enumerate(data_rows, start=1):
        if not row or all(cell.strip() == "" for cell in row):
            continue
        try:
            start_val = float(row[idx_start]) if idx_start is not None and row[idx_start] else 0.0
        except (TypeError, ValueError, IndexError):
            start_val = 0.0
        duration_val = None
        if idx_duration is not None and idx_duration < len(row):
            try:
                duration_val = float(row[idx_duration]) if row[idx_duration] else None
            except (TypeError, ValueError):
                duration_val = None
        end_val = None
        if idx_end is not None and idx_end < len(row):
            try:
                end_val = float(row[idx_end]) if row[idx_end] else None
            except (TypeError, ValueError):
                end_val = None
        if duration_val is None and end_val is not None:
            duration_val = max(0.0, end_val - start_val)
        if duration_val is None:
            duration_val = 0.0
        name_val = row[idx_name] if idx_name is not None and idx_name < len(row) else ""
        desc_val = row[idx_description] if idx_description is not None and idx_description < len(row) else ""
        type_val = row[idx_type] if idx_type is not None and idx_type < len(row) else ""
        state = str(type_val or "keep").strip().lower()
        if state not in {"keep", "delete", "undecided"}:
            state = "keep"
        regions.append(
            {
                "id": f"row{row_index}",
                "start": float(start_val),
                "end": float(start_val + duration_val),
                "label": name_val,
                "description": desc_val,
                "state": state,
            }
        )
    return regions


def _load_srt(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    blocks = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            if current:
                blocks.append(current)
            current = {"index": int(stripped), "text": ""}
            continue
        if "-->" in stripped:
            if current is None:
                current = {"index": len(blocks) + 1, "text": ""}
            parts = [part.strip() for part in stripped.split("-->")]
            if len(parts) == 2:
                current["start"] = parts[0]
                current["end"] = parts[1]
            continue
        if current is None:
            continue
        if current["text"]:
            current["text"] += "\n"
        current["text"] += stripped
    if current:
        blocks.append(current)
    return blocks


def _ensure_regions(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        state = str(item.get("state", "keep")).strip().lower()
        if state not in {"keep", "delete", "undecided"}:
            state = "keep"
        regions.append(
            {
                "start": start,
                "end": end,
                "state": state,
                "label": item.get("label", ""),
                "description": item.get("description", ""),
            }
        )
    return sorted(regions, key=lambda item: item["start"])


def _export_edl(context: WebContext, stem: str, regions: list[dict[str, Any]]) -> Path:
    target = context.out_dir / f"{stem}.keepLast.edl.json"
    base: dict[str, Any] = {
        "stem": stem,
        "version": 2,
        "segments": [],
        "path_style": "posix",
    }
    if target.exists():
        try:
            base.update(json.loads(target.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            LOGGER.warning("现有 EDL JSON 解析失败，将覆盖写入: %s", target)
    base["stem"] = stem
    base.setdefault("path_style", "posix")
    keeps = [
        {
            "start": region["start"],
            "end": region["end"],
            "action": "keep",
        }
        for region in regions
        if region["state"] != "delete"
    ]
    base["segments"] = keeps
    base["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _export_csv(context: WebContext, stem: str, regions: list[dict[str, Any]], dialect: str) -> Path:
    target = context.out_dir / f"{stem}.audition_markers.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    regions = sorted(regions, key=lambda item: item["start"])
    if dialect == "simple":
        header = ["start", "end", "state", "label", "description"]
        rows = [header]
        for region in regions:
            rows.append(
                [
                    f"{region['start']:.3f}",
                    f"{region['end']:.3f}",
                    region["state"],
                    region.get("label", ""),
                    region.get("description", ""),
                ]
            )
    else:
        header = ["Name", "Start", "Duration", "Type", "Description", "Comment"]
        rows = [header]
        for index, region in enumerate(regions, start=1):
            start = region["start"]
            end = region["end"]
            duration = max(0.0, end - start)
            name = str(region.get("label") or f"Region {index}")
            description = str(region.get("description") or "")
            rows.append(
                [
                    name,
                    f"{start:.3f}",
                    f"{duration:.3f}",
                    region["state"],
                    description,
                    region["state"],
                ]
            )
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerows(rows)
    return target


def create_app(
    out_dir: Path,
    audio_root: Path | None = None,
    *,
    enable_cors: bool = False,
) -> FastAPI:
    out_path = Path(out_dir).expanduser().resolve()
    audio_path = Path(audio_root).expanduser().resolve() if audio_root else None
    web_dir = Path(__file__).resolve().parents[1] / "web"

    token_store = PathTokenStore()
    context = WebContext(out_dir=out_path, audio_root=audio_path, web_dir=web_dir, token_store=token_store)

    app = FastAPI(title="OnePass Audio 控制台")
    app.state.context = context

    if enable_cors:
        origins = ["http://localhost", "http://localhost:5173", "http://127.0.0.1", "http://127.0.0.1:5173"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _refresh_bundles() -> dict[str, StemBundle]:
        return _gather_out_files(context)

    @app.get("/api/list")
    def api_list(stem: str | None = Query(default=None)) -> JSONResponse:
        bundles = _refresh_bundles()
        audio_map = context.get_audio_map()
        entries: list[dict[str, Any]] = []
        target = stem.lower() if stem else None
        keys = set(bundles.keys()) | set(audio_map.keys())
        for key in sorted(keys):
            bundle = bundles.get(key)
            if target and key != target:
                continue
            audio_candidates = audio_map.get(key, [])
            if bundle is None:
                if audio_candidates:
                    stem_name = _normalise_stem(audio_candidates[0].name)
                else:
                    stem_name = key
                bundle = StemBundle(stem=stem_name)
            else:
                stem_name = bundle.stem
            entry = {
                "stem": stem_name,
                "files": {
                    "audio": [],
                    "edl": [],
                    "csv": [],
                    "srt": [],
                    "txt": [],
                    "align": [],
                },
            }
            for path in bundle.audio_outputs:
                token = context.token_store.register(path)
                relative = context.posix_from_out(path)
                entry["files"]["audio"].append(
                    {
                        "name": path.name,
                        "token": token,
                        "path": relative,
                        "kind": "rendered" if "clean/" in relative else "out",
                    }
                )
            for path in bundle.edl:
                entry["files"]["edl"].append(
                    {
                        "name": path.name,
                        "path": context.posix_from_out(path),
                    }
                )
            for path in bundle.csv:
                entry["files"]["csv"].append(
                    {
                        "name": path.name,
                        "path": context.posix_from_out(path),
                    }
                )
            for path in bundle.srt:
                entry["files"]["srt"].append(
                    {
                        "name": path.name,
                        "path": context.posix_from_out(path),
                    }
                )
            for path in bundle.txt:
                entry["files"]["txt"].append(
                    {
                        "name": path.name,
                        "path": context.posix_from_out(path),
                    }
                )
            for path in bundle.align:
                entry["files"]["align"].append(
                    {
                        "name": path.name,
                        "path": context.posix_from_out(path),
                    }
                )
            for source in audio_candidates:
                token = context.token_store.register(source)
                entry["files"]["audio"].append(
                    {
                        "name": source.name,
                        "token": token,
                        "path": source.as_posix(),
                        "kind": "source",
                    }
                )
            entry["files"]["audio"] = sorted(entry["files"]["audio"], key=lambda item: item["name"].lower())
            entries.append(entry)
        return _json_response({"stems": entries})

    def _ensure_bundle(stem_value: str) -> tuple[str, StemBundle]:
        bundles = _refresh_bundles()
        key = stem_value.lower()
        bundle = bundles.get(key)
        if bundle:
            return key, bundle
        raise HTTPException(status_code=404, detail="未找到指定 stem 的成果")

    @app.get("/api/edl/{stem}")
    def api_get_edl(stem: str) -> JSONResponse:
        _safe_stem(stem)
        _, bundle = _ensure_bundle(stem)
        path = _pick_best_file(bundle.edl, EDL_PRIORITY, bundle.stem)
        if not path:
            raise HTTPException(status_code=404, detail="未找到对应的 EDL 文件")
        data = json.loads(path.read_text(encoding="utf-8"))
        return _json_response(data)

    @app.get("/api/csv/{stem}")
    def api_get_csv(stem: str) -> JSONResponse:
        _safe_stem(stem)
        _, bundle = _ensure_bundle(stem)
        path = _pick_best_file(bundle.csv, CSV_PRIORITY, bundle.stem)
        if not path:
            raise HTTPException(status_code=404, detail="未找到 CSV 文件")
        regions = _load_csv_regions(path)
        return _json_response({"regions": regions, "path": context.posix_from_out(path)})

    @app.get("/api/srt/{stem}")
    def api_get_srt(stem: str) -> JSONResponse:
        _safe_stem(stem)
        _, bundle = _ensure_bundle(stem)
        path = _pick_best_file(bundle.srt, SRT_PRIORITY, bundle.stem)
        if not path:
            raise HTTPException(status_code=404, detail="未找到 SRT 文件")
        blocks = _load_srt(path)
        return _json_response({"items": blocks, "path": context.posix_from_out(path)})

    @app.get("/api/audio/{token}")
    def api_get_audio(token: str) -> FileResponse:
        try:
            path = context.token_store.resolve(token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="token 无效") from exc
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="音频文件不存在或不可访问")
        media_type, _ = mimetypes.guess_type(path.name)
        response = FileResponse(path, media_type=media_type or "application/octet-stream")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/export/edl")
    def api_export_edl(payload: dict[str, Any]) -> JSONResponse:
        stem = _safe_stem(str(payload.get("stem", "")))
        regions = _ensure_regions(payload.get("regions") or [])
        target = _export_edl(context, stem, regions)
        LOGGER.info("[export-edl] stem=%s path=%s", stem, target)
        return _json_response({"ok": True, "path": context.posix_from_out(target)})

    @app.post("/api/export/csv")
    def api_export_csv(payload: dict[str, Any]) -> JSONResponse:
        stem = _safe_stem(str(payload.get("stem", "")))
        regions = _ensure_regions(payload.get("regions") or [])
        dialect = str(payload.get("dialect") or "audition").lower()
        if dialect not in {"audition", "simple"}:
            raise HTTPException(status_code=400, detail="dialect 仅支持 audition/simple")
        target = _export_csv(context, stem, regions, dialect)
        LOGGER.info("[export-csv] stem=%s path=%s", stem, target)
        return _json_response({"ok": True, "path": context.posix_from_out(target)})

    @app.post("/api/render")
    def api_render(payload: dict[str, Any]) -> JSONResponse:
        stem = _safe_stem(str(payload.get("stem", "")))
        force = bool(payload.get("force"))
        fmt = str(payload.get("format", "wav")).lower()
        if fmt not in {"wav", "m4a"}:
            raise HTTPException(status_code=400, detail="format 仅支持 wav/m4a")
        _, bundle = _ensure_bundle(stem)
        edl_path = _pick_best_file(bundle.edl, EDL_PRIORITY, bundle.stem)
        if not edl_path:
            raise HTTPException(status_code=404, detail="未找到可用的 EDL 文件")
        edl_doc = load_edl(edl_path)
        audio_root = context.audio_root or context.out_dir
        source = resolve_source_audio(edl_doc, edl_path, audio_root, strict=False)
        if not source or not source.exists():
            raise HTTPException(status_code=404, detail="无法定位源音频，请确认 audio-root 设置")
        duration = probe_duration(source)
        keeps = normalize_segments(edl_doc.segments, duration)
        if not keeps:
            raise HTTPException(status_code=400, detail="EDL 无有效保留片段")
        clean_dir = context.out_dir / "clean"
        clean_dir.mkdir(parents=True, exist_ok=True)
        ext = ".clean.wav" if fmt == "wav" else ".clean.m4a"
        output = clean_dir / f"{stem}{ext}"
        if output.exists() and not force:
            token = context.token_store.register(output)
            total_keep = sum(segment.end - segment.start for segment in keeps)
            return _json_response(
                {
                    "ok": True,
                    "path_token": token,
                    "path": context.posix_from_out(output),
                    "duration_ms": int(total_keep * 1000),
                    "segments": len(keeps),
                    "skipped": True,
                }
            )

        filter_complex, label = build_filter_pipeline(keeps, edl_doc.samplerate, edl_doc.channels)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            filter_complex,
            "-map",
            label,
        ]
        if fmt == "wav":
            cmd.extend(["-c:a", "pcm_s16le"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])
        cmd.append(str(output))

        LOGGER.info("[render] stem=%s cmd=%s", stem, " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail="未找到 ffmpeg，请安装后重试") from exc
        stderr_bytes = proc.stderr or b""
        stderr_text = stderr_bytes.decode("utf-8", "ignore")
        if proc.returncode != 0:
            LOGGER.error("[render] ffmpeg failed rc=%s stderr=%s", proc.returncode, stderr_text.strip())
            raise HTTPException(status_code=500, detail=f"ffmpeg 渲染失败，退出码 {proc.returncode}")
        if not output.exists():
            raise HTTPException(status_code=500, detail="ffmpeg 未生成输出文件")
        token = context.token_store.register(output)
        total_keep = sum(segment.end - segment.start for segment in keeps)
        return _json_response(
            {
                "ok": True,
                "path_token": token,
                "path": context.posix_from_out(output),
                "duration_ms": int(total_keep * 1000),
                "segments": len(keeps),
                "skipped": False,
            }
        )

    @app.get("/api/debug/{stem}")
    def api_debug(stem: str) -> JSONResponse:
        _safe_stem(stem)
        debug_path = context.out_dir / "debug" / f"{stem}.alignment_profile.json"
        if not debug_path.exists():
            raise HTTPException(status_code=404, detail="未找到调试 JSON")
        data = json.loads(debug_path.read_text(encoding="utf-8"))
        return _json_response(data)

    static_app = StaticFiles(directory=context.web_dir, html=True)
    app.mount("/", static_app, name="static")

    return app


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    time.sleep(delay)
    try:
        webbrowser.open_new_tab(url)
    except Exception:  # pragma: no cover - 容错
        LOGGER.warning("自动打开浏览器失败", exc_info=True)


def run_web_server(
    out_dir: Path,
    audio_root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 5173,
    enable_cors: bool = False,
    open_browser: bool = False,
    log_level: str = "info",
) -> None:
    import uvicorn

    app = create_app(out_dir, audio_root, enable_cors=enable_cors)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level, reload=False)
    server = uvicorn.Server(config)
    url = f"http://{host}:{port}/"
    LOGGER.info("Web 控制台启动中: %s", url)
    if open_browser:
        threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()
    server.run()


def spawn_web_server(
    out_dir: Path,
    audio_root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 5173,
    enable_cors: bool = False,
    open_browser: bool = False,
    log_level: str = "info",
) -> tuple[Any, threading.Thread]:
    import uvicorn

    app = create_app(out_dir, audio_root, enable_cors=enable_cors)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level, reload=False)
    server = uvicorn.Server(config)

    def _runner() -> None:
        server.run()

    thread = threading.Thread(target=_runner, name="onepass-web-server", daemon=True)
    thread.start()
    url = f"http://{host}:{port}/"
    LOGGER.info("Web 控制台已启动: %s", url)
    if open_browser:
        threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()
    return server, thread


def wait_for_server(server: Any, thread: threading.Thread) -> None:
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        LOGGER.info("收到中断信号，正在关闭 web 服务…")
        try:
            server.should_exit = True
        except Exception:
            pass
        thread.join(timeout=5)
