# -*- coding: utf-8 -*-
"""关键对照：用户方案里的「启发式破平局」vs「浅层搜索破平局」——哪个真的有信号？

=================== 为什么必须做这个对照 ====================
bench/eval_heuristic_metrics.py 在 32 个可精确求值的中局决策点上实测：
  · 生产贪心 S0（选 safety 最高的格）平均 regret = 0.0157
  · **只在 safety 平局集内**做选择的 oracle，平均 regret = 0.0032
  ⇒ 贪心 80% 的损失落在平局集**内部**，也就是"选哪个都不更危险，但价值不同"。

这正好是用户方案的核心主张：用启发式破平局。所以真正要回答的不是
"8 个指标各自好不好"，而是：
  Q1 手工启发式破平局，能不能把 0.0157 拉向 0.0032？
  Q2 如果能，是不是换成 2-ply/3-ply 浅层搜索会更好？
  Q3 代价差多少（毫秒口径）？

=================== 口径（严格，防上一轮的错误）====================
  · 样本 = 世界数可枚举的中局决策点（能算忠实 V*），抛 Budget 的整点丢弃
  · 真值 after_click(c) = safety(c) × E[完美续玩胜率]（含 0 连锁展开）
  · regret = v_opt − after_click(选中格)
  · 所有臂在**同一个决策点的同一个候选集**上比较（配对）
  · 「破平局」臂 = 先取 safety 最大的集合 T，再在 T 内按该准则取 max

用法:
  python bench/eval_search_vs_heuristics.py --max-worlds 120 --time-cap 15
"""
import argparse
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = sum(sum(int(v) for v in case["groups"][gi][sel])
                 for gi, sel in enumerate(idx))
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def sign_test(w, l):
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=60.0)
    ap.add_argument("--max-worlds", type=int, default=120)
    ap.add_argument("--node-cap", type=int, default=3_000_000)
    ap.add_argument("--time-cap", type=float, default=15.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "search_vs_heuristics.json"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]

    print("=" * 104)
    print("浅层搜索 vs 手工启发式（都在「safety 平局集」内破平局，真值口径）")
    print(f"limitation ∈ ({args.min_lim}, {args.max_lim}]，世界数 ≤ {args.max_worlds:,}")
    print("=" * 104, flush=True)

    # 臂名 -> (是否只在平局集内选, 准则)
    ARMS = ("S0_greedy", "tie_IG", "tie_U", "tie_E", "tie_L",
            "tie_B", "tie_R",
            "tie_2ply", "tie_3ply", "all_2ply", "all_3ply")
    TIE_ARMS = {"tie_IG": "IG", "tie_U": "U", "tie_E": "E", "tie_L": "L",
                "tie_B": "B", "tie_R": "R",
                "tie_2ply": 2, "tie_3ply": 3}

    res = {a: [] for a in ARMS}
    cost = {a: [] for a in ARMS}
    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0}

    for c in sample:
        nw = count_worlds(c)
        if nw > args.max_worlds:
            skipped["world_cap"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                              world_cap=None)
        except Exception:                                  # noqa: BLE001
            skipped["err"] += 1
            continue
        if eng.n_worlds == 0:
            skipped["err"] += 1
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk_all, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            skipped["err"] += 1
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        w, h = int(c["w"]), int(c["h"])

        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            skipped["budget"] += 1
            continue
        v_opt = max(q.values())

        saf = {cd: eng.safety(cd, wk2) for cd in cands}
        saf_max = max(saf.values())
        tie = [cd for cd in cands if saf[cd] >= saf_max - 1e-12]

        def pick_max(scope, score):
            best = max(score[cd] for cd in scope)
            return min(cd for cd in scope if score[cd] >= best - 1e-12)

        picks = {}
        tms = {}
        t0 = time.perf_counter()
        picks["S0_greedy"] = pick_max(cands, saf)
        tms["S0_greedy"] = (time.perf_counter() - t0) * 1000.0

        # ---- 启发式破平局 ----
        t0 = time.perf_counter()
        IG = {}
        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []
            # 信息增益代理：safety(c) × E[点开后新开的格数]
            IG[cd] = (0.0 if p_safe <= 0 else
                      p_safe * sum(wt * eng.pop(op3 & ~op2)
                                   for _s, op3, wt in outs))
        tms["tie_IG"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_IG"] = pick_max(tie, IG)

        t0 = time.perf_counter()
        E = {}
        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []
            if p_safe <= 0:
                E[cd] = 0.0
                continue
            acc = 0.0
            for sub, op3, wt in outs:
                _u, inter = eng._union_inter(sub)
                acc += wt * eng.pop(eng.all_mask & ~op3 & ~inter)
            E[cd] = p_safe * acc
        tms["tie_E"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_E"] = pick_max(tie, E)

        t0 = time.perf_counter()
        U = {}
        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []
            if p_safe <= 0:
                U[cd] = 0.0
                continue
            worst = 1.0
            for sub, op3, wt in outs:
                c3 = eng.candidates(sub, op3)
                if c3:
                    worst = min(worst, min(eng.safety(x, sub) for x in c3))
            U[cd] = p_safe * worst
        tms["tie_U"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_U"] = pick_max(tie, U)

        t0 = time.perf_counter()
        L = {cd: bdist(w, h, eng._coord(cd)) for cd in cands}
        tms["tie_L"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_L"] = pick_max(tie, L)

        # ---- B：约束块强度 / 边界收益 ----
        # 工程定义：点开 cd（假设安全）后，**约束块的切碎程度**。
        # 本仓快照没有显式连通块，用可计算代理：
        #   B(c) = E[点开后仍不确定的格数] 的倒数 × 分支数
        # 直觉：分支越多 = 观测越丰富 = 后续约束越紧；仍不确定格越少 = 块越碎。
        t0 = time.perf_counter()
        B = {}
        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []
            if p_safe <= 0 or not outs:
                B[cd] = 0.0
                continue
            acc = 0.0
            for sub, op3, wt in outs:
                c3 = eng.candidates(sub, op3)
                if not c3:
                    continue
                # 分支内候选格的 safety 离散度：越分散 ⇒ 约束越松（不好）
                sv = [eng.safety(x, sub) for x in c3]
                spread = max(sv) - min(sv)
                acc += wt * len(outs) / (1.0 + spread * 10.0)
            B[cd] = p_safe * acc
        tms["tie_B"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_B"] = pick_max(tie, B)

        # ---- R：尾部风险 / 后续强制猜测次数 ----
        # 工程定义：E[点开 cd 后，仍有"最安全格 safety 也低"的约束块数量]。
        # 越小越好 ⇒ 取负分（pick_max 里用 -R）。
        t0 = time.perf_counter()
        R = {}
        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []
            if p_safe <= 0 or not outs:
                R[cd] = 1e9
                continue
            acc = 0.0
            for sub, op3, wt in outs:
                c3 = eng.candidates(sub, op3)
                if not c3:
                    continue
                best_next = max(eng.safety(x, sub) for x in c3)
                acc += wt * (1.0 - best_next)      # 仍需"被迫猜测"的风险
            R[cd] = acc
        tms["tie_R"] = (time.perf_counter() - t0) * 1000.0
        picks["tie_R"] = pick_max(tie, {cd: -R[cd] for cd in cands})

        # ---- 浅层搜索破平局 ----
        for k in (2, 3):
            t0 = time.perf_counter()
            try:
                memo = {}
                P = {cd: eng.ply_score(cd, wk2, op2, k, memo) for cd in cands}
                tms[f"tie_{k}ply"] = (time.perf_counter() - t0) * 1000.0
                picks[f"tie_{k}ply"] = pick_max(tie, P)
                picks[f"all_{k}ply"] = pick_max(cands, P)
            except Budget:
                tms[f"tie_{k}ply"] = (time.perf_counter() - t0) * 1000.0
                picks[f"tie_{k}ply"] = min(tie)
                picks[f"all_{k}ply"] = min(cands)
        # all_*ply 与 tie_*ply 复用同一份 P，计时应一致（不是 0）
        for k in (2, 3):
            tms[f"all_{k}ply"] = tms[f"tie_{k}ply"]

        row = {"limitation": c["limitation"], "worlds": nw, "n_cand": len(cands),
               "n_tie": len(tie), "v_opt": round(v_opt, 6),
               "ceiling_regret": round(v_opt - max(q[cd] for cd in tie), 6)}
        for a in ARMS:
            cd = picks[a]
            reg = v_opt - q[cd]
            row[a] = {"regret": round(reg, 6),
                      "is_opt": bool(reg <= 1e-12),
                      "ms": round(tms.get(a, 0.0), 3)}
            res[a].append(reg)
            cost[a].append(tms.get(a, 0.0))
        rows.append(row)

    print("\n" + "=" * 104)
    print(f"可覆盖 {len(rows)}/{len(sample)}（{skipped}）")
    print("=" * 104)
    if not rows:
        return

    print(f"\n{'臂':<14}{'全对':>9}{'平均regret':>13}{'最大':>10}{'总损失':>10}"
          f"{'中位ms':>10}{'p90ms':>10}")
    print("-" * 104)
    for a in ARMS:
        v = res[a]
        ok = sum(1 for x in v if x <= 1e-12)
        cs_ = sorted(cost[a])
        med = cs_[len(cs_) // 2]
        p90 = cs_[min(len(cs_) - 1, int(len(cs_) * 0.9))]
        print(f"{a:<14}{f'{ok}/{len(v)}':>9}{sum(v)/len(v):>13.4f}"
              f"{max(v):>10.4f}{sum(v):>10.3f}{med:>10.2f}{p90:>10.2f}")

    print("\n" + "=" * 104)
    print("配对符号检验（vs S0_greedy；负 Δ = 该臂更好）")
    print("=" * 104)
    print(f"{'臂 vs S0':<24}{'更好':>7}{'更差':>7}{'n':>5}{'p':>9}{'平均Δ':>11}")
    print("-" * 104)
    for a in ARMS:
        if a == "S0_greedy":
            continue
        d = [r[a]["regret"] - r["S0_greedy"]["regret"] for r in rows]
        wn = sum(1 for x in d if x < -1e-12)
        ls = sum(1 for x in d if x > 1e-12)
        p = sign_test(wn, ls)
        print(f"{a + ' vs S0':<24}{wn:>7}{ls:>7}{wn+ls:>5}{p:>9.3f}"
              f"{sum(d)/len(d):>+11.4f}  {'显著' if p < 0.05 else ''}")

    print("\n" + "=" * 104)
    print("启发式 vs 浅层搜索（同为破平局臂，配对）")
    print("=" * 104)
    for h in ("tie_IG", "tie_U", "tie_E", "tie_L", "tie_B", "tie_R"):
        for s in ("tie_2ply", "tie_3ply"):
            d = [r[s]["regret"] - r[h]["regret"] for r in rows]
            wn = sum(1 for x in d if x < -1e-12)
            ls = sum(1 for x in d if x > 1e-12)
            p = sign_test(wn, ls)
            print(f"  {s} 优于 {h}: {wn} 次更好 / {ls} 次更差 / n={wn+ls} "
                  f"p={p:.3f} 平均Δ={sum(d)/len(d):+.4f} "
                  f"{'显著' if p < 0.05 else ''}")

    print("\n" + "=" * 104)
    print("指标 G（全局雷数一致性权重）—— 可识别性检验")
    print("=" * 104)
    print("  用户定义 G(c) = 与全局剩余雷数最一致的方案权重 / 总方案权重。")
    print("  本引擎的世界枚举**在构造上**只保留满足全局雷数的世界")
    print("  （rem = a − num10 − Σm 必须落在 [0, U]，权重含 C(U, rem)），")
    print("  所以 engine.safety(c) 与「按全局雷数加权的 P(安全)」是**同一个数**。")
    print("  ⇒ G 不可能独立于 S0 提供新信息；把它放进 prior 只会与 S0 完全共线。")
    print("  （数值核验见 bench/verify_global_count_equiv.py，独立抽样逐点比对）")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "skipped": skipped, "args": vars(args)},
                  f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
