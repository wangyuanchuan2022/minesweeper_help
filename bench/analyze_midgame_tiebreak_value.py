# -*- coding: utf-8 -*-
"""问题 2 的核心：**在同一个安全率平局集内**，换破平局准则能值多少胜率。

=================== 为什么这是"优化贪心"的正确切入点 ====================
实测（bench/analyze_tiebreak_room.py，400 个真实中局决策点）：
  · limitation (8,20]：59.7% 的决策存在安全率平局
  · (20,60]：45.8%
  · (60,inf]：82.9%，且平局集中位 **463 格**（内部格安全率恒等）
  · 生产破平后仍在 ≥2 格里**均匀随机选**：81.8% 的 (60,inf] 决策
⇒ 贪心的"深度"不是主要缺口；**破平局准则**才是。而破平局是
    "在安全率完全相同的格里挑一个" —— 换准则**不改安全率、不改成本量级**，
    是低风险可 A/B 的改动。

=================== 本脚本度量什么 ====================
只取**世界数可枚举**的中局决策点（能算忠实 V*），然后：
  1. 算出精确每格 after_click，v_opt = max
  2. 平局集 T = argmax safety
  3. 对每个破平局准则 f: T → 一格，度量 regret = v_opt − after(f(T))
  4. 同时报告 **oracle 在 T 内的 regret**（= v_opt − max_{c∈T} after(c)）
     —— 这一项是"破平局天花板"：任何只靠 T 内选格的规则的上界。

=================== 关键：天花板有多高 ====================
若 `v_opt − max_{c∈T} after(c)` 恒为 0，说明最优格一定在平局集里
⇒ 破平局**理论上够用**，改进破平局就能逼近最优（这是最重要的判断）。
若不为 0，说明还得看安全率之外的信息（即真的需要前瞻/新信源）。

用法: python bench/analyze_midgame_tiebreak_value.py --max-worlds 1200
"""
import argparse
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402


def open5x5_raw(w, h, cv, pos):
    """生产原实现 utils/probability.py:1555：越界计为已开。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                res += 1
                continue
            if 0 <= cv[j][i] <= 8:
                res += 1
    return res


def open5x5_fixed(w, h, cv, pos):
    """只数真实已开格（越界跳过）。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if 0 <= cv[j][i] <= 8:
                res += 1
    return res


def frontier_adj(w, h, cv, pos):
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i == x and j == y:
                continue
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if cv[j][i] == 9 or cv[j][i] == 10:
                res += 1
    return res


def is_frontier_cell(w, h, cv, pos):
    """该未开格是否"紧邻已开数字格"（即受局部约束的前沿格）。"""
    x, y = pos
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if 0 <= cv[j][i] <= 8:
                return True
    return False


def near_frontier(w, h, cv, pos):
    """5x5 内"前沿格数"（未开且紧邻已开数字格）。

    这是**内部格的真正信息量代理**：内部格安全率恒等（只受总雷数约束），
    点开它本身无风险好坏之分，但若它紧邻前沿格，点开后露出的数字会约束
    那些前沿格 ⇒ 有价值；孤立在远处的内部格点开后几乎不提供新约束。
    生产现用的 open_num5x5 数的是"已开格"，对内部格而言贴近已开区才会高分，
    并不直接数前沿格数。
    """
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i == x and j == y:
                continue
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if cv[j][i] == 9 and is_frontier_cell(w, h, cv, (i, j)):
                res += 1
    return res


def open_plus_frontier(w, h, cv, pos):
    """已开格数（越界不计） + 前沿格数：两个信息代理合并打分。"""
    return open5x5_fixed(w, h, cv, pos) + near_frontier(w, h, cv, pos)


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = 0
        for gi, sel in enumerate(idx):
            mt += sum(int(v) for v in case["groups"][gi][sel])
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def sign_test(wins, losses):
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


RULES = ("lowest", "open5x5_raw", "open5x5_fixed", "frontier_adj",
         "expand", "farthest_from_edge", "near_frontier", "open_plus_frontier")


def pick_by_rule(rule, tie, eng, wk2, op2, cv, w, h):
    """在平局集 tie 内按给定准则选一格（平局再按位索引升序，确定性）。"""
    coords = {cd: eng._coord(cd) for cd in tie}
    if rule == "lowest":
        return min(tie)
    if rule == "open5x5_raw":
        return max(tie, key=lambda cd: (open5x5_raw(w, h, cv, coords[cd]), -cd))
    if rule == "open5x5_fixed":
        return max(tie, key=lambda cd: (open5x5_fixed(w, h, cv, coords[cd]), -cd))
    if rule == "frontier_adj":
        return max(tie, key=lambda cd: (frontier_adj(w, h, cv, coords[cd]), -cd))
    if rule == "expand":
        return max(tie, key=lambda cd: (eng.expand_score(cd, wk2, op2), -cd))
    if rule == "farthest_from_edge":
        return max(tie, key=lambda cd: (bdist(w, h, coords[cd]), -cd))
    if rule == "near_frontier":
        return max(tie, key=lambda cd: (near_frontier(w, h, cv, coords[cd]), -cd))
    if rule == "open_plus_frontier":
        return max(tie, key=lambda cd: (open_plus_frontier(w, h, cv,
                                                         coords[cd]), -cd))
    raise ValueError(rule)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=60.0)
    ap.add_argument("--max-worlds", type=int, default=1200)
    ap.add_argument("--node-cap", type=int, default=1_500_000)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--cases", default=None)
    args = ap.parse_args()

    path = args.cases or os.path.join(ROOT, "bench", "data",
                                      "midgame_cases.json")
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]
    print("=" * 100)
    print(f"破平局准则的价值（忠实 V* 口径）：limitation ∈ "
          f"({args.min_lim}, {args.max_lim}]，世界数 ≤ {args.max_worlds:,}")
    print(f"候选样本 {len(sample)} 个决策点")
    print("=" * 100, flush=True)

    res = {r: [] for r in RULES}
    res["oracle_in_tie"] = []
    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0, "no_tie": 0}

    for k, c in enumerate(sample):
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
        wk = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            skipped["err"] += 1
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            skipped["budget"] += 1
            print(f"  [{(k+1)}/{len(sample)}] w={nw} 预算淘汰 → 整点不计入",
                  flush=True)
            continue
        v_opt = max(q.values())
        saf = {cd: eng.safety(cd, wk2) for cd in cands}
        smax = max(saf.values())
        tie = [cd for cd in cands if saf[cd] >= smax - 1e-12]
        if len(tie) < 2:
            skipped["no_tie"] += 1
            continue
        cv = c["cell_value"]
        w, h = int(c["w"]), int(c["h"])

        q_tie_opt = max(q[cd] for cd in tie)
        row = {"limitation": c["limitation"], "worlds": eng.n_worlds,
               "F": c["F"], "U": c["U"], "n_cand": len(cands),
               "n_tie": len(tie), "v_opt": round(v_opt, 6),
               "q_tie_opt": round(q_tie_opt, 6),
               "ceiling_regret": round(v_opt - q_tie_opt, 6)}
        res["oracle_in_tie"].append(v_opt - q_tie_opt)
        for rule in RULES:
            cd = pick_by_rule(rule, tie, eng, wk2, op2, cv, w, h)
            reg = v_opt - q[cd]
            row[rule] = {"regret": round(reg, 6),
                         "quality": round(q[cd], 6),
                         "is_opt": bool(reg <= 1e-12)}
            res[rule].append(reg)
        rows.append(row)
        print(f"  [{len(rows):>2}] lim={c['limitation']:>7} w={nw:>6,} "
              f"tie={len(tie):>3} V*={v_opt:.4f} 天花板regret="
              f"{row['ceiling_regret']:.4f} | "
              + "  ".join("%s=%.4f" % (r, row[r]["regret"]) for r in RULES),
              flush=True)

    print("\n" + "=" * 100)
    print(f"可覆盖 {len(rows)}/{len(sample)}（世界数超限 {skipped['world_cap']}、"
          f"预算淘汰 {skipped['budget']}、无平局 {skipped['no_tie']}、"
          f"错误 {skipped['err']}）")
    print("=" * 100)
    if not rows:
        print("无覆盖样本，不出结论")
        return

    print(f"\n{'破平局准则':<22} {'全对':>9} {'平均regret':>12} {'最大':>10} "
          f"{'总损失':>10}")
    print("-" * 100)
    for rule in RULES:
        v = res[rule]
        ok = sum(1 for x in v if x <= 1e-12)
        print(f"{rule:<22} {f'{ok}/{len(v)}':>9} {sum(v)/len(v):>12.4f} "
              f"{max(v):>10.4f} {sum(v):>10.4f}")
    c = res["oracle_in_tie"]
    print(f"{'★ 平局集内天花板':<22} {f'{sum(1 for x in c if x <= 1e-12)}/{len(c)}':>9} "
          f"{sum(c)/len(c):>12.4f} {max(c):>10.4f} {sum(c):>10.4f}")
    print("  （天花板 regret = 最优格不在平局集里造成的损失。")
    print("    若这项≈0 ⇒ 只要在平局集内选对就能逼近最优。）")

    print("\n" + "=" * 100)
    print("配对符号检验（同一决策点；负 = A 更好）")
    print("=" * 100)
    pairs = [("open5x5_fixed", "open5x5_raw"), ("farthest_from_edge", "open5x5_raw"),
             ("expand", "open5x5_raw"), ("frontier_adj", "open5x5_raw"),
             ("near_frontier", "open5x5_raw"),
             ("open_plus_frontier", "open5x5_raw"),
             ("open5x5_fixed", "lowest"), ("expand", "lowest"),
             ("farthest_from_edge", "lowest"), ("near_frontier", "lowest"),
             ("open_plus_frontier", "lowest")]
    print(f"{'A vs B':<36} {'A更好':>7} {'B更好':>7} {'n':>5} {'p':>8} {'平均Δ':>10}")
    print("-" * 100)
    for a, b in pairs:
        d = [x - y for x, y in zip(res[a], res[b])]
        wq = sum(1 for x in d if x < -1e-12)
        lq = sum(1 for x in d if x > 1e-12)
        print(f"{a + ' vs ' + b:<36} {wq:>7} {lq:>7} {len(d):>5} "
              f"{sign_test(wq, lq):>8.3f} "
              f"{(sum(d)/len(d) if d else 0):>10.4f}  "
              f"{'显著' if sign_test(wq, lq) < 0.05 else ''}")

    out = os.path.join(ROOT, "state", "ab", "midgame_tiebreak_value.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"max_worlds": args.max_worlds, "rows": rows,
                   "skipped": skipped}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
