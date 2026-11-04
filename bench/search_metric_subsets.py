# -*- coding: utf-8 -*-
"""对 7 个指标的**全部 128 个子集**做线性组合评估（含消融与 MDE）。

=================== 这是对"验证不同指标的线性组合"的完整回答 ====================
上一轮只试了"全部 7 个指标"这一种组合，且用 LOO 发现它劣于 S0。
但用户要的是"不同指标的线性组合"⇒ 必须穷举**子集**，回答：
  1. 是否存在某个子集，其线性组合在留出集上优于纯 S0？
  2. 每个指标的边际贡献是多少（消融：从最佳子集里去掉它，看变差多少）？
  3. 检出力（MDE）多大 ⇒ "没检出"能不能等同于"不存在"？

=================== 三种权重模式（关键设计）====================
  · equal    : 等权 Σ f_i（**零参数、零过拟合风险**）
               这正是用户原方案 `prior = Σ w_i f_i` 不训练的形式。
               若等权都赢不了 S0，那"权重怎么调"就无从谈起。
  · loo_pair : 感知机式成对排序，留一交叉验证
  · loo_grid : 随机/格点搜索最小化 regret，留一交叉验证

=================== 为什么必须 LOO ====================
样本 41 个决策点、参数最多 7 个。in-sample 一定变好（已实测：
随机搜索 in-sample 0.0131 优于 S0 的 0.0157，但 LOO 0.0241 劣于 S0）。
只有 LOO 是证据。

用法:
  python bench/search_metric_subsets.py
"""
import argparse
import itertools
import json
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

ALL_METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def pick_best(fvec, w, cands):
    best_i, best_v = 0, None
    for i in range(len(cands)):
        v = 0.0
        for k in range(len(w)):
            v += w[k] * fvec[k][i]
        if best_v is None or v > best_v + 1e-15:
            best_i, best_v = i, v
    return best_i


def regret_of(row, subset, w):
    fvec = [row["F"][m] for m in subset]
    i = pick_best(fvec, w, row["cands"])
    q = row["q"]
    return max(q) - q[i]


def mean_regret(rows, subset, w):
    if not rows:
        return float("nan")
    return sum(regret_of(r, subset, w) for r in rows) / len(rows)


def per_point(rows, subset, w):
    return [regret_of(r, subset, w) for r in rows]


# ---------------- 拟合器 ----------------
def fit_pairwise(rows, subset, epochs=40, seed=0):
    rng = random.Random(seed)
    d = len(subset)
    w = [1.0 / d] * d
    idxs = list(range(len(rows)))
    for _ in range(epochs):
        rng.shuffle(idxs)
        for ri in idxs:
            row = rows[ri]
            fvec = [row["F"][m] for m in subset]
            i = pick_best(fvec, w, row["cands"])
            q = row["q"]
            opt = max(range(len(q)), key=lambda k: q[k])
            if q[opt] - q[i] <= 1e-12:
                continue
            for k in range(d):
                w[k] += 0.05 * (fvec[k][opt] - fvec[k][i])
    s = sum(w)
    return [x / s for x in w] if s > 1e-9 else [1.0 / d] * d


def fit_grid(rows, subset, n_rand=250, seed=0):
    rng = random.Random(seed)
    d = len(subset)
    cands = []
    n_div = 4
    for combo in itertools.product(range(n_div + 1), repeat=d):
        s = sum(combo)
        if s == 0 or abs(s - n_div) > 0:
            continue
        cands.append([c / n_div for c in combo])
    for _ in range(n_rand):
        v = [rng.random() for _ in range(d)]
        s = sum(v)
        cands.append([x / s for x in v])
    best_w, best_r = None, None
    for w in cands:
        r = mean_regret(rows, subset, w)
        if best_r is None or r < best_r - 1e-12:
            best_w, best_r = w, r
    return best_w


def loo(rows, subset, fitter):
    regs = []
    n = len(rows)
    for i in range(n):
        train = rows[:i] + rows[i + 1:]
        w = fitter(train, subset)
        regs.append(regret_of(rows[i], subset, w))
    return sum(regs) / n, regs


def sign_p(d):
    wn = sum(1 for x in d if x < -1e-12)
    ls = sum(1 for x in d if x > 1e-12)
    nz = wn + ls
    if nz == 0:
        return 1.0, wn, ls
    k = min(wn, ls)
    p = min(1.0, 2.0 * sum(math.comb(nz, i) * (0.5 ** nz) for i in range(k + 1)))
    return p, wn, ls


def mde80(diffs):
    n = len(diffs)
    if n < 2:
        return None
    mu = sum(diffs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in diffs) / (n - 1))
    return (1.959964 + 0.841621) * sd / math.sqrt(n), sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab",
                                                    "metric_matrix.json"))
    args = ap.parse_args()

    path = args.matrix if os.path.isabs(args.matrix) else os.path.join(
        ROOT, args.matrix)
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload["rows"]
    print("=" * 100)
    print("7 指标全部 128 个子集的线性组合评估")
    print("=" * 100)
    print(f"样本 {len(rows)} 个可精确求值决策点，"
          f"{sum(len(r['cands']) for r in rows)} 个候选格")
    print()

    # ---- 单指标基线 ----
    s0_w = [1.0]
    s0_regs = per_point(rows, ("S0",), s0_w)
    s0_mean = sum(s0_regs) / len(s0_regs)
    print(f"基线：S0 单指标 平均 regret = {s0_mean:.4f}")
    print()

    # ---- 128 子集 × 三种权重模式 ----
    results = []
    subsets = []
    for r in range(1, len(ALL_METRICS) + 1):
        for combo in itertools.combinations(ALL_METRICS, r):
            subsets.append(combo)

    print(f"穷举 {len(subsets)} 个子集 ...", flush=True)
    for si, subset in enumerate(subsets):
        d = len(subset)
        # equal（零参数）
        eq_regs = per_point(rows, subset, [1.0 / d] * d)
        eq_mean = sum(eq_regs) / len(eq_regs)
        # LOO pairwise
        lp_mean, lp_regs = loo(rows, subset, fit_pairwise)
        results.append({
            "subset": list(subset), "k": d,
            "equal": eq_mean, "loo_pair": lp_mean,
            "eq_regs": eq_regs, "lp_regs": lp_regs,
        })
        if (si + 1) % 32 == 0:
            print(f"  已处理 {si + 1}/{len(subsets)}", flush=True)

    # ---- 排名 ----
    print()
    print("=" * 100)
    print("TOP 15（按等权，零参数 ⇒ 无过拟合风险）")
    print("=" * 100)
    print(f"{'排名':>4}{'子集':<30}{'k':>3}{'等权regret':>13}{'vs S0':>10}"
          f"{'LOO配对':>11}{'vs S0':>10}")
    print("-" * 100)
    by_eq = sorted(results, key=lambda r: r["equal"])
    for i, r in enumerate(by_eq[:15], 1):
        print(f"{i:>4}{'+'.join(r['subset']):<30}{r['k']:>3}"
              f"{r['equal']:>13.4f}{r['equal']-s0_mean:>+10.4f}"
              f"{r['loo_pair']:>11.4f}{r['loo_pair']-s0_mean:>+10.4f}")

    print()
    print("=" * 100)
    print("TOP 15（按 LOO 配对拟合）")
    print("=" * 100)
    print(f"{'排名':>4}{'子集':<30}{'k':>3}{'LOO配对':>11}{'vs S0':>10}"
          f"{'等权regret':>13}{'p值':>8}")
    print("-" * 100)
    by_lp = sorted(results, key=lambda r: r["loo_pair"])
    for i, r in enumerate(by_lp[:15], 1):
        d = [a - b for a, b in zip(r["lp_regs"], s0_regs)]
        p, wn, ls = sign_p(d)
        print(f"{i:>4}{'+'.join(r['subset']):<30}{r['k']:>3}"
              f"{r['loo_pair']:>11.4f}{r['loo_pair']-s0_mean:>+10.4f}"
              f"{r['equal']:>13.4f}{p:>8.3f}")

    # ---- 判定 ----
    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    best_eq = by_eq[0]
    best_lp = by_lp[0]
    print(f"  最佳等权子集   : {'+'.join(best_eq['subset'])}"
          f"  regret={best_eq['equal']:.4f}  Δ={best_eq['equal']-s0_mean:+.4f}")
    print(f"  最佳 LOO 配子集: {'+'.join(best_lp['subset'])}"
          f"  regret={best_lp['loo_pair']:.4f}  Δ={best_lp['loo_pair']-s0_mean:+.4f}")

    n_eq_better = sum(1 for r in results if r["equal"] < s0_mean - 1e-4)
    n_lp_better = sum(1 for r in results if r["loo_pair"] < s0_mean - 1e-4)
    print(f"  等权优于 S0 的子集数   : {n_eq_better}/{len(results)}")
    print(f"  LOO配对优于 S0 的子集数: {n_lp_better}/{len(results)}")

    # 最优者的配对检验 + MDE
    d_best = [a - b for a, b in zip(best_lp["lp_regs"], s0_regs)]
    p, wn, ls = sign_p(d_best)
    mde = mde80(d_best)
    print()
    print(f"  最佳 LOO 子集 vs S0 配对检验：{wn} 更好 / {ls} 更差，p={p:.3f}")
    if mde:
        m, sd = mde
        print(f"  配对差 SD={sd:.4f}，n={len(d_best)} ⇒ MDE(80%)={m:.4f}")
        print(f"  ⇒ 本样本只能检出 |Δ| ≥ {m:.4f} 的效应；"
              f"更小的真实收益**无法排除也无法证实**。")

    # ---- 消融：从最佳等权子集逐一移除 ----
    print()
    print("=" * 100)
    print(f"消融：从「{'+'.join(best_eq['subset'])}」逐一移除每个指标（等权口径）")
    print("=" * 100)
    base_sub = best_eq["subset"]
    base_r = best_eq["equal"]
    print(f"{'移除':<8}{'剩余子集':<28}{'k':>3}{'regret':>11}{'变差多少':>12}")
    print("-" * 100)
    for m in base_sub:
        sub = tuple(x for x in base_sub if x != m)
        if not sub:
            print(f"{m:<8}{'(空)':<28}{0:>3}{'—':>11}{'—':>12}")
            continue
        r = mean_regret(rows, sub, [1.0 / len(sub)] * len(sub))
        print(f"{m:<8}{'+'.join(sub):<28}{len(sub):>3}{r:>11.4f}{r-base_r:>+12.4f}")
    print(f"\n  （基线 {base_r:.4f}；数字为正 = 移除该指标后变差 = 它有正贡献）")

    out = os.path.join(ROOT, "state", "ab", "metric_subsets.json")
    slim = [{k: v for k, v in r.items() if k not in ("eq_regs", "lp_regs")}
            for r in results]
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"n_points": len(rows), "s0_mean": s0_mean,
                   "results": slim, "best_equal": best_eq["subset"],
                   "best_loo": best_lp["subset"]}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
