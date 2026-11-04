# -*- coding: utf-8 -*-
"""评估用户提出的 8 个启发式指标：哪些**在可精确算真值的样本上真的有效**。

=================== 评估口径（严格）====================
只在 **世界数可枚举** 的中局决策点上评估（能算忠实 V*）：
  · 真值 = 每格 after_click(c)（= safety × E[完美续玩胜率]）
  · v_opt = max_c after_click(c)
  · 指标 c 的 regret = v_opt − after_click(该指标选中的格)
  · 决不做"按易算程度筛选样本"：抛 Budget 的决策点**整点丢弃**，不计入

=================== 8 个指标的可计算性分类（这是评估的第一层结论）====================
用户给的 8 个指标中，有几项**在本仓现有信息下无法定义或已退化**：
  S0   基础安全概率            —— 可算，且**已是生产贪心的选格准则**（基线）
  IG   信息增益                —— 可近似为「safety × E[点开后新开格数]」（已实现）
  U    Filter-U 两步最坏存活    —— 可算（用 world 集合精确求 min 下一步 safety）
  E    Filter-E 预期安全格数    —— 可算（点开后必然安全的新格数期望）
  B    约束块强度/边界收益      —— **信息不足**：本仓快照只有 groups 的解矩阵，
                                 没有"连通块"结构；需重建块图才能定义
  R    尾部风险/后续猜测次数    —— **部分可算**：用"点开后仍需猜测的期望"代理，
                                 精确版本需整局 rollout
  L    位置与模式先验          —— 可算（到边界距离 / 5x5 模式）
  G    全局雷数一致性权重      —— **已等价于 S0**：生产 `_pbs_compute_dp` 已经是
                                 "按总雷数组合数加权"的精确概率 ⇒ 无新信息

=================== 为什么这层分类比打分更重要 ====================
若一个指标已等价于现有准则（S0/G）或与它共线，那么把它加进线性加权和
**只会引入拟合噪声**，不会带来新信号。用户方案的核心风险就在这里：
`prior = Σ w_i f_i` 在样本量有限（本仓可用精确样本 <50）时必然过拟合。

用法:
  python bench/eval_heuristic_metrics.py --max-worlds 400
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


def _rank(v):
    """平均秩（处理并列）。"""
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    """Spearman 秩相关；任一向量为常数时返回 None（相关无定义）。"""
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    if max(ra) == min(ra) or max(rb) == min(rb):
        return None
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=60.0)
    ap.add_argument("--max-worlds", type=int, default=400)
    ap.add_argument("--node-cap", type=int, default=3_000_000)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "heuristic_metrics.json"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]

    print("=" * 100)
    print(f"启发式指标评估（真值口径）：limitation ∈ ({args.min_lim}, "
          f"{args.max_lim}]，世界数 ≤ {args.max_worlds:,}")
    print(f"候选 {len(sample)} 个决策点")
    print("=" * 100, flush=True)

    # 每个指标的选格打分函数：返回可在 max() 里比较的分数（平局取小索引）
    METRICS = ("S0_safety", "IG_expand", "U_filter_u", "E_filter_e",
               "L_center", "S0_then_IG", "S0_then_U", "S0_then_E")

    res = {m: [] for m in METRICS}
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
        cv = c["cell_value"]
        w, h = int(c["w"]), int(c["h"])

        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            skipped["budget"] += 1
            continue
        v_opt = max(q.values())

        # ---------- 各指标打分 ----------
        saf = {cd: eng.safety(cd, wk2) for cd in cands}

        def safe_after(cd):
            """点开 cd（假设安全）后，每个观测分支下"必然安全格"的加权期望数。

            直接反映 Filter-E：「点它之后能确定多少安全格」。
            """
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                return 0.0
            if p_safe <= 0.0:
                return 0.0
            acc = 0.0
            for sub, op3, wt in outs:
                _u, inter = eng._union_inter(sub)
                safe_next = eng.all_mask & ~op3 & ~inter
                acc += wt * eng.pop(safe_next)
            return p_safe * acc

        def worst_next(cd):
            """Filter-U：p_safe(c) × min_{c'} p_safe(c' | c 安全) 的最小值。

            = safety(c) × 该格安全后、后续最差候选的安全率（越小越危险）。
            """
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                return 0.0
            if p_safe <= 0.0:
                return 0.0
            worst = 1.0
            for sub, op3, wt in outs:
                cands3 = eng.candidates(sub, op3)
                if not cands3:
                    continue
                mn = min(eng.safety(x, sub) for x in cands3)
                worst = min(worst, mn)
            return p_safe * worst

        def expand(cd):
            try:
                return eng.expand_score(cd, wk2, op2)
            except Budget:
                return 0.0

        scores = {
            "S0_safety": {cd: saf[cd] for cd in cands},
            "IG_expand": {cd: expand(cd) for cd in cands},
            "U_filter_u": {cd: worst_next(cd) for cd in cands},
            "E_filter_e": {cd: safe_after(cd) for cd in cands},
            "L_center": {cd: bdist(w, h, eng._coord(cd)) for cd in cands},
        }

        def pick_by(sc, tiebreak=None):
            best = max(sc[cd] for cd in cands)
            tied = [cd for cd in cands if sc[cd] >= best - 1e-12]
            if len(tied) == 1 or tiebreak is None:
                return min(tied)
            tb = max(tiebreak[cd] for cd in tied)
            return min(cd for cd in tied if tiebreak[cd] >= tb - 1e-12)

        picks = {m: pick_by(sc) for m, sc in scores.items()}
        # 「先按安全率，平局再用启发式破」
        picks["S0_then_IG"] = pick_by(scores["S0_safety"], scores["IG_expand"])
        picks["S0_then_U"] = pick_by(scores["S0_safety"], scores["U_filter_u"])
        picks["S0_then_E"] = pick_by(scores["S0_safety"], scores["E_filter_e"])

        saf_max = max(saf.values())
        tie_n = sum(1 for cd in cands if saf[cd] >= saf_max - 1e-12)
        # 天花板：**只允许在安全率平局集内**做选择时能拿到的最好值。
        # 生产贪心其实已经用了平局集（win_rate 平局时随机/按内部格选），
        # 所以「启发式破平局」的价值上限 = 平局集内 oracle 的 regret。
        tie_set = [cd for cd in cands if saf[cd] >= saf_max - 1e-12]
        v_tie_opt = max(q[cd] for cd in tie_set)
        row = {"limitation": c["limitation"], "worlds": nw, "F": c["F"],
               "U": c["U"], "n_cand": len(cands), "n_tie": tie_n,
               "v_opt": round(v_opt, 6),
               "ceiling_regret": round(v_opt - v_tie_opt, 6)}
        # 同一决策点内、跨候选格的指标取值（供相关性分析）
        row["_scores"] = {m: {str(cd): round(scores[m][cd], 9) for cd in cands}
                          for m in ("IG_expand", "U_filter_u", "E_filter_e",
                                    "L_center")}
        row["_safety"] = {str(cd): round(saf[cd], 9) for cd in cands}
        for m in METRICS:
            cd = picks[m]
            reg = v_opt - q[cd]
            row[m] = {"regret": round(reg, 6), "is_opt": bool(reg <= 1e-12),
                      "in_safety_tie": bool(saf[cd] >= saf_max - 1e-12)}
            res[m].append(reg)
        rows.append(row)

    print("\n" + "=" * 100)
    print(f"可覆盖 {len(rows)}/{len(sample)}（{skipped}）")
    print("=" * 100)
    if not rows:
        return

    print(f"\n{'指标':<16} {'全对':>9} {'平均regret':>12} {'最大':>10} "
          f"{'总损失':>10} {'选中格在安全率平局内':>20}")
    print("-" * 100)
    for m in METRICS:
        v = res[m]
        ok = sum(1 for x in v if x <= 1e-12)
        intra = sum(1 for r in rows if r[m]["in_safety_tie"])
        print(f"{m:<16} {f'{ok}/{len(v)}':>9} {sum(v)/len(v):>12.4f} "
              f"{max(v):>10.4f} {sum(v):>10.4f} {f'{intra}/{len(v)}':>20}")

    print("\n" + "=" * 100)
    print("配对符号检验（vs 生产基线 S0_safety；负 = 该指标更好）")
    print("=" * 100)
    print(f"{'指标 vs S0':<28} {'更好':>6} {'更差':>6} {'n':>5} {'p':>9} {'平均Δ':>10}")
    print("-" * 100)
    for m in METRICS:
        if m == "S0_safety":
            continue
        d = [r[m]["regret"] - r["S0_safety"]["regret"] for r in rows]
        wn = sum(1 for x in d if x < -1e-12)
        ls = sum(1 for x in d if x > 1e-12)
        p = sign_test(wn, ls)
        print(f"{m + ' vs S0':<28} {wn:>6} {ls:>6} {len(d):>5} {p:>9.3f} "
              f"{sum(d)/len(d):>+10.4f}  {'显著' if p < 0.05 else ''}")

    print("\n" + "=" * 100)
    print("天花板：只在「安全率平局集」内选格时的最优可达")
    print("=" * 100)
    ceil = [r["ceiling_regret"] for r in rows]
    print(f"  平局集内 oracle 的平均 regret = {sum(ceil)/len(ceil):.4f}"
          f"（= 任何破平局准则的理论上限，越小空间越少）")
    print(f"  平局集内 oracle 完全选对 {sum(1 for x in ceil if x <= 1e-12)}/{len(ceil)}")
    print(f"  S0 自己的平均 regret = {sum(res['S0_safety'])/len(rows):.4f}")
    gap = sum(res["S0_safety"]) / len(rows) - sum(ceil) / len(ceil)
    print(f"  ⇒ 破平局可回收的 regret 上限 = {gap:.4f}/决策点")

    print("\n" + "=" * 100)
    print("指标之间的相关性（同一决策点内、跨候选格的 Spearman 秩相关均值）")
    print("=" * 100)
    print("  高度共线的指标合并进线性 prior 只会放大噪声、不提供新信号。")
    pairs = [("IG_expand", "U_filter_u"), ("IG_expand", "E_filter_e"),
             ("U_filter_u", "E_filter_e"), ("IG_expand", "L_center"),
             ("U_filter_u", "L_center"), ("E_filter_e", "L_center")]
    _rho = {p: [] for p in pairs}
    _rho_s0 = {m: [] for m in ("IG_expand", "U_filter_u", "E_filter_e", "L_center")}
    for r in rows:
        sc = r["_scores"]
        saf = r["_safety"]
        keys = [k for k in saf]
        for a, b in pairs:
            v = spearman([sc[a][k] for k in keys], [sc[b][k] for k in keys])
            if v is not None:
                _rho[(a, b)].append(v)
        for m in _rho_s0:
            v = spearman([sc[m][k] for k in keys], [saf[k] for k in keys])
            if v is not None:
                _rho_s0[m].append(v)
    print(f"\n{'指标对':<28} {'平均rho':>9} {'n点':>6}")
    print("-" * 60)
    for a, b in pairs:
        v = _rho[(a, b)]
        if v:
            print(f"{a + ' ~ ' + b:<28} {sum(v)/len(v):>+9.3f} {len(v):>6}")
    print()
    for m, v in _rho_s0.items():
        if v:
            print(f"{m + ' ~ S0_safety':<28} {sum(v)/len(v):>+9.3f} {len(v):>6}")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "skipped": skipped,
                   "args": vars(args)}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
