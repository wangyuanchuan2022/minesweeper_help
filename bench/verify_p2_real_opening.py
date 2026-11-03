# -*- coding: utf-8 -*-
"""P2：在真实受约束开局上复核 P0.5（可完整枚举的样本）。

=================== 与上一轮的区别 ====================
上一轮（bench/verify_p05_symmetric_lookahead.py）用的是 **6x6/4雷/前沿无约束**
的极端构造，只能说明"对称格的续玩估值并非处处严格相等"，不能外推到真实大 U。

本轮要求（按评审意见）：
  1. 用**真实受约束开局**样本：前沿格受数字约束（不是全 0 解）
  2. 只选**可完整枚举**的样本（世界数 ≤ world_cap，能算精确 V*）
  3. **固定平局选择规则**（否则决策差异可能来自随机）
  4. 配对比较：原前瞻 vs 闭式 safety + 贪心
     · 决策是否相同
     · 单步胜率差（用精确 V* 衡量该决策的质量）
     · 耗时
  5. 跨种子看是否稳定 —— 只有稳定且值得成本的增益才启动 P1

=================== 样本来源 ====================
从 bench/data/wr_cases_big.json（33 个生产真实决策点）里筛：
  · 世界数 ≤ --world-cap（可完整枚举）
  · 内部格 U ≥ --min-u（确保有"对称内部格"可比较）
  · 同时存在前沿格与内部格

用法: python bench/verify_p2_real_opening.py
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

import numpy as np                       # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def pick_tiebreak(cands, scores, rule="lowest_cell"):
    """固定平局选择规则，避免随机导致决策差异。

    rule='lowest_cell'：分数最高者中取坐标最小（字典序）的格。
    """
    best = max(scores.values())
    tied = [c for c in cands if abs(scores[c] - best) < 1e-12]
    return min(tied) if rule == "lowest_cell" else tied[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(ROOT, "bench", "data",
                                                   "wr_cases_big.json"))
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--min-u", type=int, default=8)
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--time-cap", type=float, default=120.0)
    args = ap.parse_args()

    from arms_lookahead import ExactEngine, WKey, Budget

    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 100)
    print("P2：真实受约束开局上复核 P0.5")
    print("=" * 100)
    print(f"筛选条件：世界数 ≤ {args.world_cap:,}，内部格 U ≥ {args.min_u}")

    # ---------------- 筛选样本 ----------------
    samples = []
    for ci, c in enumerate(cases):
        U = len(c["clicks9"])
        F = len(c["clicks"])
        if U < args.min_u or F == 0:
            continue
        if c["total"] > args.world_cap:
            continue
        samples.append((ci, c, F, U))

    print(f"\n筛出 {len(samples)} 个可完整枚举的真实开局样本：")
    for ci, c, F, U in samples:
        opened = c["w"] * c["h"] - F - U
        print(f"  [{ci:>2}] F={F:>2} U={U:>2} 世界数={c['total']:>6} "
              f"已开={opened:>3} limitation≈"
              f"{U * 0.8 + np.log2(max(c['total'], 1)):.1f}")

    if not samples:
        # ⚠️ 这**只是样本观察，不是结构性规律**。
        # 世界数 = Σ C(U, R-前沿雷数)，由 U、剩余雷数 R、约束结构共同决定，
        # **不是 U 的单调函数**。反例：U=471, R=1 时只有 471 个世界（可枚举）；
        # U=471, R=0 时仅 1 个。因此不能断言"大 U 必然不可枚举"。
        # 正确表述：当前样本中未观察到"大 U 且世界数可控"的局面。
        print("\n当前样本中无满足条件的局面 —— 这只是**样本观察**：")
        print("  当前 33 个真实样本里未观察到「大 U 且世界数可控」的局面。")
        print("  注意：世界数由 U、剩余雷数 R、约束结构共同决定（不是 U 的单调函数），")
        print("        例如 U=471,R=1 只有 471 个世界，完全可枚举。")
        print("  若要继续：应按 U × R × 世界数 三维分层扩充采集，")
        print("            不能用 U≥4 的少量样本代表大 U。")
        return

    # ---------------- 逐样本配对比较 ----------------
    rows = []
    for ci, c, F, U in samples:
        try:
            eng = ExactEngine(c, node_cap=args.node_cap,
                              time_cap=args.time_cap,
                              world_cap=args.world_cap)
        except Exception as e:                         # noqa: BLE001
            print(f"\n[{ci}] 构造失败 {type(e).__name__}: {e}")
            continue
        if eng.n_worlds == 0:
            continue

        wk = WKey.all(eng.n_worlds)
        op = eng.opened0
        cands = eng.candidates(wk, op)
        if len(cands) < 2:
            continue

        # ---- 计算每个候选的 safety 与精确续玩价值 ----
        saf = {}
        val = {}
        incomplete = False
        for cd in cands:
            saf[cd] = eng.safety(cd, wk)
            try:
                val[cd] = eng.after_click(cd, wk, op)
            except Budget:
                incomplete = True
                break
        if incomplete:
            print(f"\n[{ci}] 决策点未覆盖（Budget），跳过"
                  f"（不纳入统计，避免按是否易算筛选）")
            continue

        v_opt = max(val.values())

        # ---- 臂 A：原前瞻（用精确续玩价值选格）----
        t0 = time.perf_counter()
        pick_la = pick_tiebreak(cands, val)
        t_la = (time.perf_counter() - t0) * 1000

        # ---- 臂 B：闭式 safety + 贪心（只看 safety）----
        t0 = time.perf_counter()
        pick_gr = pick_tiebreak(cands, saf)
        t_gr = (time.perf_counter() - t0) * 1000

        # 注：闭式 safety 的计算成本在 ExactEngine 里已包含（safety() 用位运算），
        # 真正的"闭式"在生产里是 _pbs_compute_dp，这里只比较**选格准则**的差异。

        rows.append({
            "ci": ci, "F": F, "U": U, "worlds": eng.n_worlds,
            "n_cand": len(cands),
            "pick_la": eng._coord(pick_la),
            "pick_gr": eng._coord(pick_gr),
            "val_la": val[pick_la], "val_gr": val[pick_gr],
            "v_opt": v_opt,
            "same": pick_la == pick_gr,
            "regret_la": v_opt - val[pick_la],
            "regret_gr": v_opt - val[pick_gr],
            "t_la_ms": t_la, "t_gr_ms": t_gr,
        })
        print(f"\n[{ci}] F={F} U={U} 世界={eng.n_worlds} 候选={len(cands)}")
        print(f"     原前瞻 选 {rows[-1]['pick_la']} 价值={val[pick_la]:.6f}")
        print(f"     贪心   选 {rows[-1]['pick_gr']} 价值={val[pick_gr]:.6f}")
        print(f"     V*={v_opt:.6f}  决策{'相同' if rows[-1]['same'] else '**不同**'}")

    if not rows:
        print("\n无有效样本")
        return

    # ---------------- 汇总 ----------------
    print()
    print("=" * 100)
    print("配对比较汇总")
    print("=" * 100)
    n = len(rows)
    n_same = sum(1 for r in rows if r["same"])
    reg_la = [r["regret_la"] for r in rows]
    reg_gr = [r["regret_gr"] for r in rows]
    d = [r["regret_gr"] - r["regret_la"] for r in rows]   # >0 表示前瞻更好

    print(f"样本数 {n}，决策相同 {n_same}/{n} "
          f"（{n_same / n * 100:.1f}%）")
    print(f"原前瞻 regret：均值 {statistics.mean(reg_la):.6f}，"
          f"最大 {max(reg_la):.6f}")
    print(f"贪心   regret：均值 {statistics.mean(reg_gr):.6f}，"
          f"最大 {max(reg_gr):.6f}")
    print(f"逐样本差（贪心 regret - 前瞻 regret，>0 表示前瞻更好）：")
    for r in rows:
        if abs(r["regret_gr"] - r["regret_la"]) > 1e-12:
            print(f"   [{r['ci']:>2}] 前瞻 {r['regret_la']:.6f} vs "
                  f"贪心 {r['regret_gr']:.6f}  "
                  f"差 {r['regret_gr'] - r['regret_la']:+.6f}")

    better = sum(1 for x in d if x > 1e-12)
    worse = sum(1 for x in d if x < -1e-12)
    print(f"\n前瞻更好 {better} 例，贪心更好 {worse} 例，"
          f"持平 {n - better - worse} 例")

    # ---------------- 结论 ----------------
    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    mean_gain = statistics.mean(d) if d else 0.0
    print(f"平均单步增益（前瞻相对贪心）= {mean_gain:+.6f}")
    if better == 0:
        print("⇒ 在本批真实可枚举样本上，**前瞻未产生任何增益**。")
        print("  结合上轮 6x6 极端构造的 0.5% 幅度，")
        print("  不足以支撑启动 P1（前瞻算法设计）。")
        print("  建议：将「bench 前瞻臂在目标大 U 局面（U=471,R=99）不可用」")
        print("        保留为明确边界 —— 依据是**资源能力**（世界数 6.87e103")
        print("        超出精确枚举能力），不是数学必然性。")
    elif mean_gain > 1e-4:
        print("⇒ 前瞻有正向增益，但仍需跨更多种子确认稳定性。")
        print("  ⚠️ 本批样本 U 仅 4-7，不能代表大 U 局面；")
        print("     后续应按 U × R × 世界数 三维分层扩充采样。")
    else:
        print("⇒ 增益极小（<1e-4），不值得为此新增算法。")

    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")

    out = os.path.join(ROOT, "state", "ab", "p2_real_opening.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "min_u": args.min_u,
                   "world_cap": args.world_cap}, f, ensure_ascii=False)
    print(f"明细已写 {out}")


if __name__ == "__main__":
    main()
