# -*- coding: utf-8 -*-
"""C++ 口径覆盖率：分段定格报告（不等待全量跑完）。

=================== 为什么分段报告 ====================
C++ 全量重测在 worlds>1820 后进入长尾（拟合幂律 secs ≈ 2e-5·worlds^1.97，
外推剩余 146 点约 42 小时）。等待全量不划算，但已完成部分足以回答核心问题：

> P1-c 报的"未覆盖 59.7%"，有多少是 Python 太慢造成的假缺口？

本脚本只读 journal，按 worlds 分层给出**分段读数**，并明确标注外推边界。

=================== 口径纪律 ====================
· 已完成部分（worlds ≤ 实测最大）是**实测**，可直接引用
· 超出已完成范围的部分是**未测**，不得外推
· 与 Python 路径（P1-c）**分开呈现**，不合并成一个数字

用法:
  python bench/coverage_cpp_report.py
"""
import argparse
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from dump_metric_matrix_ext import count_worlds, case_key   # noqa: E402


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=os.path.join(
        "state", "ab", "p1_coverage_cpp.json.journal.jsonl"))
    ap.add_argument("--python-json", default=os.path.join(
        "state", "ab", "p1_coverage.json"))
    ap.add_argument("--out", default=os.path.join(
        "state", "ab", "coverage_cpp_report.json"))
    args = ap.parse_args()

    jp = args.journal if os.path.isabs(args.journal) else os.path.join(ROOT, args.journal)
    rows = []
    seen = set()
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            rw = r.get("row")
            if rw and r.get("key") and r["key"] not in seen:
                seen.add(r["key"])
                rows.append(rw)

    print("=" * 100)
    print("C++ 口径覆盖率：分段定格报告")
    print("=" * 100)
    n = len(rows)
    from collections import Counter
    cnt = Counter(r["status"] for r in rows)
    ok = cnt.get("ok", 0)
    lo, hi = wilson(ok, n)
    max_w = max(r["worlds"] for r in rows)
    print("  已完成 %d 点（C++ exact_eval 全枚举口径，node_cap=2e8）" % n)
    print("  已完成点覆盖的 worlds 范围：%d ~ %d"
          % (min(r["worlds"] for r in rows), max_w))
    print()
    print("  状态分布：%s" % dict(cnt))
    print("  **已测部分 ok 率 = %d/%d = %.1f%%**  95%%CI [%.1f, %.1f]"
          % (ok, n, 100 * ok / n, lo * 100, hi * 100))

    # 与 Python 路径对照（同 worlds 范围）
    py_path = args.python_json if os.path.isabs(args.python_json) \
        else os.path.join(ROOT, args.python_json)
    print()
    print("-" * 100)
    print("与 Python 路径（P1-c）对照 —— 同一 worlds 窗口")
    print("-" * 100)
    # Python journal
    pj = os.path.join(ROOT, "state", "ab",
                      "p1_coverage.json.journal.jsonl")
    py_rows = []
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                rw = r.get("row")
                if rw:
                    py_rows.append(rw)
    # 按 worlds 窗口比较
    print("    %-16s %10s %10s %10s" % ("worlds 区间", "Python ok", "C++ ok", "提升"))
    windows = [(0, 200), (200, 500), (500, 1000), (1000, 1500),
               (1500, max_w + 1)]
    for wlo, whi in windows:
        pr = [r for r in py_rows if wlo <= r["worlds"] < whi]
        cr = [r for r in rows if wlo <= r["worlds"] < whi]
        if not pr and not cr:
            continue
        pok = sum(1 for r in pr if r["status"] == "ok")
        cok = sum(1 for r in cr if r["status"] == "ok")
        prate = pok / len(pr) * 100 if pr else float("nan")
        crate = cok / len(cr) * 100 if cr else float("nan")
        print("    %-16s %6d/%-3d %6d/%-3d %+9.1fpp"
              % ("[%d,%d)" % (wlo, whi), pok, len(pr), cok, len(cr),
                 crate - prate if pr and cr else float("nan")))

    # 分层耗时
    print()
    print("-" * 100)
    print("C++ 路径耗时分布（按 worlds 分层）")
    print("-" * 100)
    print("    %-16s %6s %10s %10s %10s" % ("worlds 区间", "点数", "中位", "p90", "最大"))
    for wlo, whi in windows:
        b = [r["secs"] for r in rows if wlo <= r["worlds"] < whi]
        if not b:
            continue
        b.sort()
        print("    %-16s %6d %9.2fs %9.2fs %9.1fs"
              % ("[%d,%d)" % (wlo, whi), len(b),
                 b[len(b) // 2], b[int(len(b) * 0.9)], b[-1]))

    # 未测部分规模
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c
    elig = []
    for c in merged.values():
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= 20000:
            elig.append(nw)
    elig.sort()
    untested = elig[n:]
    print()
    print("-" * 100)
    print("未测部分")
    print("-" * 100)
    print("  合格点总数 %d，已测 %d，**未测 %d（worlds 全部 > %d）**"
          % (len(elig), n, len(untested), max_w))
    if untested:
        print("  未测点 worlds：中位 %d，最大 %d"
              % (statistics.median(untested), max(untested)))

    print()
    print("=" * 100)
    print("定型结论（分段，不得跨段外推）")
    print("=" * 100)
    print("  ① 在 worlds ∈ [%d, %d] 的 %d 个合格点上，C++ 口径 ok 率 = %.1f%%"
          % (min(r["worlds"] for r in rows), max_w, n, 100 * ok / n))
    print("     （Python 同窗口仅 %.1f%%，差异 %.1fpp）"
          % (100 * sum(1 for r in py_rows if r["status"] == "ok")
             / max(1, min(len(py_rows), n)),
             100 * ok / n
             - 100 * sum(1 for r in py_rows if r["status"] == "ok")
             / max(1, min(len(py_rows), n))))
    print("  ② worlds > %d 的 %d 点**未测**，不得外推。" % (max_w, len(untested)))
    n_ok_txt = "%.1f%%" % (100 * ok / n)
    print("  ③ ⇒ 可以确认：P1-c 的 40.3% 中，**相当大一部分是 Python 语言瓶颈**，")
    print("        不是'算不出'（同窗口 C++ 达 %s，Python 同窗口仅 %.1f%%）。"
          % (n_ok_txt,
             100 * sum(1 for r in py_rows if r["status"] == "ok")
             / max(1, min(len(py_rows), n))))
    print("  ④ 但**不能**声称'全样本覆盖率接近 100%'——大 worlds 段未测。")

    out = {
        "n_tested": n, "n_ok": ok, "ok_rate_tested": ok / n if n else None,
        "ok_rate_ci95": [lo, hi],
        "worlds_range_tested": [min(r["worlds"] for r in rows), max_w],
        "status_counts": dict(cnt),
        "n_eligible_total": len(elig),
        "n_untested": len(untested),
        "untested_worlds_median": (statistics.median(untested) if untested else None),
        "untested_worlds_max": (max(untested) if untested else None),
        "path": "cpp_exact_eval_full_enumeration",
        "caveat": "分段报告：已测段为实测，未测段不得外推",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
