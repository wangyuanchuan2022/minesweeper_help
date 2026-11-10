# -*- coding: utf-8 -*-
"""依赖闭包分析：求「17万局测试」与「整个 GUI 产品」两个闭包的并集，
再与仓库实际文件求差集，得出「不属于该并集」的候选清除清单。

为什么要单独写脚本而不是 grep：
  · grep 只能找到字面 import；本仓有 package 内相对导入、`from utils import native`
    后经属性调 `native.mscore.decide` 这类间接用法；
  · GUI 入口 main.py 有大量延迟导入（函数内 import，用于加速启动/避免循环），
    纯文本扫描会漏；必须 AST 全量遍历（含嵌套作用域）。
  · 需要区分「被 import」与「被真正调用」——本脚本只做前者的保守闭包
    （宁可多留，不可误删生产依赖）。

口径声明：
  · 节点 = 仓库内 .py 文件（按模块名解析，非文件名猜测）
  · 边 = AST 中的 Import / ImportFrom（含函数体内），解析到仓内文件；
        相对导入按 package 层级解析；`from utils import native` 展开为
        同时依赖 utils/__init__.py 与 utils/native.py
  · 非 .py 资产（数据 json、ui/*.ui、图标、.pyd）单独作为「被引用资产」扫描
  · 外部三方包（PyQt5/numpy/cv2/...)只记录，不参与差集

输出：
  state/audit/dep_closure_gui.json        GUI 产品闭包
  state/audit/dep_closure_test170k.json   17万局测试闭包
  state/audit/dep_closure_union.json      并集 + 差集
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "state" / "audit"

# 仓库内的顶层包目录（用于把 import 名解析成路径）
PKG_DIRS = ("utils", "sim", "ui", "tools", "bench", "tests")
# 入口点
GUI_ENTRY = "main.py"
TEST_ENTRY = [
    "bench/stress_20k_production.py",   # 2 万局压测（17.9万局语料的生成器）
    "bench/probe_layer_usage.py",       # 17万局层使用取证
    "bench/verify_paper_layers.py",     # 17万局论文层核对
]
# 明确排除，不参与闭包（运行时产物/历史评审/缓存）
SKIP_DIRS = {".git", ".tools", ".idea", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "__pycache__", "output", "review", "review2", "agent-out", "chat-export",
             "md_img", "image", "logs", "state", "course", "icons", ".code-review-graph",
             ".claude", ".github", "cpp"}


def modname_to_path(mod):
    """模块名 → 仓内文件路径（或 None）。"""
    rel = mod.replace(".", "/")
    for cand in (f"{rel}.py", f"{rel}/__init__.py"):
        p = ROOT / cand
        if p.exists():
            return cand.replace("\\", "/")
    return None


def resolve_relative(py_path: Path, level: int, module):
    """相对导入解析：level=1 表示当前目录。"""
    base = py_path.parent
    for _ in range(level - 1):
        base = base.parent
    parts = []
    if module:
        parts = module.split(".")
    return base.joinpath(*parts)


def imports_of(py_path: Path):
    """返回 (仓内依赖文件集合, 外部包名集合)。"""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set(), set()
    deps, externals = set(), set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                p = modname_to_path(a.name)
                if p:
                    deps.add(p)
                elif top in PKG_DIRS:
                    # `import utils.x` 隐式依赖包 __init__
                    init = modname_to_path(top)
                    if init:
                        deps.add(init)
                else:
                    externals.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入
                tgt = resolve_relative(py_path, node.level, node.module)
                # 逐级向上补 __init__.py 依赖
                cur = py_path.parent
                for _ in range(node.level):
                    init = cur / "__init__.py"
                    if init.exists():
                        deps.add(init.relative_to(ROOT).as_posix())
                    cur = cur.parent
                cand_py = tgt.with_suffix(".py")
                cand_init = tgt / "__init__.py"
                if cand_py.exists():
                    deps.add(cand_py.relative_to(ROOT).as_posix())
                elif cand_init.exists():
                    deps.add(cand_init.relative_to(ROOT).as_posix())
                # from . import a, b —— module 为 None，names 是子模块
                if node.module is None:
                    for a in node.names:
                        sub = tgt / (a.name + ".py")
                        sub_init = tgt / a.name / "__init__.py"
                        if sub.exists():
                            deps.add(sub.relative_to(ROOT).as_posix())
                        elif sub_init.exists():
                            deps.add(sub_init.relative_to(ROOT).as_posix())
                else:
                    # from utils import native  → 也要依赖 utils/native.py
                    for a in node.names:
                        sub = tgt / (a.name + ".py")
                        sub_init = tgt / a.name / "__init__.py"
                        if sub.exists():
                            deps.add(sub.relative_to(ROOT).as_posix())
                        elif sub_init.exists():
                            deps.add(sub_init.relative_to(ROOT).as_posix())
            else:
                if not node.module:
                    continue
                top = node.module.split(".")[0]
                p = modname_to_path(node.module)
                if p:
                    deps.add(p)
                elif top in PKG_DIRS:
                    init = modname_to_path(top)
                    if init:
                        deps.add(init)
                else:
                    externals.add(top)
                # from utils import native
                if p and node.module in PKG_DIRS:
                    base = ROOT / node.module
                    for a in node.names:
                        sub = base / (a.name + ".py")
                        sub_init = base / a.name / "__init__.py"
                        if sub.exists():
                            deps.add(sub.relative_to(ROOT).as_posix())
                        elif sub_init.exists():
                            deps.add(sub_init.relative_to(ROOT).as_posix())
    return deps, externals


def closure(entries):
    """从入口文件出发做传递闭包。"""
    seen, externals = set(), set()
    queue = [e for e in entries if (ROOT / e).exists()]
    missing_entries = [e for e in entries if not (ROOT / e).exists()]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        deps, ext = imports_of(ROOT / rel)
        externals |= ext
        for d in deps:
            if d not in seen:
                queue.append(d)
    return seen, externals, missing_entries


def asset_refs(files):
    """在给定文件里扫描「被引用的非 .py 资产」（数据/UI/图标/pyd）。"""
    pats = [
        re.compile(r'["\']((?:bench/data|state/[\w./-]+|ui/[\w./-]+|icons/[\w./-]+)'
                   r'[\w./-]*\.(?:json|jsonl|ui|png|ico|txt|yaml|yml))["\']'),
        re.compile(r'mscore\.cp310-win_amd64\.pyd'),
    ]
    found = set()
    for rel in files:
        p = ROOT / rel
        if not p.exists() or p.suffix != ".py":
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        for pat in pats:
            for m in pat.finditer(txt):
                found.add(m.group(1) if m.groups() else "utils/mscore.cp310-win_amd64.pyd")
    return sorted(found)


def all_repo_py():
    out = set()
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        out.add(rel)
    return out


def main():
    AUDIT.mkdir(parents=True, exist_ok=True)

    gui, gui_ext, gui_missing = closure([GUI_ENTRY])
    test, test_ext, test_missing = closure(TEST_ENTRY)
    union = gui | test

    repo_py = all_repo_py()
    outside = sorted(repo_py - union)

    # ---- GUI 闭包里「谁依赖谁」摘要（便于人工核验）----
    def tree_of(entry):
        lines = []

        def walk(rel, depth, seen):
            lines.append("  " * depth + rel)
            if rel in seen or depth > 6:
                return
            seen.add(rel)
            deps, _ = imports_of(ROOT / rel)
            for d in sorted(deps):
                walk(d, depth + 1, seen)

        walk(entry, 0, set())
        return lines

    report = {
        "口径": {
            "gui_entry": GUI_ENTRY,
            "test_entry": TEST_ENTRY,
            "说明": "闭包 = AST 全量导入（含函数体内延迟导入 + 相对导入）传递闭包；"
                    "不含运行时产物/历史评审/缓存目录",
        },
        "gui_closure": {
            "files": sorted(gui),
            "count": len(gui),
            "external_packages": sorted(gui_ext),
            "missing_entries": gui_missing,
        },
        "test170k_closure": {
            "files": sorted(test),
            "count": len(test),
            "external_packages": sorted(test_ext),
            "missing_entries": test_missing,
        },
        "union": {
            "files": sorted(union),
            "count": len(union),
            "external_packages": sorted(gui_ext | test_ext),
        },
        "outside_union": {
            "files": outside,
            "count": len(outside),
        },
        "assets_referenced": asset_refs(union),
        "gui_tree": tree_of(GUI_ENTRY),
    }

    (AUDIT / "dep_closure_gui.json").write_text(
        json.dumps({"files": sorted(gui), "external": sorted(gui_ext)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (AUDIT / "dep_closure_test170k.json").write_text(
        json.dumps({"files": sorted(test), "external": sorted(test_ext)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (AUDIT / "dep_closure_union.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("GUI 闭包:      %d 个 .py" % len(gui))
    print("17万局测试闭包: %d 个 .py" % len(test))
    print("并集:          %d 个 .py" % len(union))
    print("仓内 .py 总数: %d" % len(repo_py))
    print("并集之外:      %d 个 .py" % len(outside))
    print()
    print("--- GUI 闭包文件 ---")
    for f in sorted(gui):
        print("   ", f)
    print()
    print("--- 17万局测试闭包文件 ---")
    for f in sorted(test):
        print("   ", f)
    print()
    print("--- 外部三方包（GUI ∪ 测试）---")
    print("   ", sorted(gui_ext | test_ext))
    print()
    print("--- 并集之外的 .py（候选清除，%d）---" % len(outside))
    for f in outside:
        print("   ", f)
    print()
    print("--- 被引用的非 .py 资产 ---")
    for a in report["assets_referenced"]:
        print("   ", a)
    print()
    print("report:", AUDIT / "dep_closure_union.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
