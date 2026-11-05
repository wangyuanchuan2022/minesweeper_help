# -*- coding: utf-8 -*-
"""验证 limitation 是否是 ply 成本的可靠代理（决定 k 分档能否按它定）。

=================== 为什么必须先验证（Advisor 2026-11-04）====================
冒烟实测 12 点已显示 limitation 与 ply3 耗时**非单调**：

    limitation 桶   ply3 中位
    [8,10]           2.0 ms
    [10,12]          1.5 ms
    [12,14]        269.0 ms     ← 比下一桶高
    [14,16]         30.1 ms     ← 反而更低
    [16,20]       1408.9 ms     ← 最高
    >20            407.6 ms     ← 又降下来

若直接按 limitation 分档，同档内会混有 2ms 与 1400ms 的点，
"按档给延迟保证"不成立 ⇒ 定稿后必须推倒重做。

=================== 本脚本做什么 ====================
扩样本，计算 limitation 与实测耗时的：
  · Spearman 秩相关（单调性）
  · Kendall tau
  · 与**候选替代代理**的对比秩相关：
        n_cand       候选格数
        n_worlds     一致世界数
        total        总未开格数
        n_cand*log2(n_worlds)   简单复合代理
        n_cand*n_worlds         历史 ad1 用的 cost 形式
  · 关键判据：若某代理的秩相关 >= 0.8，可作为分档依据；
              否则必须改用「运行前成本预判」而非静态分档。

=================== 执行纪律 ====================
  · 先冒烟 4 点验证输出合理（Advisor 要求），再扩样本
  · 每点记录 (limitation, n_cand, n_worlds, total, ply3_ms)
  · 耗时用中位数（每点重复 3 次取中位，压单次抖动）

用法:
  python bench/validate_cost_proxy.py --smoke 4
  python bench/validate_cost_proxy.py --per-bucket 5
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

TIE_EPS = 0.0005
LIM_EDGES = [8, 10, 12, 14, 16, 20]


def limitation_of(c):
    return len(c.get("clicks9", [])) * 0.8 + math.log2(max(1, int(c.get("total", 0))))


def bucket_of(lim):
    prev = 0
    for e in LIM_EDGES:
        if lim <= e:
            return "[%g,%g]" % (prev, e)
        prev = e
    return ">%g" % LIM_EDGES[-1]


def measure_point(c, node_cap, repeats=3):
    """对一个点测 ply3（只算平局集）耗时，返回中位数与代理量。"""
    try:
        eng = ExactEngine(c, node_cap=node_cap, time_cap=300.0, world_cap=None)
        wk = WKey.all(eng.n_worlds)
        cands = eng.candidates(wk, eng.opened0)
    except Exception:
        return None
    if not cands:
        return None
    saf = [eng.safety(cd, wk) for cd in cands]
    mx = max(saf)
    tie_idx = [i for i, s in enumerate(saf) if s >= mx - TIE_EPS]
    times = []
    works = 0
    for _ in range(repeats):
        memo = {}
        t0 = time.perf_counter()
        for i in tie_idx:
            eng.ply_score(cands[i], wk, eng.opened0, 3, memo)
        times.append((time.perf_counter() - t0) * 1000)
        works = len(memo)
    return {
        "limitation": limitation_of(c),
        "n_cand": len(cands),
        "n_worlds": int(eng.n_worlds),
        "total": int(c.get("total", 0)),
        "tie_size": len(tie_idx),
        "ply3_ms": statistics.median(times),
        "ply3_reps": times,
        "ply_memo_entries": works,
    }


def spearman(x, y):
    """Spearman 秩相关（无 scipy 依赖，处理并列用平均秩）。"""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and abs(v[order[j + 1]] - v[order[i]]) < 1e-12:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx > 0 and dy > 0 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=0,
                    help=">0 时只测 N 点做冒烟验证")
    ap.add_argument("--per-bucket", type=int, default=5)
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "cost_proxy.json"))
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

    # 分桶（按 limitation）
    buckets = {}
    for nw, c in elig:
        buckets.setdefault(bucket_of(limitation_of(c)), []).append((nw, c))
    order = []
    prev = 0
    for e in LIM_EDGES:
        order.append("[%g,%g]" % (prev, e))
        prev = e
    order.append(">%g" % LIM_EDGES[-1])

    sel = []
    if args.smoke:
        # 冒烟：按 worlds 从各桶均匀取，共 smoke 点
        per = max(1, args.smoke // len(order))
        for b in order:
            sel.extend(sorted(buckets.get(b, []), key=lambda t: t[0])[:per])
        sel = sel[:args.smoke]
    else:
        for b in order:
            sel.extend(sorted(buckets.get(b, []), key=lambda t: t[0])[:args.per_bucket])

    print("=" * 100)
    print("验证 limitation 是否为 ply3 成本的可靠代理")
    print("=" * 100)
    print("  模式：%s，计划测 %d 点"
          % ("冒烟(%d)" % args.smoke if args.smoke else "扩样本(每桶 %d)"
             % args.per_bucket, len(sel)))
    print("  每点重复 3 次取中位（压单次抖动）")

    rows = []
    print()
    print("  %-12s %8s %7s %8s %9s %11s"
          % ("limitation桶", "worlds", "n_cand", "n_worlds", "tie", "ply3中位ms"))
    print("  " + "-" * 62)
    for nw, c in sel:
        r = measure_point(c, args.node_cap)
        if r is None:
            print("  %-12s %8d  —（引擎失败）" % (bucket_of(limitation_of(c)), nw))
            continue
        rows.append(r)
        print("  %-12s %8d %7d %8d %9d %11.1f"
              % (bucket_of(r["limitation"]), nw, r["n_cand"],
                 r["n_worlds"], r["tie_size"], r["ply3_ms"]), flush=True)

    if len(rows) < 3:
        print()
        print("  点太少，无法计算相关性")
        return 1

    print()
    print("=" * 100)
    print("秩相关分析：哪个量能预测成本？")
    print("=" * 100)
    ms = [r["ply3_ms"] for r in rows]
    proxies = {
        "limitation": [r["limitation"] for r in rows],
        "n_cand": [r["n_cand"] for r in rows],
        "n_worlds": [r["n_worlds"] for r in rows],
        "total": [r["total"] for r in rows],
        "tie_size": [r["tie_size"] for r in rows],
        "n_cand*log2(n_worlds)":
            [r["n_cand"] * math.log2(max(2, r["n_worlds"])) for r in rows],
        "n_cand*n_worlds": [r["n_cand"] * r["n_worlds"] for r in rows],
        "n_worlds*log2(n_worlds)":
            [r["n_worlds"] * math.log2(max(2, r["n_worlds"])) for r in rows],
    }
    print("  %-24s %10s" % ("代理量", "Spearman"))
    print("  " + "-" * 36)
    scored = []
    for name, v in proxies.items():
        rho = spearman(v, ms)
        scored.append((name, rho))
        flag = ""
        if rho == rho:
            if rho >= 0.8:
                flag = "  ← 强（可作分档依据）"
            elif rho >= 0.6:
                flag = "  ← 中"
            else:
                flag = "  ← 弱"
        print("  %-24s %10.3f%s" % (name, rho, flag))

    print()
    best = max((s for s in scored if s[1] == s[1]),
               key=lambda t: abs(t[1]), default=None)
    print("=" * 100)
    print("结论")
    print("=" * 100)
    lim_rho = dict(scored)["limitation"]
    print("  limitation 的秩相关 = %.3f" % lim_rho)
    if lim_rho == lim_rho and lim_rho >= 0.8:
        print("  ⇒ limitation 是可靠代理，可按它分档")
    else:
        print("  ⇒ **limitation 不是可靠代理**（< 0.8）")
        print("     按 limitation 分档会让同档内混入差异巨大的成本 ⇒ 不能作为延迟保证")
        if best:
            print()
            print("  最佳替代代理：%s（Spearman=%.3f）" % (best[0], best[1]))
            if best[1] >= 0.8:
                print("     ⇒ 应改用 %s 分档" % best[0])
            else:
                print("     ⇒ 所有静态代理都弱 ⇒ 应采用**运行前成本预判**")
                print("        （先估算成本再决定算不算前瞻，而非按局面特征分档）")

    out = {"n_points": len(rows), "rows": rows,
           "spearman": {k: v for k, v in scored},
           "best_proxy": {"name": best[0], "rho": best[1]} if best else None,
           "limitation_rho": lim_rho,
           "caveat": "样本含 Python 可算点；耗时受同进程漂移影响，已用 3 次中位缓解"}
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
