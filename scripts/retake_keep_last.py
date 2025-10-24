"""scripts.retake_keep_last
用途：驱动单章处理流程，调用 ``onepass.pipeline.run_once`` 生成字幕、EDL 与 Audition 标记。
依赖：Python 标准库 argparse、os、pathlib、sys；内部模块 ``onepass.pipeline``、``onepass.types``、``onepass.ux``。
示例：
  python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out --aggr 60
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

from scripts.clean_outputs import CleanResult, CleanupError, human_size, perform_cleanup

PROJ_ROOT = Path(__file__).resolve().parents[1]
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from onepass.config import load_config
from onepass.pipeline import run_once
from onepass.types import Paths
from onepass.ux import enable_ansi, log_err, log_info, log_ok, log_warn, section


def clamp(value: int, min_value: int = 0, max_value: int = 100) -> int:
    """Clamp integer ``value`` between ``min_value`` and ``max_value`` inclusive."""

    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the script."""

    parser = argparse.ArgumentParser(
        description=(
            "读取 ASR JSON 与原文 TXT，执行单章“保留最后一遍 + 去口癖 + 断句”流程，输出字幕、EDL 与 Audition 标记。"
        ),
        epilog=(
            "示例：\n"
            "  python scripts/retake_keep_last.py ^\n"
            "    --json data/asr-json/001.json ^\n"
            "    --original data/original_txt/001.txt ^\n"
            "    --outdir out --aggr 60\n\n"
            "仅生成字幕/EDL/标记，不渲染音频：\n"
            "  python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --outdir out --dry-run\n\n"
            "使用自定义配置（覆盖默认阈值与口癖词表）：\n"
            "  python scripts/retake_keep_last.py --json data/asr-json/001.json --original data/original_txt/001.txt --config config/my_config.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", required=True, help="ASR 词级时间戳 JSON 文件路径，例如 data/asr-json/001.json")
    parser.add_argument("--original", required=True, help="原文 TXT 文件路径，例如 data/original_txt/001.txt")
    parser.add_argument("--outdir", default="out", help="输出目录，默认 out")
    parser.add_argument("--aggr", type=int, default=50, help="重录裁剪力度 (0-100)，默认 50")
    parser.add_argument(
        "--config",
        default="config/default_config.json",
        help="自定义配置文件路径，默认 config/default_config.json。文件不存在时退回默认配置。",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅生成字幕/EDL/标记，不触碰音频")
    parser.add_argument(
        "--regen",
        action="store_true",
        help="处理前清理旧的字幕/EDL/标记等产物，再重新生成",
    )
    parser.add_argument(
        "--hard-delete",
        action="store_true",
        help="搭配 --regen 使用，跳过回收区直接删除旧产物（危险）",
    )
    parser.add_argument(
        "--align-mode",
        choices=["fast", "accurate", "hybrid"],
        help="句级对齐策略，默认为配置文件中的 align_strategy",
    )
    parser.add_argument(
        "--align-sim",
        type=float,
        help="句级匹配最低相似度阈值，覆盖配置中的 align_min_sim",
    )
    parser.add_argument(
        "--keep",
        choices=["last", "best"],
        help="重复句保留策略，覆盖配置中的 overlap_keep",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="强制开启详细日志")
    verbosity.add_argument("--quiet", action="store_true", help="关闭大部分进度输出")
    return parser.parse_args()


def _resolve_path(path_str: str) -> Path:
    """Resolve ``path_str`` relative to the current working directory."""

    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _relative_to_cwd(path: Path) -> str:
    """Return a string representation of ``path`` relative to the current working directory."""

    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path.resolve())


def _validate_inputs(json_path: Path, original_path: Path) -> None:
    """Ensure the required input files exist."""

    missing: list[str] = []
    if not json_path.exists():
        missing.append(f"ASR JSON 未找到：{_relative_to_cwd(json_path)}")
    if not original_path.exists():
        missing.append(f"原文 TXT 未找到：{_relative_to_cwd(original_path)}")
    if missing:
        for line in missing:
            log_err(line)
        sys.exit(2)


def _prepare_config(config_arg: str) -> Path | None:
    """Return a ``Path`` for the config file when it exists, otherwise ``None``."""

    if not config_arg:
        return None
    config_path = _resolve_path(config_arg)
    if config_path.exists():
        return config_path
    log_warn(f"配置文件不存在：{_relative_to_cwd(config_path)}，将使用默认配置。")
    return None


def _write_stats_log(outdir: Path, stem: str, stats: Dict[str, Any]) -> Path:
    """Write statistics into ``outdir/<stem>.log`` and return the written path."""

    outdir.mkdir(parents=True, exist_ok=True)
    log_path = outdir / f"{stem}.log"
    lines = [f"{key}: {value}" for key, value in stats.items()]
    log_path.write_text("\n".join(lines) + "\n", "utf-8")
    return log_path


def _determine_verbose(args: argparse.Namespace) -> bool:
    """Return the effective verbose flag based on CLI and environment."""

    env_verbose = os.environ.get("ONEPASS_VERBOSE", "1") != "0"
    if getattr(args, "quiet", False):
        return False
    if getattr(args, "verbose", False):
        return True
    return env_verbose


def main() -> int:
    """Entry point for the CLI script."""

    enable_ansi()
    try:
        args = parse_args()
        verbose_flag = _determine_verbose(args)

        if args.hard_delete and not args.regen:
            log_err("--hard-delete 需搭配 --regen 使用。")
            return 2

        section("参数确认")
        json_path = _resolve_path(args.json)
        original_path = _resolve_path(args.original)
        outdir = _resolve_path(args.outdir)
        log_info(f"ASR JSON: {_relative_to_cwd(json_path)}")
        log_info(f"原文 TXT: {_relative_to_cwd(original_path)}")
        log_info(f"输出目录: {_relative_to_cwd(outdir)}")
        log_info(f"去口癖力度: {args.aggr}")
        log_info(f"dry-run: {'是' if args.dry_run else '否'}")

        _validate_inputs(json_path, original_path)

        aggr = clamp(args.aggr)
        config_path = _prepare_config(args.config)

        stem = json_path.stem
        paths = Paths(json=json_path, original=original_path, outdir=outdir)

        regen_result: CleanResult | None = None
        if args.regen:
            section("重新生成：清理旧产物")
            try:
                regen_result = perform_cleanup(
                    stems=[stem],
                    categories={"generated"},
                    mode="hard" if args.hard_delete else "trash",
                    assume_yes=True,
                    dry_run=False,
                    reporter=log_info,
                    emit_summary=False,
                )
            except CleanupError as exc:
                log_err(f"清理失败：{exc}")
                return 2
            if regen_result.files == 0:
                log_info("未发现需要清理的旧文件。")
            else:
                log_ok(
                    f"已清理 {regen_result.files} 个旧文件（{human_size(regen_result.bytes_total)}）。"
                )

        overrides: Dict[str, Any] = {}
        if args.align_mode:
            overrides["align_strategy"] = args.align_mode
        if args.align_sim is not None:
            overrides["align_min_sim"] = float(args.align_sim)
        if args.keep:
            overrides["overlap_keep"] = args.keep

        preview_cfg = load_config(config_path)
        if overrides:
            preview_cfg.update(overrides)

        section("启动流水线")
        log_info(f"开始处理 {stem}，详细日志 {'开启' if verbose_flag else '关闭'}")
        result = run_once(
            stem,
            paths,
            aggr=aggr,
            config_path=config_path,
            cfg_overrides=overrides if overrides else None,
            verbose=verbose_flag,
        )

        outputs = {name: Path(path) for name, path in result.get("outputs", {}).items()}
        stats: Dict[str, Any] = result.get("stats", {})

        section("产物列表")
        stats_log = _write_stats_log(outdir, stem, stats)
        outputs.setdefault("stats_log", stats_log)
        for key, path in sorted(outputs.items()):
            log_ok(f"{key:>10s}: {_relative_to_cwd(path)}")

        strategy = preview_cfg.get("align_strategy", "hybrid")
        min_sim = float(preview_cfg.get("align_min_sim", 0.84))
        keep_mode = preview_cfg.get("overlap_keep", "last")
        log_info(f"对齐策略：strategy={strategy}，min_sim={min_sim:.2f}，keep={keep_mode}")
        if "diff" in outputs:
            log_info(f"差异报告：{_relative_to_cwd(outputs['diff'])}")

        filler_removed = stats.get("filler_removed", 0)
        retake_cuts = stats.get("retake_cuts", 0)
        shortened_ms = stats.get("shortened_ms", 0)
        shortened_s = shortened_ms / 1000
        long_pauses = stats.get("long_pauses", 0)
        duplicated = stats.get("duplicated_sentences", 0)

        section("统计信息")
        log_info(f"重录段数: {retake_cuts}")
        log_info(f"口癖移除词数: {filler_removed}")
        log_info(f"预计缩短时长: {shortened_s:.2f}s")
        log_info(f"长停顿收紧: {long_pauses}")
        log_info(f"删除重复句: {duplicated}")
        if args.dry_run:
            log_warn("dry-run 模式：未触碰音频渲染流程。")

        if args.regen:
            cleaned = regen_result.files if regen_result else 0
            cleaned_size = human_size(regen_result.bytes_total) if regen_result else "0 B"
            log_ok(
                f"已清理 {cleaned} 个旧文件（{cleaned_size}），已重新生成 5+1 件套。"
            )

        log_ok("流程完成")
        return 0
    except KeyboardInterrupt:
        log_warn("用户取消操作。")
        return 1
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive coding
        log_err(f"执行失败：{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
