# -*- coding: utf-8 -*-
"""量化 3-ply 的在线成本（Advisor 要求：先测成本，再定方向）。

=================== 为什么必须先测成本 ====================
离线显示「安全 ∥ 3-ply 收益」并列主准则能把 regret 从 0.032926 降到 0.008893
（Δ=-0.024032，p<0.0001）。但这只是**离线 regret**。

生产上每个决策点都要真的算 3-ply。若单步耗时超出预算，
离线收益就拿不到 —— 整局会因超时降级到贪心，反而可能更差。

本脚本测：**深局面**（worlds 中位约 7580 那层）每个决策点
  · S0 单独的计算耗时（baseline）
  · 3-ply 的计算耗时（候选）
  · 是否落在生产预算内

=================== 生产预算参照 ====================
`utils/probability.py` 中 time_cap 相关设定（从代码读取，不猜）：
  · 决策预算：`time_cap=30s`（P1-c 口径）/ 生产 wr 路径按 `_wr_node_cap()`
  · 实际生产每步预算远小于 30s（连续对局中每步需在秒级完成）

本脚本同时报：绝对耗时、相对 S0 的倍数、以及按 30s/10s/2s/1s 四档预算判超限比例。

用法:
  python bench/measure_ply_cost.py --n-deep 20
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np                                        # noqa: E402
import utils.native as native                             # noqa: E402
from dump_metric_matrix_ext import case_key, count_worlds  # noqa: E402
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402

STRATA = [(5000, 7000), (7000, 12000), (12000, 20001)]


def timed_eval(hexw, case, node_cap, ply):
    """测一次 exact_eval 的墙钟耗时。返回 (secs, ok, err)。"""
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    t0 = time.perf_counter()
    try:
        ql, co, nodes, v = native.mscore.exact_eval(
            hexw, [tuple(x) for x in case["clicks"]],
            [tuple(x) for x in case["clicks9"]], cv,
            int(case["a"]), int(case["w"]), int(case["h"]), node_cap, ply)
        el = time.perf_counter() - t0
        return el, bool(ql), None
    except Exception as e:                                 # noqa: BLE001
        el = time.perf_counter() - t0
        return el, False, str(e)[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-deep", type=int, default=20,
                    help="每层取多少点（默认 20）")
    ap.add_argument("--node-cap", type=int, default=200_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "ply_cost.json"))
    args = ap.parse_args()

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
            elig.append((nw, c))
    elig.sort(key=lambda t: t[0])

    sel = []
    for lo, hi in STRATA:
        b = [(nw, c) for nw, c in elig if lo <= nw < hi]
        sel.extend(b[:args.n_deep])
    print("=" * 100)
    print("3-ply 在线成本测量（深局面）")
    print("=" * 100)
    print("  分层：%s，每层取 %d 点，合计 %d 点"
          % (STRATA, args.n_deep, len(sel)))
    print("  预算档：30s / 10s / 2s / 1s")

    rows = []
    print()
    print("  %4s %7s %7s %10s %10s %8s %s"
          % ("#", "worlds", "n_cand", "S0(ply=0)", "PLY3(ply=3)", "倍数", "备注"))
    print("  " + "-" * 78)
    for i, (nw, c) in enumerate(sel):
        worlds = enumerate_worlds_py(c)
        if not worlds:
            continue
        hexw = [format(m, "x") for m in worlds]
        # S0（ply=0 = after_click，即当前生产的完整求解）
        t_s0, ok0, err0 = timed_eval(hexw, c, args.node_cap, 0)
        # PLY3
        t_p3, ok3, err3 = timed_eval(hexw, c, args.node_cap, 3)
        n_cand = len(json.loads("[]"))  # placeholder
        ratio = (t_p3 / t_s0) if t_s0 > 1e-9 else float("nan")
        note = ""
        if err0 or err3:
            note = "ERR:%s" % ((err0 or err3)[:28])
        rec = {"worlds": nw, "secs_s0": t_s0, "secs_ply3": t_p3,
               "ratio": ratio, "ok_s0": ok0, "ok_ply3": ok3,
               "err": note or None}
        rows.append(rec)
        print("  %4d %7d %7s %9.2fs %9.2fs %8.2fx %s"
              % (i + 1, nw, "—", t_s0, t_p3, ratio, note), flush=True)

    ok_rows = [r for r in rows if r["ok_s0"] and r["ok_ply3"]]
    print()
    print("=" * 100)
    print("汇总（%d 点成功）" % len(ok_rows))
    print("=" * 100)
    if not ok_rows:
        print("  无成功点")
        return 1
    s0s = [r["secs_s0"] for r in ok_rows]
    p3s = [r["secs_ply3"] for r in ok_rows]
    ratios = [r["ratio"] for r in ok_rows if r["ratio"] == r["ratio"]]
    print("  S0 (ply=0)   ：中位 %.2fs，最大 %.2fs"
          % (statistics.median(s0s), max(s0s)))
    print("  PLY3 (ply=3) ：中位 %.2fs，最大 %.2fs，合计 %.0fs"
          % (statistics.median(p3s), max(p3s), sum(p3s)))
    if ratios:
        print("  耗时倍数（PLY3/S0）：中位 %.2fx，最大 %.2fx"
              % (statistics.median(ratios), max(ratios)))
    print()
    print("  按预算档的超限比例：")
    for budget in (30.0, 10.0, 2.0, 1.0):
        over = sum(1 for v in p3s if v > budget)
        print("    预算 %5.1fs：PLY3 超限 %d/%d = %.0f%%"
              % (budget, over, len(p3s), 100 * over / len(p3s)))
    print()
    print("  判读：")
    med = statistics.median(p3s)
    if med > 30.0:
        print("    ✘ 深局面 PLY3 中位耗时 %.1fs 已超 30s 上限 ⇒ 生产中绝大部分决策无法使用" % med)
        print("      ⇒ 离线收益（Δ=-0.024）在深局面**拿不到**，需重新设计（浅层并行/缓存/近似）")
    elif med > 10.0:
        print("    ⚠️ PLY3 中位 %.1fs，超出常见交互预算(1-2s)，仅在非实时对局可用" % med)
    elif med > 2.0:
        print("    ⚠️ PLY3 中位 %.1fs，接近上限，需与整局节奏一起评估" % med)
    else:
        print("    ✔ PLY3 中位 %.1fs，在秒级预算内可接受" % med)

    out = {
        "n_points": len(ok_rows),
        "strata": [list(s) for s in STRATA],
        "n_per_stratum": args.n_deep,
        "s0_median_s": statistics.median(s0s),
        "s0_max_s": max(s0s),
        "ply3_median_s": statistics.median(p3s),
        "ply3_max_s": max(p3s),
        "ratio_median": (statistics.median(ratios) if ratios else None),
        "row_ratio_max": (max(ratios) if ratios else None),
        "over_budget": {str(b): sum(1 for v in p3s if v > b) for b in
                        (30.0, 10.0, 2.0, 1.0)},
        "rows": rows,
        "caveat": "耗时读数受同进程漂移影响，不同点之间不宜互相排序；"
                  "此处只用单次墙钟做主判断（跨点量级对比）",
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
