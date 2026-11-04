# -*- coding: utf-8 -*-
"""真拟合：7 个指标的线性组合权重，能不能超过纯 S0 贪心？

=================== 为什么重做 ====================
上一轮我写「线性 prior 的选格必然落在某一单臂选过的格上 ⇒ 无起点」，
**这个论证是错的**。反例：两指标各自第二好的格，加权和可超各自第一好。
argmax(w·f) 能落在任何单臂都没选过的格上。所以必须真拟合。

=================== 验证协议（这是本脚本的核心，不是拟合本身）====================
样本只有 32～41 个决策点、参数 7 个。**任何 in-sample 拟合都会变好**，
所以判据必须是 **留出集**：

  1. **留一交叉验证（LOO）**：每次留 1 个决策点，在其余点上拟合，
     在留出点上评估。这是小样本下最稳的协议（不浪费样本、无随机性）。
  2. **重复随机切分**（可选，作交叉验证）：100 次随机 70/30，
     报 test 上 regret 的分布。
  3. **嵌套对照**：同一协议下评估 S0（0 参数）与每个单指标（0 参数），
     以及 oracle（在留出集上事后最优，= 上界）。
     ⇒ 若拟合权重的 LOO 表现**不如 S0**，则"拟合能带来增益"被实证否定。

=================== 拟合目标 ====================
直接最小化 regret 是非凸（argmax 不可微）。三种可选：
  · **L2 回归**：用 q 做回归目标拟合 w（可解析、稳定），再取 argmax(w·f)。
    问题：回归拟合的是"预测 q"，不是"选对格"，与目标不完全一致。
  · **网格/随机搜索**：直接搜索 w 使平均 regret 最小（目标对齐，但易过拟合）。
  · **成对排序（pairwise）**：只要求 argmax 选中 q 最高的格，用感知机式更新。
本脚本三者都跑，让结论不依赖单一拟合方法的选择。

用法:
  python bench/fit_linear_prior.py
  python bench/fit_linear_prior.py --matrix state/ab/metric_matrix.json
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


def pick_argmax(fvec, w, cands):
    """按 w·f 选格；平局取候选顺序最前（确定性）。"""
    best_i, best_v = 0, None
    for i in range(len(cands)):
        v = sum(w[k] * fvec[k][i] for k in range(len(w)))
        if best_v is None or v > best_v + 1e-15:
            best_i, best_v = i, v
    return best_i


def regret_at(row, pick_i):
    q = row["q"]
    return max(q) - q[pick_i]


def evaluate(rows, w, metrics):
    """给定权重，返回平均 regret 与逐点 regret。"""
    regs = []
    for row in rows:
        fvec = [row["F"][m] for m in metrics]
        i = pick_argmax(fvec, w, row["cands"])
        regs.append(regret_at(row, i))
    return (sum(regs) / len(regs)) if regs else float("nan"), regs


def fit_l2(rows, metrics, ridge=1e-6):
    """最小二乘拟合 q（带极小 ridge 防奇异）。

    对每个决策点：目标 y_i = q_i，特征 f_i。
    因为 q 在点内可比、跨点不可比，用**点内去均值**后回归
    （等价于拟合"相对优势"，这正是 argmax 需要的）。
    """
    d = len(metrics)
    A = [[0.0] * d for _ in range(d)]
    b = [0.0] * d
    for row in rows:
        q = row["q"]
        mq = sum(q) / len(q)
        fvec = [row["F"][m] for m in metrics]
        n = len(q)
        for i in range(n):
            y = q[i] - mq
            fi = [fvec[k][i] for k in range(d)]
            for r in range(d):
                b[r] += fi[r] * y
                for cc in range(d):
                    A[r][cc] += fi[r] * fi[cc]
    for r in range(d):
        A[r][r] += ridge
    # 高斯消元
    for col in range(d):
        piv = max(range(col, d), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-14:
            continue
        A[col], A[piv] = A[piv], A[col]
        b[col], b[piv] = b[piv], b[col]
        pv = A[col][col]
        for cc in range(col, d):
            A[col][cc] /= pv
        b[col] /= pv
        for r in range(d):
            if r == col:
                continue
            fac = A[r][col]
            if fac == 0.0:
                continue
            for cc in range(col, d):
                A[r][cc] -= fac * A[col][cc]
            b[r] -= fac * b[col]
    return b


def fit_grid(rows, metrics, step=0.5, seed=0):
    """随机搜索权重（目标 = 最小化平均 regret）。"""
    rng = random.Random(seed)
    d = len(metrics)
    best_w, best_r = None, None
    # 候选权重：非负、和为 1 的格点 + 随机采样
    cands = []
    n_div = int(round(1.0 / step))
    for combo in itertools.product(range(n_div + 1), repeat=d):
        s = sum(combo)
        if s == 0:
            continue
        if abs(s - n_div) <= 0:
            cands.append([c / n_div for c in combo])
    for _ in range(400):
        v = [rng.random() for _ in range(d)]
        s = sum(v)
        cands.append([x / s for x in v])
    for w in cands:
        r, _ = evaluate(rows, w, metrics)
        if best_r is None or r < best_r - 1e-12:
            best_w, best_r = w, r
    return best_w


def fit_pairwise(rows, metrics, epochs=60, seed=0):
    """感知机式成对排序：若选中的格不是最优，就往"最优−选中"方向调。"""
    rng = random.Random(seed)
    d = len(metrics)
    w = [1.0 / d] * d
    for _ in range(epochs):
        rng.shuffle(rows)
        for row in rows:
            q = row["q"]
            fvec = [row["F"][m] for m in metrics]
            i = pick_argmax(fvec, w, row["cands"])
            opt = max(range(len(q)), key=lambda k: q[k])
            if q[opt] - q[i] <= 1e-12:
                continue
            for k in range(d):
                w[k] += 0.05 * (fvec[k][opt] - fvec[k][i])
    s = sum(w)
    return [x / s for x in w] if s > 0 else [1.0 / d] * d


def loo_eval(rows, metrics, fitter):
    """留一交叉验证：返回 LOO 平均 regret。"""
    regs = []
    n = len(rows)
    for i in range(n):
        train = rows[:i] + rows[i + 1:]
        test = [rows[i]]
        w = fitter(train, metrics)
        r, _ = evaluate(test, w, metrics)
        regs.append(r)
    return sum(regs) / len(regs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab",
                                                    "metric_matrix.json"))
    args = ap.parse_args()

    path = args.matrix if os.path.isabs(args.matrix) else os.path.join(
        ROOT, args.matrix)
    if not os.path.exists(path):
        print(f"缺少 {path}，请先跑 bench/dump_metric_matrix.py")
        return 1
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload["rows"]
    metrics = payload["metrics"]

    print("=" * 100)
    print("线性组合权重的真拟合 + 留一交叉验证")
    print("=" * 100)
    print(f"样本 {len(rows)} 个决策点，"
          f"{sum(len(r['cands']) for r in rows)} 个候选格，"
          f"{len(metrics)} 个指标：{metrics}")
    print()

    # ---------- 零参数基线 ----------
    print("=" * 100)
    print("零参数基线（LOO 不需要，直接全样本；这些臂无拟合、无过拟合风险）")
    print("=" * 100)
    print(f"{'臂':<24}{'平均regret':>13}{'全对':>9}{'备注':>22}")
    print("-" * 100)
    baselines = {}
    for mi, m in enumerate(metrics):
        w = [0.0] * len(metrics)
        w[mi] = 1.0
        r, regs = evaluate(rows, w, metrics)
        baselines[m] = r
        ok = sum(1 for x in regs if x <= 1e-12)
        print(f"{m:<24}{r:>13.4f}{f'{ok}/{len(rows)}':>9}"
              f"{'单指标（无拟合）':>22}")
    s0_r = baselines.get("S0")
    print(f"\n  ⇒ 基线 S0 = {s0_r:.4f}（这是必须超越的门槛）")

    # ---------- 拟合（in-sample，只作参照）----------
    print()
    print("=" * 100)
    print("拟合结果（★ in-sample 数字必然变好，不可作为证据；证据在下一节 LOO）")
    print("=" * 100)
    fitters = {
        "L2 回归": lambda tr, ms: fit_l2(tr, ms),
        "随机搜索": lambda tr, ms: fit_grid(tr, ms),
        "成对排序": lambda tr, ms: fit_pairwise(tr, ms),
    }
    print(f"{'拟合方法':<14}{'in-sample regret':>18}{'权重':>52}")
    print("-" * 100)
    insample = {}
    for name, fit in fitters.items():
        w = fit(rows, metrics)
        r, _ = evaluate(rows, w, metrics)
        insample[name] = (w, r)
        wtxt = " ".join(f"{m}={x:.2f}" for m, x in zip(metrics, w))
        print(f"{name:<14}{r:>18.4f}  {wtxt}")

    # ---------- LOO 交叉验证（真正的证据）----------
    print()
    print("=" * 100)
    print("留一交叉验证（LOO）—— 这是唯一可作为证据的数字")
    print("=" * 100)
    print(f"{'拟合方法':<14}{'LOO regret':>13}{'vs S0':>11}{'判定':>22}")
    print("-" * 100)
    loo_results = {}
    for name, fit in fitters.items():
        try:
            r = loo_eval(rows, metrics, fit)
        except Exception as e:                             # noqa: BLE001
            print(f"{name:<14}  失败: {e}")
            continue
        loo_results[name] = r
        delta = r - s0_r
        verdict = ("优于 S0" if delta < -1e-4 else
                   ("劣于 S0" if delta > 1e-4 else "与 S0 持平"))
        print(f"{name:<14}{r:>13.4f}{delta:>+11.4f}{verdict:>22}")

    # ---------- 上界：oracle ----------
    print()
    print("=" * 100)
    print("上界与可达性")
    print("=" * 100)
    oracle_regs = []
    for row in rows:
        q = row["q"]
        oracle_regs.append(0.0)         # oracle 选最优 ⇒ regret 0
    print(f"  oracle（事后最优）regret = 0.0000（定义如此）")
    print(f"  S0 贪心            regret = {s0_r:.4f}")
    print(f"  ⇒ 理论上限可回收 {s0_r:.4f}")
    best_loo = min(loo_results.values()) if loo_results else None
    if best_loo is not None:
        rec = (s0_r - best_loo) / s0_r * 100 if s0_r > 0 else 0.0
        print(f"  最佳 LOO 拟合权重   regret = {best_loo:.4f}"
              f"（回收 {rec:.1f}% 的可回收损失）")

    # ---------- 权重稳定性 ----------
    print()
    print("=" * 100)
    print("权重稳定性（LOO 各折拟合出的权重差异；差异大 = 不可辨识）")
    print("=" * 100)
    w_all = []
    for i in range(len(rows)):
        train = rows[:i] + rows[i + 1:]
        w_all.append(fit_l2(train, metrics))
    print(f"{'指标':<8}{'均值':>9}{'标准差':>10}{'最小':>9}{'最大':>9}{'符号一致率':>12}")
    print("-" * 100)
    for k, m in enumerate(metrics):
        vals = [w[k] for w in w_all]
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
        pos = sum(1 for v in vals if v > 1e-9)
        neg = sum(1 for v in vals if v < -1e-9)
        consist = max(pos, neg) / len(vals)
        print(f"{m:<8}{mu:>9.3f}{sd:>10.3f}{min(vals):>9.3f}"
              f"{max(vals):>9.3f}{consist:>11.0%}")
    print()
    print("  读法：符号一致率低（<80%）⇒ 该指标的作用方向在不同子样本上翻转")
    print("        ⇒ 权重不可辨识，把它放进线性组合只会拟合噪声。")

    out = os.path.join(ROOT, "state", "ab", "linear_prior_fit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "n_points": len(rows), "metrics": metrics,
            "baselines": baselines, "insample": {k: v[1] for k, v in insample.items()},
            "loo": loo_results,
            "weights_insample": {k: v[0] for k, v in insample.items()},
        }, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
