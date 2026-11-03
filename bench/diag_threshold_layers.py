# -*- coding: utf-8 -*-
"""按 limitation 分层：高阈值新增了哪些 win_rate 决策、是否改变选格、增量耗时。

=================== 为什么先做这个 ====================
上一轮把**整局耗时**的 p99（181s）与**单次决策**预算（30s）直接比较，
口径错误。而且盲目把整局样本从 12 扩到 60 局成本很高（th=16 单局可达 181s），
胜率信息却有限。

本脚本先回答更便宜、更关键的问题：
  · 提高阈值后，**新增**了哪些 win_rate 决策（按 limitation 分层）
  · 这些新增决策里，**实际改变了选格**的比例是多少
  · 这些新增决策的**增量耗时**是多少（单决策口径）

若"新增决策几乎都不改变选格"，那提阈值就是纯成本无收益，
不必扩样；若"有相当比例改变选格"，才值得扩样验证胜率。

=================== 测量方式（单决策口径） ====================
拦截 `ProbabilityMixin.win_rate`，记录每次调用的：
  · 当时的 limitation（由 len(clicks9)*0.8 + log2(total) 复算）
  · 调用耗时（ms）—— **单决策口径**
  · 是否真的被调用（阈值是否覆盖到）
再对比两档阈值（低/高），看**高阈值独有**的那些调用。

选格是否改变：比较 win_rate 返回的 argmax 格 与 贪心分支
（process_bigger_situation）会选的格是否相同。

用法:
  python bench/diag_threshold_layers.py --low 8 --high 16 --games 8
"""
import argparse
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import time as _time_mod                      # noqa: E402
_REAL_SLEEP = _time_mod.sleep


def pctl(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def run_with_threshold(th, args):
    """跑一批局，返回每次 win_rate 调用的记录。"""
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "thlayer"), True)

    import ab_policy as ab
    import utils.probability as prob

    calls = []
    orig_wr = prob.ProbabilityMixin.win_rate

    def hooked(self, clicks, clicks9, res_list, cell_value, ck, num10):
        # 复算 limitation（与 number5_1 同一公式）
        total = 1
        for c in ck:
            total *= int(c)
        lim = len(clicks9) * 0.8 + math.log2(max(total, 1))
        t0 = _time_mod.perf_counter()
        out = orig_wr(self, clicks, clicks9, res_list, cell_value, ck, num10)
        dt = (_time_mod.perf_counter() - t0) * 1000.0
        # win_rate 返回 (res, sorted_clicks, total, clicks2p)
        res, sclicks = out[0], out[1]
        import numpy as np
        arr = np.asarray(res, float)
        arg = int(np.argmax(arr)) if arr.size else -1
        pick_wr = tuple(sclicks[arg]) if 0 <= arg < len(sclicks) else None
        # 贪心会选谁：用 clicks2p（safety）最大者
        c2p = out[3] or {}
        pick_greedy = None
        if c2p:
            pick_greedy = max(c2p.items(), key=lambda kv: kv[1])[0]
        calls.append({
            "lim": round(float(lim), 2),
            "n_clicks9": len(clicks9),
            "total": int(total),
            "ms": dt,
            "pick_wr": list(pick_wr) if pick_wr else None,
            "pick_greedy": list(pick_greedy) if pick_greedy else None,
            "same_pick": (pick_wr == pick_greedy) if pick_wr and pick_greedy else None,
            "n_cand": 1 + len(clicks) + len(clicks9),
        })
        return out

    prob.ProbabilityMixin.win_rate = hooked
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            try:
                ab.play_one(sd, 30, 16, 99, args.arm, 80, args.world_cap,
                            args.node_cap, wr_threshold=th)
            except Exception:                      # noqa: BLE001
                pass
    finally:
        prob.ProbabilityMixin.win_rate = orig_wr
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--low", type=float, default=8.0)
    ap.add_argument("--high", type=float, default=16.0)
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    args = ap.parse_args()

    print("=" * 96)
    print(f"阈值分层诊断：th={args.low} vs th={args.high}，"
          f"{args.games} 局，单决策口径")
    print("=" * 96)

    print(f"\n跑 th={args.low} ...", flush=True)
    lo_calls = run_with_threshold(args.low, args)
    print(f"  捕获 {len(lo_calls)} 次 win_rate 调用")
    print(f"跑 th={args.high} ...", flush=True)
    hi_calls = run_with_threshold(args.high, args)
    print(f"  捕获 {len(hi_calls)} 次 win_rate 调用")

    # ---------- 单决策耗时分布 ----------
    print()
    print("=" * 96)
    print("单决策耗时（win_rate 单次调用，ms）—— 注意：这是**单决策**口径")
    print("=" * 96)
    print(f"{'th':>6} {'调用数':>7} {'p50':>9} {'p90':>10} {'p99':>10} {'max':>10}")
    print("-" * 96)
    for th, calls in ((args.low, lo_calls), (args.high, hi_calls)):
        ms = [c["ms"] for c in calls]
        if not ms:
            print(f"{th:>6} 无调用")
            continue
        print(f"{th:>6} {len(ms):>7} {statistics.median(ms):>9.1f} "
              f"{pctl(ms, 0.9):>10.1f} {pctl(ms, 0.99):>10.1f} {max(ms):>10.1f}")

    # ---------- limitation 分层 ----------
    print()
    print("=" * 96)
    print("按 limitation 分层（两档各自的调用分布）")
    print("=" * 96)
    buckets = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 24), (24, 10 ** 9)]
    print(f"{'limitation':>14} {'th低 次数':>10} {'th高 次数':>10} "
          f"{'高-低 新增':>10}")
    print("-" * 96)
    for lo, hi in buckets:
        nl = sum(1 for c in lo_calls if lo <= c["lim"] < hi)
        nh = sum(1 for c in hi_calls if lo <= c["lim"] < hi)
        label = f"[{lo},{hi if hi < 10**9 else '∞'})"
        print(f"{label:>14} {nl:>10} {nh:>10} {nh - nl:>10}")

    # ---------- 高阈值独有区间（limitation > low 且 <= high）----------
    print()
    print("=" * 96)
    print(f"高阈值**新增**覆盖区间：limitation ∈ ({args.low}, {args.high}]")
    print("=" * 96)
    added = [c for c in hi_calls if args.low < c["lim"] <= args.high]
    if not added:
        print("  该区间内无新增调用 —— 提高阈值没有多算任何局面")
    else:
        ms = [c["ms"] for c in added]
        same = [c["same_pick"] for c in added if c["same_pick"] is not None]
        n_same = sum(1 for x in same if x)
        print(f"  新增调用数: {len(added)}")
        print(f"  单决策耗时: p50 {statistics.median(ms):.1f}ms  "
              f"p90 {pctl(ms, 0.9):.1f}ms  max {max(ms):.1f}ms")
        print(f"  选格是否与贪心相同: {n_same}/{len(same)} 相同"
              f"（{n_same / max(1, len(same)) * 100:.0f}%）")
        if same:
            print(f"  ⇒ **改变选格的比例** = "
                  f"{(len(same) - n_same) / len(same) * 100:.0f}%")
            if n_same == len(same):
                print("  ⇒ 新增决策**全部与贪心选同一格** ⇒ 纯成本、无收益")
        print(f"  新增调用总耗时: {sum(ms) / 1000:.1f}s")

    # ---------- 超单决策预算的调用 ----------
    BUDGET_MS = 30_000.0
    print()
    print("=" * 96)
    print(f"超单决策预算（{BUDGET_MS / 1000:.0f}s）的调用数")
    print("=" * 96)
    for th, calls in ((args.low, lo_calls), (args.high, hi_calls)):
        over = [c for c in calls if c["ms"] > BUDGET_MS]
        print(f"  th={th}: {len(over)}/{len(calls)} 次调用超 "
              f"{BUDGET_MS / 1000:.0f}s"
              + (f"（最慢 {max(c['ms'] for c in over) / 1000:.1f}s）" if over else ""))

    out = os.path.join(ROOT, "state", "ab", "th_layer.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"low": args.low, "high": args.high,
                   "lo_calls": lo_calls, "hi_calls": hi_calls},
                  f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
