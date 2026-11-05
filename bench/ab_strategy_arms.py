# -*- coding: utf-8 -*-
"""策略臂 A/B：生产 win_rate 现状 vs 候选改进（整体局胜率，配对设计）。

=================== 目的 ====================
用户要求"把已验证的改进写入生产算法测量胜率 10000 局作为基线"。
但先读生产代码后发现两件事（必须先说清）：

1. **生产已经在用前瞻作主准则**：
   `utils/probability.py:833-837`
       win_rate = argmax 结果 → np.random.choice 按最大 win_rate 破平局
   而 `cpp/mscore.cpp:911` 的 `wr_f` 是**带深度上限的递归前瞻**。
   ⇒ 我此前用 `argmax(S0)`（纯 safety）作基线，**弱于生产实际**。

2. 因此"相对 S0 的改进 −0.0247" **不能**当作"相对生产的改进"。
   真实对比必须用「生产 win_rate 臂」作为基线臂。

=================== 本脚本做什么 ====================
用 `bench/ab_policy.py` 的 `play_one`（生产流水线，只替换选格臂）做**同 seed 配对**：

    臂 baseline : 生产原样（win_rate 深分支 + 降级贪心）
    臂 ply3     : 用 ExactEngine 的 3-ply 选格（候选改进）
    臂 ply2     : 用 ExactEngine 的 2-ply 选格
    臂 safety   : 纯 safety（仅作参照，证明基线强弱）
    臂 exact    : 用 after_click 完美续玩（上界参照）

⚠️ 关键：`ply3/ply2/safety/exact` 这些臂走的是 `decide_with_arm`，
   会**整体替换** win_rate 的决策位置（见 ab_policy.py:326-391 的 monkey-patch）。
   这正是"把改进写入生产算法"的等价形式，但**不修改生产源文件**。

=================== 预注册判据（看结果前写定）====================
主判据：候选臂 vs baseline 的整体局胜率差，配对 McNemar 双侧 p < 0.05 且 Δ > 0。
· 若 ply3 显著优于 baseline ⇒ 值得投 10000 局确认
· 若无一臂显著优于 baseline ⇒ **改进相对生产无收益**，10000 局基线只需跑 baseline

同时报告：降级/cap 触发比例、单局平均耗时（用于校准 10000 局预算外推）。

用法:
  python bench/ab_strategy_arms.py --arms baseline ply3 --games 300 --seed 20274000
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


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / float(2 ** n)
    return min(1.0, 2 * tail)


def run_arm(arm, games, seed, w, h, a, limit, node_cap, world_cap):
    """跑一个臂，返回逐局结果与统计。"""
    import ab_policy as ab
    ab.reset_gate_stats()
    results = []
    t0 = time.perf_counter()
    fails = 0
    for i in range(games):
        sd = seed + i
        try:
            r = ab.play_one(sd, w, h, a, arm, limit=limit,
                            node_cap=node_cap, world_cap=world_cap)
        except Exception as e:                             # noqa: BLE001
            fails += 1
            results.append({"seed": sd, "won": None,
                            "err": type(e).__name__ + ":" + str(e)[:50]})
            continue
        results.append({"seed": sd, "won": bool(r.get("won")),
                        "steps": r.get("steps")})
        if (i + 1) % 25 == 0:
            won = sum(1 for x in results if x.get("won"))
            print("      %s %d/%d（won=%d，失败=%d，%.0fs）"
                  % (arm, i + 1, games, won, fails,
                     time.perf_counter() - t0), flush=True)
    wall = time.perf_counter() - t0
    gates = ab.gate_stats() if hasattr(ab, "gate_stats") else {}
    return {"arm": arm, "games": games, "results": results,
            "fails": fails, "wall_s": wall, "gates": gates}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="*",
                    default=["baseline", "ply3"],
                    help="要对比的臂；第一个为基线臂")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20274000)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--node-cap", type=int, default=200000)
    ap.add_argument("--world-cap", type=int, default=4096)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    print("=" * 100)
    print("策略臂整局 A/B（同 seed 配对，生产流水线）")
    print("=" * 100)
    print("  臂：%s" % args.arms)
    print("  局数：%d/臂，seed %d+（两臂完全相同）"
          % (args.games, args.seed))
    print("  棋盘 w=%d h=%d a=%d，limit=%d node_cap=%d world_cap=%d"
          % (args.w, args.h, args.a, args.limit,
             args.node_cap, args.world_cap))
    print("  基线臂 = %s（生产原样）" % args.arms[0])

    arms_out = {}
    for arm in args.arms:
        print()
        print("-" * 100)
        print("跑臂：%s" % arm)
        print("-" * 100)
        arms_out[arm] = run_arm(arm, args.games, args.seed,
                               args.w, args.h, args.a, args.limit,
                               args.node_cap, args.world_cap)

    # 汇总
    print()
    print("=" * 100)
    print("结果")
    print("=" * 100)
    base_arm = args.arms[0]
    base = arms_out[base_arm]
    base_won = sum(1 for r in base["results"] if r.get("won"))
    base_n = sum(1 for r in base["results"] if r.get("won") is not None)
    blo, bhi = wilson(base_won, base_n)
    print("  %-10s %6s/%5s = %6.2f%%  95%%CI [%.2f, %.2f]  失败 %d  %.0fs"
          % (base_arm, base_won, base_n, 100 * base_won / max(1, base_n),
             100 * blo, 100 * bhi, base["fails"], base["wall_s"]))

    summaries = {base_arm: {"won": base_won, "n": base_n,
                            "rate": base_won / max(1, base_n),
                            "ci95": [blo, bhi],
                            "fails": base["fails"],
                            "wall_s": base["wall_s"],
                            "gates": base["gates"]}}

    for arm in args.arms[1:]:
        a_ = arms_out[arm]
        won = sum(1 for r in a_["results"] if r.get("won"))
        n = sum(1 for r in a_["results"] if r.get("won") is not None)
        lo, hi = wilson(won, n)
        # 配对
        bmap = {r["seed"]: r.get("won") for r in base["results"]}
        amap = {r["seed"]: r.get("won") for r in a_["results"]}
        seeds = [s for s in bmap if bmap[s] is not None and amap.get(s) is not None]
        only_a = sum(1 for s in seeds if amap[s] and not bmap[s])
        only_b = sum(1 for s in seeds if bmap[s] and not amap[s])
        p = mcnemar_exact(only_a, only_b)
        delta = (won / max(1, n) - base_won / max(1, base_n)) * 100
        print("  %-10s %6d/%5d = %6.2f%%  95%%CI [%.2f, %.2f]  失败 %d  %.0fs"
              % (arm, won, n, 100 * won / max(1, n), 100 * lo, 100 * hi,
                 a_["fails"], a_["wall_s"]))
        print("            配对：仅本臂赢 %d / 仅基线赢 %d，McNemar 双侧 p = %.4f，Δ = %+.2fpp"
              % (only_a, only_b, p, delta))
        summaries[arm] = {"won": won, "n": n, "rate": won / max(1, n),
                          "ci95": [lo, hi], "delta_pp": delta,
                          "paired_only_arm": only_a,
                          "paired_only_base": only_b, "mcnemar_p": p,
                          "fails": a_["fails"], "wall_s": a_["wall_s"],
                          "gates": a_["gates"]}

    # 判据
    print()
    print("=" * 100)
    print("预注册判据：候选臂 vs 基线，McNemar p<0.05 且 Δ>0")
    print("=" * 100)
    any_pass = False
    for arm in args.arms[1:]:
        s = summaries[arm]
        ok = (s["delta_pp"] > 0) and (s["mcnemar_p"] < 0.05)
        if ok:
            any_pass = True
        print("  %-10s Δ=%+.2fpp  p=%.4f  ⇒ %s"
              % (arm, s["delta_pp"], s["mcnemar_p"],
                 "**通过**" if ok else "未通过"))
    print()
    if any_pass:
        print("  ⇒ 存在显著优于生产基线的臂 ⇒ 值得扩大样本（如 10000 局）确认。")
    else:
        print("  ⇒ 无臂显著优于生产基线 ⇒ 「前瞻作主准则」相对生产**无额外收益**")
        print("     （因为生产已经用 win_rate 前瞻作主准则了）")
        print("     ⇒ 10000 局基线只需跑 baseline，不必加改进臂。")

    # 耗时外推
    print()
    print("-" * 100)
    print("吞吐与 10000 局预算校准")
    print("-" * 100)
    for arm in args.arms:
        s = summaries[arm]
        per_game = s["wall_s"] / max(1, s["n"])
        print("  %-10s %.2f 秒/局 ⇒ 10000 局约 %.1f 小时"
              % (arm, per_game, per_game * 10000 / 3600))

    out = {
        "arms": args.arms, "games_per_arm": args.games,
        "seed": args.seed,
        "board": {"w": args.w, "h": args.h, "a": args.a},
        "solver": {"limit": args.limit, "node_cap": args.node_cap,
                   "world_cap": args.world_cap},
        "summaries": summaries,
        "raw": {a: {"results": arms_out[a]["results"]} for a in args.arms},
        "args": vars(args),
    }
    op = args.out or os.path.join(
        ROOT, "state", "ab",
        "strategy_arms_%s.json" % "_".join(args.arms))
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
