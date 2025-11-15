#!/usr/bin/env python3
"""Run the all-in-one pipeline once and capture call/import telemetry."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_INPUT = ROOT_DIR / "materials"
DEFAULT_OUTPUT = ROOT_DIR / "out" / "dev_audit_run"
DEFAULT_AUDIT_DIR = ROOT_DIR / "audit"
TARGET_FUNCTIONS = (
    ("scripts.onepass_cli", "run_all_in_one"),
    ("scripts.onepass_cli", "run_prep_norm"),
    ("scripts.onepass_cli", "_process_single_text"),
    ("onepass.text_normalizer", "normalize_text_for_export"),
    ("onepass.text_normalizer", "split_sentences_with_rules"),
    ("onepass.retake_keep_last", "compute_retake_keep_last"),
)


def _install_import_audit(prefixes: Iterable[str]) -> list[dict[str, Any]]:
    """Collect import order for modules that start with the given prefixes."""

    import_log: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _hook(event: str, args: tuple[Any, ...]) -> None:
        if event != "import" or not args:
            return
        name = args[0]
        if not isinstance(name, str):
            return
        if not any(name == p or name.startswith(f"{p}.") for p in prefixes):
            return
        if name in seen:
            return
        seen.add(name)
        import_log.append({"module": name, "timestamp": time.time()})

    sys.addaudithook(_hook)
    return import_log


def _instrument_calls(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Wrap selected functions so we can see whether they execute."""

    import importlib
    import functools

    summary: dict[str, dict[str, Any]] = {}

    for module_name, attr_name in TARGET_FUNCTIONS:
        module = importlib.import_module(module_name)
        target = getattr(module, attr_name)
        fq_name = f"{module_name}.{attr_name}"
        summary[fq_name] = {"calls": 0, "last_status": None}

        @functools.wraps(target)
        def _wrapper(*args: Any, __orig=target, __name=fq_name, **kwargs: Any) -> Any:
            entry = {
                "function": __name,
                "start": time.time(),
                "args_type": [type(arg).__name__ for arg in args[:3]],
                "kwargs": sorted(kwargs.keys()),
            }
            records.append(entry)
            summary_entry = summary[__name]
            summary_entry["calls"] += 1
            try:
                result = __orig(*args, **kwargs)
                entry["status"] = "ok"
                summary_entry["last_status"] = "ok"
                return result
            except Exception as exc:  # pragma: no cover - diagnostics only
                entry["status"] = f"error:{exc.__class__.__name__}"
                summary_entry["last_status"] = entry["status"]
                raise
            finally:
                entry["end"] = time.time()
                entry["elapsed_sec"] = entry["end"] - entry["start"]

        setattr(module, attr_name, _wrapper)

    return summary


def _build_pipeline_argv(input_dir: Path, output_dir: Path, extra: Iterable[str]) -> list[str]:
    argv = [
        "all-in-one",
        "--in",
        str(input_dir),
        "--out",
        str(output_dir),
        "--render",
        "never",
        "--no-interaction",
    ]
    argv.extend(extra)
    return argv


def main() -> int:
    parser = argparse.ArgumentParser(description="Dev pipeline auditor")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="输入目录 (默认=materials)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出目录 (默认=out/dev_audit_run)")
    parser.add_argument("--report", type=Path, default=DEFAULT_AUDIT_DIR / "report.json", help="审计报告输出")
    parser.add_argument(
        "--extra-arg",
        dest="extra_args",
        action="append",
        default=[],
        help="附加传给 all-in-one 的参数（按顺序追加）",
    )
    args = parser.parse_args()

    import_log = _install_import_audit(("scripts", "onepass"))
    call_records: list[dict[str, Any]] = []
    call_summary = _instrument_calls(call_records)

    from scripts import onepass_cli

    args.output.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    pipeline_argv = _build_pipeline_argv(args.input, args.output, args.extra_args)
    start = time.time()
    exit_code = onepass_cli.main(pipeline_argv)
    elapsed = time.time() - start

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_dir": str(args.input),
        "output_dir": str(args.output),
        "cli_argv": pipeline_argv,
        "elapsed_sec": elapsed,
        "exit_code": exit_code,
        "import_log": import_log,
        "call_records": call_records,
        "call_summary": call_summary,
    }
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Audit report written to {args.report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
