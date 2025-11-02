# tools/find_redundant_scripts.py
# 用法：python tools/find_redundant_scripts.py > out/script_audit.txt
import os, re, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIRS = ["scripts", "tools"]
KEEP_KEYWORDS = {
    # 来自 README 的权威入口关键词（避免误删）
    "onepass_main.py",
    "retake_keep_last.py",
    "onepass_cli.py",
    "env_check.py",
    "edl_render.py",
    "edl_set_source.py",
    "smoke_test.py",
    "demo_run.ps1",
    "demo_run.sh",
}

# 读取 README 作为“被文档引用”的依据
readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="ignore") if (ROOT / "README.md").exists() else ""
readme_lower = readme.lower()

# 收集项目所有 .py/.ps1/.sh
candidates = []
for d in SCRIPTS_DIRS:
    p = ROOT / d
    if not p.exists():
        continue
    for fp in p.rglob("*"):
        if fp.is_file() and fp.suffix in {".py", ".ps1", ".sh"}:
            rel = fp.relative_to(ROOT).as_posix()
            candidates.append(rel)

# 搜索代码对脚本名的引用(import / 调用)
project_texts = []
for fp in ROOT.rglob("*.py"):
    try:
        project_texts.append(fp.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
PROJECT_ALL = "\n".join(project_texts)

def referenced_in_code(relpath:str)->bool:
    name = os.path.basename(relpath)
    stem = os.path.splitext(name)[0]
    # 粗略：作为模块名被 import；作为字符串被 mention；被 subprocess/run 调用等
    patterns = [
        rf"import\s+{re.escape(stem)}\b",
        rf"from\s+{re.escape(stem)}\s+import\b",
        re.escape(relpath),
        re.escape(name),
    ]
    for pat in patterns:
        if re.search(pat, PROJECT_ALL):
            return True
    return False

def referenced_in_readme(relpath:str)->bool:
    return os.path.basename(relpath).lower() in readme_lower

report = {"keep": [], "suspicious": [], "redundant_like": []}

for rel in sorted(candidates):
    base = os.path.basename(rel)
    lower = base.lower()
    keep_flag = (lower in KEEP_KEYWORDS)
    in_readme = referenced_in_readme(rel)
    in_code = referenced_in_code(rel)
    if keep_flag or in_readme or in_code:
        report["keep"].append(rel)
    else:
        # 进一步：打标签
        if re.search(r"(old|bak|backup|tmp|draft|playground|demo2|v\d+)", lower):
            report["redundant_like"].append(rel)
        else:
            report["suspicious"].append(rel)

outdir = ROOT / "out"
outdir.mkdir(parents=True, exist_ok=True)
with open(outdir / "script_audit.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("# OnePass-Audio 脚本审计（自动生成）")
print("## 建议保留（被 README 引用 / 被代码导入 / 关键白名单）")
for x in report["keep"]:
    print("  -", x)
print("\n## 高度疑似冗余（命名像旧版/备份/草稿）")
for x in report["redundant_like"]:
    print("  -", x)
print("\n## 需要人工判定（未在 README/代码中出现）")
for x in report["suspicious"]:
    print("  -", x)
print("\n明细 JSON：out/script_audit.json")
