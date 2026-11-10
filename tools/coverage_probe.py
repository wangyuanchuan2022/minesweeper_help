# -*- coding: utf-8 -*-
"""无外部依赖的语句行覆盖率探针（coverage.py 装不上时的可复现替代）。

口径（必须随读数一起声明，不可与 coverage.py 混用）：
  - 分母 = AST 判定的「可执行语句行」集合（每模块去重后的行号）。
    覆盖 ast.stmt 子类的 lineno，外加 except/else/finally 等子句体首行；
    不含注释、docstring、空行、声明性 import 行之外无副作用行。
  - 分子 = unittest 跑测期间 sys.settrace 观察到的 (file, line) 命中。
  - 只统计 WHITELIST 里的生产模块；分母固定，不因 import 失败而漂移。
  - 100% = 该口径下所有白名单模块的可执行行全部命中。
    不含分支覆盖、不含 C++、不含真实 GUI/Arbiter 交互。

用法：
  python tools/coverage_probe.py                 # 跑 unittest 并输出报告
  python tools/coverage_probe.py --json out.json
  python tools/coverage_probe.py --list-only     # 只列分母，不跑测试
"""
import argparse
import ast
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 生产模块白名单（相对仓库根）。排除生成资源与纯声明文件。
WHITELIST = [
    "main.py", "setting.py", "logger.py", "color.py",
    "utils/__init__.py", "utils/analyze_decide_trace.py", "utils/clicking.py",
    "utils/combinatorics.py", "utils/decide_trace.py", "utils/deduction.py",
    "utils/e2e_probe.py", "utils/misc.py", "utils/mm0.py", "utils/native.py",
    "utils/probability.py", "utils/solver.py", "utils/util.py", "utils/vision.py",
    "ui/edit_setting.py", "ui/preview_popup.py", "ui/screenshot.py", "ui/window.py",
]
# 明确排除（写进报告，避免有人以为漏算）
EXCLUDED = [
    "ui/bg_rc.py（10MB Qt 生成资源，无可执行逻辑）",
    "bench/（210 个历史实验脚本，史料，非生产）",
    "sim/（离线模拟器）、tools/、tests/、state/、docs/、review*/",
    "cpp/mscore.cpp（C++17，pybind11，非 Python 语句覆盖口径）",
]


def executable_lines(path):
    """AST 判定「跑测期间可命中」的可执行语句行号集合。

    口径修正（注入必红自测发现）：模块级 def/class/import 等**声明行**在
    import 阶段执行，而 tracer 只在跑测期间挂上 ⇒ 这些行永远不会被命中。
    若把它们算进分母，100% 结构性不可达。故只统计函数/方法**体内**的
    可执行语句行，与 coverage.py statement coverage 口径一致。
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    lines = set()
    # 只遍历函数/方法体（含 lambda 不适用，lambda 无 stmt 体）
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if sub is node:
                continue
            if isinstance(sub, ast.stmt):
                lines.add(sub.lineno)
        # except/else/finally 子句体首行也算独立可执行点
        for sub in ast.walk(node):
            for field in ("body", "orelse", "finalbody", "handlers"):
                seq = getattr(sub, field, None)
                if not isinstance(seq, list):
                    continue
                for child in seq:
                    if isinstance(child, ast.stmt):
                        lines.add(child.lineno)
    return lines


def build_denominator():
    denom = {}
    missing = []
    for rel in WHITELIST:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        denom[str(p.resolve())] = {"rel": rel, "lines": executable_lines(p)}
    return denom, missing


def run_tests_with_trace(denom):
    """跑 unittest discover，用两级 tracer 只记录白名单文件的行命中。

    性能关键：全局 tracer 只在 'call' 事件判断文件归属，
    非白名单 frame 返回 None（该 frame 及其子调用不再逐行回调），
    白名单 frame 才返回 line_tracer 逐行记录。
    旧版对每一行（含 numpy/Qt/unittest 全部第三方代码）都回调，
    307 项测试 18 分钟跑不完，属仪器缺陷而非代码慢。
    """
    hit = {f: set() for f in denom}
    cache = {}  # 原始 co_filename → 白名单 key，避免每行一次 realpath

    def make_line_tracer(key):
        def line_tracer(frame, event, arg):
            if event == "line":
                hit[key].add(frame.f_lineno)
            return line_tracer
        return line_tracer

    def global_tracer(frame, event, arg):
        if event != "call":
            return None
        raw = frame.f_code.co_filename
        if raw in cache:
            return cache[raw]
        try:
            key = os.path.realpath(raw)
        except OSError:
            cache[raw] = None
            return None
        t = make_line_tracer(key) if key in hit else None
        cache[raw] = t
        return t

    sys.settrace(global_tracer)
    try:
        loader = unittest.TestLoader()
        suite = loader.discover("tests", pattern="test*.py", top_level_dir=str(ROOT))
        result = unittest.TextTestRunner(verbosity=0).run(suite)
        stats = {"tests_run": result.testsRun, "failures": len(result.failures),
                 "errors": len(result.errors), "skipped": len(result.skipped)}
    finally:
        sys.settrace(None)
    return hit, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    denom, missing = build_denominator()
    total_lines = sum(len(v["lines"]) for v in denom.values())
    if args.list_only:
        print("denominator modules:", len(denom), "executable lines:", total_lines)
        for f, v in sorted(denom.items(), key=lambda x: x[1]["rel"]):
            print("  %-42s %5d lines" % (v["rel"], len(v["lines"])))
        if missing:
            print("MISSING:", missing)
        return

    hit, stats = run_tests_with_trace(denom)
    per = []
    for f, v in denom.items():
        d = v["lines"]
        h = hit.get(f, set()) & d
        per.append({"module": v["rel"], "denominator": len(d),
                    "hit": len(h), "miss": len(d - h),
                    "pct": round(100.0 * len(h) / len(d), 2) if d else 100.0,
                    "missing_lines": sorted(d - h)[:40]})
    per.sort(key=lambda x: x["pct"])
    total_hit = sum(p["hit"] for p in per)
    report = {
        "instrument": "tools/coverage_probe.py（sys.settrace + AST 可执行行分母）",
        "caveat": "与 coverage.py statement coverage 口径近似但不等价；不含分支覆盖与 C++。",
        "whitelist_modules": len(denom),
        "denominator_lines": total_lines,
        "hit_lines": total_hit,
        "overall_pct": round(100.0 * total_hit / total_lines, 2) if total_lines else 0.0,
        "tests": stats,
        "excluded": EXCLUDED,
        "missing_whitelist_files": missing,
        "per_module": per,
    }
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(out, encoding="utf-8")
    print("=== COVERAGE BASELINE ===")
    print("modules=%d  denominator_lines=%d  hit=%d  overall=%.2f%%" % (
        len(denom), total_lines, total_hit, report["overall_pct"]))
    print("tests:", stats)
    print("--- lowest modules ---")
    for p in per[:15]:
        print("  %-42s %6d/%6d  %6.2f%%" % (
            p["module"], p["hit"], p["denominator"], p["pct"]))
    if args.json:
        print("json:", args.json)


if __name__ == "__main__":
    main()
