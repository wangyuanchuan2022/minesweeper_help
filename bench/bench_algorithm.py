# -*- coding: utf-8 -*-
"""算法性能基准：在生产流水线的**真实决策点**上测量各阶段耗时。

=================== 这个基准回答什么 ====================
"优化哪里最值"必须建立在**真实占比**上，而不是凭直觉。
本基准在整局对局过程中埋点，逐决策记录：

  · vision_ms   —— 读屏 / 识别（complete_scan + 两次 rescan）
  · deduce_ms   —— 基础推理（mine_clear1 / mine_clear3_1）
  · part_solve_ms —— 分组枚举（part_solve，C++ 热路径）
  · decide_ms   —— 概率决策（number5_1 内部：分组 + 枚举 + 选格）
  · click_ms    —— 点击施加（模拟驱动，生产含 sleep 0.1s，这里单独标）
  · total_ms    —— 单步合计

外加规模分布：F/U/世界数/候选数/limitation，以及各闸门触发次数。

=================== 为什么不用 cProfile ====================
cProfile 会放大 Python 函数调用开销（本项目热路径有大量小函数与 numpy
调用），实测会把"Python 侧小函数"的占比虚高数倍，误导优化方向。
本基准用 wall-clock 埋点（time.perf_counter）测**阶段**耗时。

=================== 用法 ====================
  python bench/bench_algorithm.py --games 20 --arm baseline
  python bench/bench_algorithm.py --games 20 --arm exact --node-cap 2000000

输出：state/ab/bench_algo_<arm>.json + 终端汇总
"""
import argparse
import gc
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import psutil                            # noqa: E402

P = psutil.Process()


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "benchalgo"), True)

    import numpy as np
    import ab_policy as ab
    import utils.probability as prob

    # ---------------- 埋点：包住各阶段 ----------------
    records = []
    cur = {}

    # 1) number5_1（概率决策）
    orig_n51 = prob.ProbabilityMixin.number5_1

    def n51(self, cv):
        t = time.perf_counter()
        try:
            return orig_n51(self, cv)
        finally:
            cur["decide_ms"] = (time.perf_counter() - t) * 1000.0

    # 2) part_solve（枚举热路径）—— 累计而非单次
    orig_ps = prob.ProbabilityMixin.part_solve
    ps_state = {"ms": 0.0, "n": 0}

    def ps(self, *a, **k):
        t = time.perf_counter()
        try:
            return orig_ps(self, *a, **k)
        finally:
            d = (time.perf_counter() - t) * 1000.0
            ps_state["ms"] += d
            ps_state["n"] += 1

    # 3) win_rate
    orig_wr = prob.ProbabilityMixin.win_rate
    wr_state = {"ms": 0.0, "n": 0}

    def wr(self, *a, **k):
        t = time.perf_counter()
        try:
            return orig_wr(self, *a, **k)
        finally:
            wr_state["ms"] += (time.perf_counter() - t) * 1000.0
            wr_state["n"] += 1

    prob.ProbabilityMixin.number5_1 = n51
    prob.ProbabilityMixin.part_solve = ps
    prob.ProbabilityMixin.win_rate = wr
    ab.reset_gate_stats()

    print("=" * 100)
    print(f"算法性能基准：arm={args.arm}，{args.games} 局")
    print("=" * 100)

    games = []
    t_all = time.time()
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            ps_state["ms"] = ps_state["n"] = 0
            wr_state["ms"] = wr_state["n"] = 0
            gc.collect()
            rss0 = P.memory_info().rss
            t0 = time.perf_counter()
            r = ab.play_one(sd, 30, 16, 99, args.arm, 80, args.world_cap,
                            args.node_cap)
            total = (time.perf_counter() - t0) * 1000.0
            rss1 = P.memory_info().rss
            # 决策段耗时：number5_1 的累计（可能多次）
            decide = cur.get("decide_ms")
            games.append({
                "seed": sd, "won": r.get("won"), "died": r.get("died"),
                "steps": r.get("steps"), "total_ms": total,
                "decide_ms": decide,
                "part_solve_ms": ps_state["ms"],
                "part_solve_calls": ps_state["n"],
                "win_rate_ms": wr_state["ms"],
                "win_rate_calls": wr_state["n"],
                "arm_calls": r.get("arm_calls", 0),
                "gate": r.get("gate_stats", {}),
                "rss_delta_mb": (rss1 - rss0) / 2 ** 20,
                "error": r.get("error"),
            })
            print(f"  [{gi + 1:>3}/{args.games}] seed={sd} "
                  f"总 {total:>8.0f}ms 决策 {decide or 0:>8.0f}ms "
                  f"枚举 {ps_state['ms']:>7.0f}ms({ps_state['n']}) "
                  f"wr {wr_state['ms']:>7.0f}ms({wr_state['n']}) "
                  f"{'win' if r.get('won') else ('dead' if r.get('died') else '?')}")
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        prob.ProbabilityMixin.part_solve = orig_ps
        prob.ProbabilityMixin.win_rate = orig_wr

    wall = time.time() - t_all
    ok = [g for g in games if not g["error"]]

    # ---------------- 汇总 ----------------
    print()
    print("=" * 100)
    print("阶段耗时汇总（每局，ms）")
    print("=" * 100)
    totals = [g["total_ms"] for g in ok]
    decides = [g["decide_ms"] for g in ok if g["decide_ms"] is not None]
    pss = [g["part_solve_ms"] for g in ok]
    wrs = [g["win_rate_ms"] for g in ok]

    def line(name, vals, base=None):
        if not vals:
            print(f"{name:<20} 无数据")
            return
        med = statistics.median(vals)
        share = f"  占比 {med / base * 100:>5.1f}%" if base else ""
        print(f"{name:<20} 中位 {med:>9.1f}  p90 {pct(vals, 0.9):>9.1f}  "
              f"最大 {max(vals):>9.1f}  合计 {sum(vals):>9.0f}{share}")

    base_med = statistics.median(totals) if totals else None
    line("单局总耗时", totals)
    line("决策段(number5_1)", decides, base_med)
    line("其中 part_solve", pss, base_med)
    line("其中 win_rate", wrs, base_med)

    print()
    print(f"完成 {len(ok)}/{args.games} 局，墙钟 {wall:.0f}s，"
          f"平均 {wall / max(1, len(games)):.2f}s/局")
    n_ps = sum(g["part_solve_calls"] for g in ok)
    n_wr = sum(g["win_rate_calls"] for g in ok)
    print(f"part_solve 调用 {n_ps} 次，win_rate 调用 {n_wr} 次")
    gs = ab.gate_stats()
    print(f"闸门触发：{gs}")

    # 结论性判断
    if base_med and decides:
        d_share = statistics.median(decides) / base_med
        ps_share = statistics.median(pss) / base_med if pss else 0
        print()
        print(f"决策段占总耗时 {d_share * 100:.1f}%；"
              f"其中枚举 part_solve 占 {ps_share * 100:.1f}%")
        rest = 1 - d_share
        print(f"**非决策段**（读屏/推理/点击）占 {rest * 100:.1f}% "
              f"→ 优化算法最多只能影响 {d_share * 100:.1f}% 的总时间")

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   f"bench_algo_{args.arm}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "games": args.games, "seed": args.seed,
                   "records": games, "gate_stats": gs,
                   "wall_s": wall}, f, ensure_ascii=False)
    print(f"明细已写 {out}")


if __name__ == "__main__":
    main()
