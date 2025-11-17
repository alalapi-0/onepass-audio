"""精准体检：定位真实调用链与冲突参数。

仅使用标准库，检查从 onepass_main.py → scripts/onepass_cli.py → 预处理与分句函数 → retake 核心的调用链。
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 计算项目根目录
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 确保 scripts 目录在路径中，以便导入 match_materials
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import onepass_main
    import scripts.onepass_cli as cli_module
    from onepass import text_normalizer
    from onepass import retake_keep_last
except ImportError as e:
    print(f"ERROR: 无法导入模块: {e}", file=sys.stderr)
    print(f"ROOT_DIR: {ROOT_DIR}", file=sys.stderr)
    print(f"sys.path: {sys.path[:3]}...", file=sys.stderr)
    sys.exit(1)


def get_source_file(obj: Any) -> Optional[str]:
    """获取对象的源文件路径（使用 inspect.getsourcefile）。"""
    try:
        source = inspect.getsourcefile(obj)
        if source:
            return str(Path(source).relative_to(ROOT_DIR))
    except (TypeError, OSError):
        pass
    return None


def trace_call_chain() -> Dict[str, Any]:
    """追踪从主入口到核心函数的调用链。"""
    chain = {}
    
    # 主入口
    chain["onepass_main"] = {
        "module": "onepass_main",
        "file": get_source_file(onepass_main) or "onepass_main.py",
        "functions": {}
    }
    
    # CLI 模块
    chain["onepass_cli"] = {
        "module": "scripts.onepass_cli",
        "file": get_source_file(cli_module) or "scripts/onepass_cli.py",
        "functions": {}
    }
    
    # 文本规范化
    chain["text_normalizer"] = {
        "module": "onepass.text_normalizer",
        "file": get_source_file(text_normalizer) or "onepass/text_normalizer.py",
        "functions": {}
    }
    
    # 关键函数追踪
    key_functions = [
        ("normalize_text_for_export", text_normalizer.normalize_text_for_export),
        ("split_sentences_with_rules", text_normalizer.split_sentences_with_rules),
        ("hard_collapse_whitespace", text_normalizer.hard_collapse_whitespace),
        ("compute_retake_keep_last", retake_keep_last.compute_retake_keep_last),
    ]
    
    for name, func in key_functions:
        source_file = get_source_file(func)
        chain["text_normalizer"]["functions"][name] = {
            "file": source_file or "unknown",
            "line": inspect.getsourcelines(func)[1] if hasattr(inspect, "getsourcelines") else None,
        }
    
    # CLI 关键函数
    cli_functions = [
        ("run_all_in_one", getattr(cli_module, "run_all_in_one", None)),
        ("run_prep_norm", getattr(cli_module, "run_prep_norm", None)),
        ("_rule_split_text", getattr(cli_module, "_rule_split_text", None)),
        ("_resolve_split_attach", getattr(cli_module, "_resolve_split_attach", None)),
    ]
    
    for name, func in cli_functions:
        if func is None:
            continue
        source_file = get_source_file(func)
        chain["onepass_cli"]["functions"][name] = {
            "file": source_file or "unknown",
            "line": inspect.getsourcelines(func)[1] if hasattr(inspect, "getsourcelines") else None,
        }
    
    return chain


def snapshot_parameters() -> Dict[str, Any]:
    """收集 CLI 默认与实际运行时的关键参数快照。"""
    params = {
        "normalization": {
            "char_map": "config/default_char_map.json",
            "collapse_lines": True,
            "hard_collapse_lines": True,
            "drop_ascii_parens": True,
            "preserve_fullwidth_parens": True,
            "ascii_paren_mapping": False,
            "squash_mixed_english": False,
        },
        "splitting": {
            "split_mode": "punct",
            "weak_punct_enable": False,
            "prosody_split": False,
            "split_attach": "right",
            "split_all_punct": True,
            "max_len": 24,
            "min_len": 8,
            "hard_max": 32,
            "hard_puncts": text_normalizer.DEFAULT_HARD_PUNCT,
            "soft_puncts": text_normalizer.DEFAULT_SOFT_PUNCT,
        },
        "matching": {
            "max_distance_ratio": 0.35,
            "min_anchor_ngram": 6,
            "dedupe_policy": "none",
            "pause_gap_sec": 0.5,
            "fallback_policy": "greedy",
        },
    }
    
    # 检查 CLI 默认值
    try:
        parser = cli_module.build_parser()
        prep_norm = parser._subparsers._group_actions[0].choices.get("prep-norm")
        if prep_norm:
            for action in prep_norm._actions:
                if hasattr(action, "dest") and hasattr(action, "default"):
                    dest = action.dest
                    default = action.default
                    if dest in ["collapse_lines", "hard_collapse_lines", "drop_ascii_parens",
                               "preserve_fullwidth_parens", "ascii_paren_mapping", "squash_mixed_english"]:
                        params["normalization"][dest] = default
                    elif dest in ["split_mode", "weak_punct_enable", "prosody_split", "split_attach",
                                 "min_len", "max_len", "hard_max", "hard_punct", "soft_punct"]:
                        if dest == "hard_punct":
                            params["splitting"]["hard_puncts"] = default or text_normalizer.DEFAULT_HARD_PUNCT
                        elif dest == "soft_punct":
                            params["splitting"]["soft_puncts"] = default or text_normalizer.DEFAULT_SOFT_PUNCT
                        else:
                            params["splitting"][dest] = default
    except Exception as e:
        params["_cli_parse_error"] = str(e)
    
    return params


def check_conflicts() -> List[Dict[str, Any]]:
    """检查死链/冲突开关。"""
    warnings = []
    
    # 检查 split_attach=left 但实现强制 right
    try:
        resolve_func = getattr(cli_module, "_resolve_split_attach", None)
        if resolve_func:
            # 检查函数实现
            source = inspect.getsource(resolve_func)
            if "left" in source and "强制为 right" in source or "强制 right" in source:
                warnings.append({
                    "type": "WARNING",
                    "message": "CLI 提供了 split_attach=left 但实现强制 right",
                    "location": "scripts/onepass_cli.py:_resolve_split_attach",
                    "advice": "CLI 层应移除 left 选项或显式改为 right",
                })
    except Exception:
        pass
    
    # 检查 legacy 模块
    try:
        from onepass import _legacy_text_norm
        legacy_file = get_source_file(_legacy_text_norm)
        if legacy_file:
            # 检查是否被主干调用
            try:
                import onepass.pipeline
                pipeline_source = inspect.getsource(onepass.pipeline)
                if "_legacy" in pipeline_source or "legacy" in pipeline_source.lower():
                    # 被调用，不是死链
                    pass
                else:
                    warnings.append({
                        "type": "INFO",
                        "message": "legacy 模块存在但可能未被主干调用",
                        "location": legacy_file,
                        "advice": "确认是否仍需要，否则标注为 Legacy",
                    })
            except Exception:
                pass
    except ImportError:
        pass
    
    # 检查 split_all_punct 默认值
    try:
        cfg = text_normalizer.TextNormConfig()
        if not getattr(cfg, "split_all_punct", True):
            warnings.append({
                "type": "WARNING",
                "message": "TextNormConfig.split_all_punct 默认值不是 True",
                "location": "onepass/text_normalizer.py:TextNormConfig",
                "advice": "应确保默认 split_all_punct=True 以符合'所有标点分句'要求",
            })
    except Exception:
        pass
    
    return warnings


def main() -> int:
    """主函数。"""
    parser = argparse.ArgumentParser(description="精准体检：调用链与参数快照")
    parser.add_argument("--in", dest="input_dir", default="materials", help="输入目录（默认 materials）")
    parser.add_argument("--out", dest="output_dir", default="out/audit", help="输出目录（默认 out/audit）")
    parser.add_argument("--glob-text", default="*.txt", help="文本匹配模式")
    parser.add_argument("--glob-words", default="*.words.json;*.json", help="词级 JSON 匹配模式（分号分隔）")
    parser.add_argument("--render", choices=["never", "auto", "always"], default="never", help="渲染模式（默认 never）")
    parser.add_argument("--emit-align", action="store_true", help="生成对齐文件")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 执行体检
    print("正在执行调用链核验...")
    callgraph = trace_call_chain()
    
    print("正在收集参数快照...")
    params = snapshot_parameters()
    
    print("正在检查冲突开关...")
    conflicts = check_conflicts()
    
    # 生成报告
    report = {
        "callgraph": callgraph,
        "params": params,
        "conflicts": conflicts,
        "timestamp": str(Path(__file__).stat().st_mtime) if Path(__file__).exists() else None,
    }
    
    # 写入 JSON
    json_path = output_dir / "report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON 报告已写入: {json_path}")
    
    # 写入 Markdown
    md_path = output_dir / "report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# 精准体检报告\n\n")
        f.write("## 调用链核验\n\n")
        for module_name, info in callgraph.items():
            f.write(f"### {module_name}\n\n")
            f.write(f"- 模块: `{info['module']}`\n")
            f.write(f"- 文件: `{info['file']}`\n")
            if info.get("functions"):
                f.write("\n关键函数:\n")
                for func_name, func_info in info["functions"].items():
                    f.write(f"- `{func_name}`: {func_info['file']}")
                    if func_info.get("line"):
                        f.write(f" (行 {func_info['line']})")
                    f.write("\n")
            f.write("\n")
        
        f.write("## 参数快照\n\n")
        f.write("### 规范化参数\n\n")
        for key, value in params["normalization"].items():
            f.write(f"- `{key}`: `{value}`\n")
        f.write("\n### 分句参数\n\n")
        for key, value in params["splitting"].items():
            f.write(f"- `{key}`: `{value}`\n")
        f.write("\n### 匹配参数\n\n")
        for key, value in params["matching"].items():
            f.write(f"- `{key}`: `{value}`\n")
        
        f.write("\n## 冲突与警告\n\n")
        if conflicts:
            for conflict in conflicts:
                f.write(f"### {conflict['type']}: {conflict['message']}\n\n")
                f.write(f"- 位置: `{conflict['location']}`\n")
                f.write(f"- 建议: {conflict['advice']}\n\n")
        else:
            f.write("未发现冲突。\n\n")
    
    print(f"Markdown 报告已写入: {md_path}")
    
    # 写入参数快照（单独文件）
    params_path = output_dir / "params.json"
    with params_path.open("w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)
    print(f"参数快照已写入: {params_path}")
    
    # 写入调用图（单独文件）
    callgraph_path = output_dir / "callgraph.json"
    with callgraph_path.open("w", encoding="utf-8") as f:
        json.dump(callgraph, f, ensure_ascii=False, indent=2)
    print(f"调用图已写入: {callgraph_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

