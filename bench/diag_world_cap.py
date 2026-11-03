# -*- coding: utf-8 -*-
"""拆解 world_cap 触发场景：按阶段/规模分层，判断该做专用还是通用方案。

=================== 要回答的问题 ====================
"89% 的局发生降级" ≠ "89% 的决策都走贪心"。必须拆开看：
  · 每局降级决策占比是多少（1/20 还是 20/20）
  · 降级发生在开局 / 中盘 / 尾盘
  · 每个降级决策点的规模（F/U/世界数/候选数）
  · 各场景的耗时

=================== 为什么这决定方案选型 ====================
  · 若降级几乎全在**开局大 U**（如 U=471, num10=0）：
    这类局面有强对称性 —— 所有内部格在均匀先验下等价，
    只需算"每格边际雷概率"= (a-num10)/U，**不需要枚举世界**。
    ⇒ 专用快捷路径即可，成本 O(1)，无需通用近似框架。
  · 若**中盘复杂局面**也占显著成本：
    没有对称性可用，才需要分层采样/局部枚举等通用近似。

用法: python bench/diag_world_cap.py --games 40 --arm safety
"""
import argparse
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

import time as _time_mod                      # noqa: E402
_REAL_SLEEP = _time_mod.sleep


def classify_phase(case, w=30, h=16):
    """按已开格比例划分阶段。"""
    opened = w * h - len(case["clicks"]) - len(case["clicks9"])
    frac = opened / (w * h)
    if frac < 0.15:
        return "开局"
    if frac < 0.60:
        return "中盘"
    return "尾盘"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--arm", default="safety")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "diagcap"), True)

    import ab_policy as ab
    from compare_2ply_vs_winrate import (count_worlds_exact,
                                         validate_case_or_raise)

    # 拦截 decide_with_arm，记录每个决策点是否降级、以及其规模
    records = []
    orig = ab.decide_with_arm

    def probe(arm, case, world_cap, node_cap):
        rec = {
            "F": case["F"], "U": case["U"], "num10": case["num10"],
            "total": case["total"], "phase": classify_phase(case),
            "n_cand": 1 + case["F"] + case["U"],
        }
        try:
            validate_case_or_raise(case)
            nw = count_worlds_exact(case, cap=world_cap)
            rec["worlds"] = nw
            rec["would_degrade"] = nw > world_cap
        except ValueError as e:
            rec["worlds"] = None
            rec["would_degrade"] = True
            rec["invalid"] = str(e)[:60]
        records.append(rec)
        # 直接回退，不真跑（避免耗时与内存）
        raise ab.PolicyFallback("diag-only")

    ab.decide_with_arm = probe
    games = []
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            n0 = len(records)
            t0 = time.perf_counter()
            try:
                r = ab.play_one(sd, 30, 16, 99, args.arm, 80,
                                args.world_cap, args.node_cap)
                err = r.get("error")
                won = r.get("won")
            except Exception as e:                     # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                won = None
            dt = time.perf_counter() - t0
            grp = records[n0:]
            games.append({"seed": sd, "won": won, "n_decisions": len(grp),
                          "n_degrade": sum(1 for x in grp
                                           if x["would_degrade"]),
                          "ms": dt * 1000, "error": err})
            print(f"  [{gi + 1}/{args.games}] seed={sd} "
                  f"决策 {len(grp):>2} 降级 "
                  f"{sum(1 for x in grp if x['would_degrade']):>2} "
                  f"{'win' if won else ('dead' if won is False else '?')}",
                  flush=True)
    finally:
        ab.decide_with_arm = orig

    # ---------------- 拆解 ----------------
    print()
    print("=" * 96)
    print(f"world_cap 触发场景拆解（arm={args.arm}，{args.games} 局）")
    print("=" * 96)

    tot_dec = len(records)
    tot_deg = sum(1 for r in records if r["would_degrade"])
    n_deg_games = sum(1 for g in games if g["n_degrade"] > 0)

    print(f"总决策点 {tot_dec}，其中会降级 {tot_deg} "
          f"（{tot_deg / max(1, tot_dec) * 100:.1f}%）")
    print(f"发生降级的局 {n_deg_games}/{len(games)} "
          f"（{n_deg_games / max(1, len(games)) * 100:.1f}%）")
    if games:
        per_game = [g["n_degrade"] / max(1, g["n_decisions"]) for g in games
                    if g["n_decisions"]]
        if per_game:
            print(f"每局降级决策占比：中位 {statistics.median(per_game) * 100:.1f}%"
                  f"  最大 {max(per_game) * 100:.1f}%")

    # 按阶段
    print()
    print("按阶段分布：")
    print(f"{'阶段':<8} {'决策数':>8} {'降级数':>8} {'降级占比':>10}")
    print("-" * 96)
    for ph in ("开局", "中盘", "尾盘"):
        sub = [r for r in records if r["phase"] == ph]
        if not sub:
            continue
        d = sum(1 for r in sub if r["would_degrade"])
        print(f"{ph:<8} {len(sub):>8} {d:>8} {d / len(sub) * 100:>9.1f}%")

    # 按 U 规模
    print()
    print("按内部未开格 U 分层：")
    print(f"{'U 区间':<12} {'决策数':>8} {'降级数':>8} {'降级占比':>10} "
          f"{'典型 worlds':>14}")
    print("-" * 96)
    buckets = [(0, 10), (11, 30), (31, 60), (61, 200), (201, 10 ** 6)]
    for lo, hi in buckets:
        sub = [r for r in records if lo <= r["U"] <= hi]
        if not sub:
            continue
        d = [r for r in sub if r["would_degrade"]]
        ws = [r["worlds"] for r in d if r.get("worlds")]
        wdesc = f"{max(ws):,}" if ws else "-"
        print(f"{f'{lo}-{hi}':<12} {len(sub):>8} {len(d):>8} "
              f"{len(d) / len(sub) * 100:>9.1f}% {wdesc:>14}")

    # 关键判断：降级是否集中在"开局大 U"
    print()
    print("=" * 96)
    print("方案选型判断")
    print("=" * 96)
    deg_recs = [r for r in records if r["would_degrade"]]
    if not deg_recs:
        print("无降级样本，无法判断")
        return

    opening_big = [r for r in deg_recs
                   if r["phase"] == "开局" and r["U"] > 100]
    mid_deg = [r for r in deg_recs if r["phase"] == "中盘"]
    print(f"降级中「开局且 U>100」: {len(opening_big)}/{len(deg_recs)} "
          f"（{len(opening_big) / len(deg_recs) * 100:.0f}%）")
    print(f"降级中「中盘」        : {len(mid_deg)}/{len(deg_recs)} "
          f"（{len(mid_deg) / len(deg_recs) * 100:.0f}%）")
    print()
    if len(opening_big) / len(deg_recs) > 0.7:
        print("⇒ 降级高度集中在**开局大 U**（对称局面）。")
        print("  这类局面下所有内部格在均匀先验下等价，")
        print("  边际雷概率 = (a - num10) / U，**无需枚举世界**。")
        print("  建议：实现**专用快捷路径**（O(1)），不必上通用近似框架。")
    elif len(mid_deg) / len(deg_recs) > 0.3:
        print("⇒ 中盘复杂局面占比显著，无对称性可用。")
        print("  建议：评估分层采样 / 局部枚举等**通用近似**。")
    else:
        print("⇒ 降级分散，需进一步按规模细分再定。")

    out = os.path.join(ROOT, "state", "ab", "diag_world_cap.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"records": records, "games": games,
                   "arm": args.arm, "games_n": args.games},
                  f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
