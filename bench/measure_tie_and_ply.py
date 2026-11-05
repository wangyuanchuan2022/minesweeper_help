# -*- coding: utf-8 -*-
"""测量：平局集大小 × 复杂度，以及 ply_score 耗时 × 复杂度。

=================== 用途（用户设计要求）====================
用户设计："在能前瞻的时候前瞻，并在前瞻的结果基础上做启发式破平局；
          前瞻深度应该和复杂度有关"

要在不改生产代码的前提下先回答三个问题（历史教训：ad1 因参数没对准分布而从未触发）：
  Q1 平局集有多大？—— 它决定阶段 3 要算多少个候选格的 ply_score
  Q2 每个候选的 ply_score(k) 耗时多少？—— 它决定 k 能取多大
  Q3 复杂度代理（limitation）与实际成本的关系是否单调？—— 决定自适应规则形态

=================== 复杂度代理 ====================
沿用生产定义（`utils/probability.py:793`）：
    limitation = len(clicks9) * 0.8 + math.log2(total)
本脚本直接从 case 重算它，不依赖 journal 记录值。

=================== 输出 ====================
两张分层表 + 一份 JSON，按 limitation 分桶给出：
  · 平局集大小分布（中位/p90/最大）
  · 平局集候选数（= 阶段 3 的调用次数）
  · ply_score(k=2)/(k=3) 单点耗时（中位/p90/最大）
  · 估算：一个决策点的阶段 3 总耗时 = 平局集大小 × 单格耗时

用法:
  python bench/measure_tie_and_ply.py --per-bucket 8
"""
import argparse
import json
import math
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
from arms_lookahead import ExactEngine, WKey               # noqa: E402
from dump_metric_matrix_ext import case_key, count_worlds  # noqa: E402

# 与生产一致的平局容差（probability.py:1206 用 0.0005）
TIE_EPS = 0.0005
# limitation 分桶（生产 wr_threshold 默认 8 / 6+bonus，故从 8 起分）
LIM_EDGES = [8, 10, 12, 14, 16, 20]


def limitation_of(case):
    """复算生产同款复杂度代理。"""
    total = int(case.get("total", 0))
    len9 = len(case.get("clicks9", []))
    return len9 * 0.8 + math.log2(max(1, total))


def bucket_of(lim):
    for i, e in enumerate(LIM_EDGES):
        if lim <= e:
            lo = LIM_EDGES[i - 1] if i > 0 else 0
            return "[%g,%g]" % (lo, e)
    return ">%g" % LIM_EDGES[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=8,
                    help="每个 limitation 桶取多少点")
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "tie_and_ply_cost.json"))
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
    print("=" * 100)
    print("测量：平局集大小 与 ply_score 耗时（按 limitation 分层）")
    print("=" * 100)
    print("  合格点 %d，平局容差 eps=%.4f（与生产一致）" % (len(elig), TIE_EPS))

    # 按 limitation 分桶，每桶取 worlds 最小的若干个（成本导向）
    buckets = {}
    for nw, c in elig:
        b = bucket_of(limitation_of(c))
        buckets.setdefault(b, []).append((nw, c))
    order = [("[%g,%g]" % (LIM_EDGES[i - 1] if i > 0 else 0, e))
             for i, e in enumerate(LIM_EDGES)] + [">%g" % LIM_EDGES[-1]]

    print()
    print("  %-12s %6s %8s %10s %10s %10s %10s"
          % ("limitation", "取点", "平局集中位", "平局集p90",
             "ply2中位ms", "ply3中位ms", "阶段3估算ms"))
    print("  " + "-" * 84)

    rows = []
    for b in order:
        items = sorted(buckets.get(b, []), key=lambda t: t[0])[:args.per_bucket]
        if not items:
            continue
        tie_sizes, t2s, t3s, est = [], [], [], []
        for nw, c in items:
            try:
                eng = ExactEngine(c, node_cap=args.node_cap,
                                  time_cap=300.0, world_cap=None)
                wk = WKey.all(eng.n_worlds)
                cands = eng.candidates(wk, eng.opened0)
            except Exception:
                continue
            if not cands:
                continue
            # 阶段 1：S0（safety）
            saf = [eng.safety(cd, wk) for cd in cands]
            mx = max(saf)
            tie_idx = [i for i, s in enumerate(saf) if s >= mx - TIE_EPS]
            tie_sizes.append(len(tie_idx))
            # 阶段 3：只对平局集算 ply_score
            memo2, memo3 = {}, {}
            t0 = time.perf_counter()
            for i in tie_idx:
                eng.ply_score(cands[i], wk, eng.opened0, 2, memo2)
            dt2 = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            for i in tie_idx:
                eng.ply_score(cands[i], wk, eng.opened0, 3, memo3)
            dt3 = (time.perf_counter() - t0) * 1000
            t2s.append(dt2)
            t3s.append(dt3)
            est.append(dt3)
            rows.append({"limitation": limitation_of(c), "worlds": nw,
                         "n_cand": len(cands), "tie_size": len(tie_idx),
                         "ply2_ms": dt2, "ply3_ms": dt3})
        if not tie_sizes:
            continue
        med = lambda v: statistics.median(v) if v else float("nan")
        p90 = lambda v: (sorted(v)[int(len(v) * 0.9)] if v else float("nan"))
        print("  %-12s %6d %10.0f %10.0f %10.1f %10.1f %10.1f"
              % (b, len(tie_sizes), med(tie_sizes),
                 p90(tie_sizes), med(t2s), med(t3s), med(est)), flush=True)

    print()
    print("=" * 100)
    print("判读")
    print("=" * 100)
    if rows:
        allt = [r["tie_size"] for r in rows]
        all3 = [r["ply3_ms"] for r in rows]
        print("  全部 %d 点：平局集中位 %.0f（p90 %.0f，最大 %d）"
              % (len(rows), statistics.median(allt),
                 sorted(allt)[int(len(allt) * 0.9)], max(allt)))
        print("  ply3（仅平局集）耗时中位 %.1f ms，p90 %.1f ms，最大 %.1f ms"
              % (statistics.median(all3),
                 sorted(all3)[int(len(all3) * 0.9)], max(all3)))
        print()
        print("  ⇒ 关键：阶段 3 成本 = 平局集大小 × 单格 ply_score 耗时。")
        print("     实测中位：%.0f 个候选 × %.1f ms ≈ %.0f ms/决策点"
              % (statistics.median(allt), statistics.median(all3),
                 statistics.median(all3)))
        print()
        over = {b: sum(1 for v in all3 if v > b) for b in (30000, 10000, 2000, 1000)}
        print("  按生产预算档（阶段 3 单独超限比例）：")
        for bud, cnt in over.items():
            print("    %6d ms：超限 %d/%d = %.0f%%"
                  % (bud, cnt, len(all3), 100 * cnt / len(all3)))
        print()
        print("  ⚠️ 注意：本测量只含 worlds≤20000 且 Python 可算的点")
        print("     （深层 worlds>10000 的多数点 Python 算不出，未覆盖）")
        print("     因此本表**低估**深局面的真实成本。")

    out = {"tie_eps": TIE_EPS, "n_points": len(rows),
           "rows": rows,
           "lim_edges": LIM_EDGES,
           "caveat": "仅 worlds<=20000 且 Python 可算的点；深局面低估"}
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
