# -*- coding: utf-8 -*-
"""向量化权重搜索 / LOO（等价性已对拍），用于固定评估预算下的公平比较。

=================== 为什么要重写（不是"优化"而是"预算不足"）====================
`optimize_weights.py` 的标量实现每次权重评估是对每个决策点做 Python 三重循环：
    for row in rows: for cand in cands: for k in 7 metrics
n=300、trials=800 时全量 LOO 需 n×trials 次 mean_regret。
上一轮用 `--loo-limit 40` 缩小样本绕开成本——**这本身是一个未申报的口径妥协**
（LOO 只在前 40 点上算，而 in-sample 用了全部 77 点）。

本模块用 numpy 把「评估一组权重」变成 (n, n_cand) 的矩阵乘法：
    score = F[k] @ w   →  argmax  →  regret = max(q) - q[argmax]
单次评估从 O(n·c·7) Python 循环降为 7 次 numpy 矩阵加。
**语义与标量实现逐位一致**（下方 verify_equivalent 做随机对拍）。

=================== 硬性纪律 ====================
1. 本模块的等价性必须由 verify_equivalent() 证明后才可用于正式读数；
2. LOO 的每折必须**独立**搜索（不得复用全量最优权重），否则是泄漏；
3. 报实际评估次数（n_trials × n_folds），不报"约"。

用法:
  python bench/fast_loo.py --self-test
  python bench/fast_loo.py --matrix state/ab/mm_full.json --trials 800
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


# ==================== 数据打包 ====================
class Packed:
    """把一个 metric matrix 打包成定长 numpy 数组，供批量评估。

    各决策点候选数不同 ⇒ 用 padding 对齐到 max_n_cand。

    ⚠️ padding 值必须用**有限**的大负数，不能用 -inf：
       因为评估时算的是 sum_k w_k * F[k,i,c]，当 w_k = 0 时
       0 * (-inf) = nan，会把整个 score 行污染成 nan，argmax 便会
       选到 padding 格，regret 变成 inf（实测踩到：S0 单指标 regret=inf）。
       用 -1e18 这个有限值后，w_k=0 时为 0，w_k>0 时足够负而不会被选中。
    """

    PAD = -1e18

    def __init__(self, rows):
        self.n = len(rows)
        self.m = len(METRICS)
        n_cand = [len(r["q"]) for r in rows]
        self.C = max(n_cand) if n_cand else 0
        C, n = self.C, self.n

        self.F = np.full((self.m, n, C), self.PAD, dtype=np.float64)
        self.q = np.zeros((n, C), dtype=np.float64)
        self.qmax = np.full((n, 1), -np.inf, dtype=np.float64)
        for i, r in enumerate(rows):
            c = len(r["q"])
            for k, mname in enumerate(METRICS):
                self.F[k, i, :c] = r["F"][mname]
            self.q[i, :c] = r["q"]
            self.qmax[i, 0] = max(r["q"])
        self.valid = np.arange(C)[None, :] < np.array(n_cand)[:, None]

    def regrets(self, w):
        """返回每个决策点的 regret（n 维）。w 长度 7，自动归一化检查不做。"""
        w = np.asarray(w, dtype=np.float64)
        # score[i, c] = sum_k w_k * F[k, i, c]
        score = np.einsum("k,kic->ic", w, self.F)
        idx = np.argmax(score, axis=1)
        picked = self.q[np.arange(self.n), idx]
        return self.qmax[:, 0] - picked

    def mean_regret(self, w):
        return float(np.mean(self.regrets(w)))

    def subset(self, idx):
        """返回 idx 指定的子集（用于 LOO 训练折），避免复制原始 rows。"""
        obj = Packed.__new__(Packed)
        obj.n = len(idx)
        obj.m = self.m
        obj.C = self.C
        ii = np.asarray(idx)
        obj.F = self.F[:, ii, :]
        obj.q = self.q[ii, :]
        obj.qmax = self.qmax[ii, :]
        obj.valid = self.valid[ii, :]
        return obj


# ==================== 等价性对拍（必须先通过）====================
def scalar_mean_regret(rows, w, pick_best, regret_of):
    """调用 optimize_weights.py 的原始标量实现，作为对拍基准。"""
    return sum(regret_of(r, w) for r in rows) / len(rows)


def verify_equivalent(rows, n_trials=30, seed=0, verbose=True):
    """随机权重下，向量化 mean_regret 必须与标量实现逐点一致。

    容差 1e-12：两条路径都是 IEEE754 double 的同类累加，
    但求和顺序不同（numpy pairwise vs Python 顺序）可能产生末位差。
    **regret 的 argmax 选择必须完全相同**——这是更严格的门（离散一致性）。
    """
    sys.path.insert(0, os.path.join(ROOT, "bench"))
    import optimize_weights as ow

    pk = Packed(rows)
    rng = np.random.default_rng(seed)
    max_abs = 0.0
    argmax_mismatch = 0
    total = 0
    for t in range(n_trials):
        g = rng.gamma(shape=1.0, size=len(METRICS))
        w = (g / g.sum()).tolist()
        vec_regs = pk.regrets(w)
        sc_regs = [ow.regret_of(r, w) for r in rows]
        for i, (a, b) in enumerate(zip(vec_regs.tolist(), sc_regs)):
            max_abs = max(max_abs, abs(a - b))
            total += 1
            # 离散一致：标量选中的候选下标 == 向量化 argmax
            fvec = [rows[i]["F"][m] for m in METRICS]
            sc_idx = ow.pick_best(fvec, w)
            score = np.einsum("k,kic->ic", w, pk.F)
            vec_idx = int(np.argmax(score[i]))
            if sc_idx != vec_idx:
                argmax_mismatch += 1
    ok = (max_abs <= 1e-12) and (argmax_mismatch == 0)
    if verbose:
        print("  等价性对拍：%d 组权重 × %d 点 = %d 个 regret 比较"
              % (n_trials, len(rows), total))
        print("    max|Δregret| = %.3e（门限 1e-12）" % max_abs)
        print("    argmax 选择不一致 = %d（必须为 0）" % argmax_mismatch)
        print("    ⇒ %s" % ("通过" if ok else "**失败**"))
    return ok, max_abs, argmax_mismatch


def verify_negative_control(rows, verbose=True):
    """阴性对照：故意改一个权重，必须产生可观测的 regret 变化。

    证明对拍器有区分力（不是恒等返回）。
    """
    pk = Packed(rows)
    w0 = [1.0] + [0.0] * (len(METRICS) - 1)
    r0 = pk.regrets(w0)
    w1 = [0.0] * (len(METRICS) - 1) + [1.0]
    r1 = pk.regrets(w1)
    n_diff = int(np.sum(np.abs(r0 - r1) > 1e-12))
    ok = n_diff > 0
    if verbose:
        print("  阴性对照：S0 单指标 vs L 单指标的 regret 不同点 = %d/%d ⇒ %s"
              % (n_diff, len(rows), "有区分力" if ok else "**无区分力**"))
    return ok


# ==================== 搜索 ====================
def random_search_packed(pk, trials, rng):
    """在单纯形上 Dirichlet 采样，返回 train 上 regret 最小的权重。"""
    best_w, best_r = None, None
    d = len(METRICS)
    for _ in range(trials):
        g = rng.gamma(shape=1.0, size=d)
        s = g.sum()
        if s <= 0:
            continue
        w = g / s
        r = pk.mean_regret(w)
        if best_r is None or r < best_r - 1e-15:
            best_w, best_r = w, r
    if best_w is None:
        best_w = np.full(d, 1.0 / d)
    return best_w


def coord_descent_packed(pk, rounds=20, grid=11, init=None):
    d = len(METRICS)
    w = np.array(init, dtype=np.float64) if init is not None else np.full(d, 1.0 / d)
    w = w / w.sum()
    best_r = pk.mean_regret(w)
    n_evals = 0
    for _ in range(rounds):
        improved = False
        for k in range(d):
            others = np.delete(w, k)
            others_sum = others.sum()
            best_local = (best_r, w[k])
            best_cand = w.copy()
            for t in range(grid + 1):
                nk = t / grid
                if nk >= 1.0:
                    continue
                scale = (1.0 - nk) / others_sum if others_sum > 1e-12 else 0.0
                cand = w.copy()
                cand[k] = nk
                for j in range(d):
                    if j != k:
                        cand[j] = w[j] * scale
                r = pk.mean_regret(cand)
                n_evals += 1
                if r < best_local[0] - 1e-15:
                    best_local = (r, nk)
                    best_cand = cand
                    improved = True
            if best_local[0] < best_r - 1e-15:
                best_r = best_local[0]
                w = best_cand
        if not improved:
            break
    return w, n_evals


def loo_random(pk, trials, seed=0, verbose=True, label=""):
    """留一交叉验证：每折在其余点上**独立**随机搜索，在留出点评估。

    代价 = n × trials 次权重评估。
    """
    n = pk.n
    rng = np.random.default_rng(seed)
    regs = np.zeros(n)
    t0 = time.perf_counter()
    all_idx = np.arange(n)
    for i in range(n):
        train_idx = np.delete(all_idx, i)
        pk_tr = pk.subset(train_idx)
        w = random_search_packed(pk_tr, trials, rng)
        regs[i] = float(pk.regrets(w)[i])
        if verbose and (i + 1) % 25 == 0:
            print("      %s LOO %d/%d（%.0fs）"
                  % (label, i + 1, n, time.perf_counter() - t0), flush=True)
    return float(np.mean(regs)), regs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab",
                                                     "mm_discovery.json"))
    ap.add_argument("--trials", type=int, default=800)
    ap.add_argument("--cd-rounds", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--loo", action="store_true", help="跑完整 LOO（昂贵）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    path = args.matrix if os.path.isabs(args.matrix) else os.path.join(ROOT, args.matrix)
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload["rows"]
    print("=" * 100)
    print("向量化权重搜索（等价性已对拍）")
    print("=" * 100)
    print("  matrix = %s，n = %d" % (os.path.basename(path), len(rows)))

    pk = Packed(rows)
    print("  打包：C_max = %d" % pk.C)
    print()
    print("=" * 100)
    print("第 0 步：仪器验证（必须先通过）")
    print("=" * 100)
    ok, max_abs, mism = verify_equivalent(rows)
    neg_ok = verify_negative_control(rows)
    if not (ok and neg_ok):
        print()
        print("  ✘ 仪器验证失败 ⇒ 拒绝出任何读数")
        return 2

    print()
    print("=" * 100)
    print("第 1 步：基线（零参数，无需 LOO）")
    print("=" * 100)
    s0_w = np.zeros(len(METRICS))
    s0_w[0] = 1.0
    s0_in = pk.mean_regret(s0_w)
    eq_w = np.full(len(METRICS), 1.0 / len(METRICS))
    eq_in = pk.mean_regret(eq_w)
    print("    S0 单指标   in-sample regret = %.6f" % s0_in)
    print("    7 指标等权  in-sample regret = %.6f" % eq_in)

    print()
    print("=" * 100)
    print("第 2 步：随机搜索（in-sample）")
    print("=" * 100)
    t0 = time.perf_counter()
    rng = np.random.default_rng(args.seed)
    w_rs = random_search_packed(pk, args.trials, rng)
    rs_in = pk.mean_regret(w_rs)
    print("    trials=%d，评估 %d 次，in-sample regret = %.6f（%.1fs）"
          % (args.trials, args.trials, rs_in, time.perf_counter() - t0))
    print("    权重：%s" % "  ".join("%s=%.4f" % (m, x)
                                     for m, x in zip(METRICS, w_rs)))

    print()
    print("=" * 100)
    print("第 3 步：坐标下降（in-sample）")
    print("=" * 100)
    t0 = time.perf_counter()
    w_cd, cd_evals = coord_descent_packed(pk, rounds=args.cd_rounds, init=w_rs)
    cd_in = pk.mean_regret(w_cd)
    print("    评估 %d 次，in-sample regret = %.6f（%.1fs）"
          % (cd_evals, cd_in, time.perf_counter() - t0))
    print("    权重：%s" % "  ".join("%s=%.4f" % (m, x)
                                     for m, x in zip(METRICS, w_cd)))

    result = {
        "n": len(rows), "metrics": list(METRICS),
        "instrument": {"equivalent": bool(ok), "max_abs": max_abs,
                       "argmax_mismatch": int(mism),
                       "negative_control_ok": bool(neg_ok)},
        "s0_in_sample": s0_in, "eq_in_sample": eq_in,
        "random_search": {"w": w_rs.tolist(), "in_sample": rs_in,
                          "evals": args.trials},
        "coord_descent": {"w": w_cd.tolist(), "in_sample": cd_in,
                          "evals": cd_evals},
        "args": vars(args),
    }

    if args.loo:
        print()
        print("=" * 100)
        print("第 4 步：LOO（每折独立搜索，代价 n×trials）")
        print("=" * 100)
        n_folds = len(rows)
        print("    随机搜索 LOO：%d 折 × %d trials = %d 次评估"
              % (n_folds, args.trials, n_folds * args.trials))
        rs_loo, _ = loo_random(pk, args.trials, seed=args.seed + 1,
                               label="随机搜索")
        print("    **随机搜索 LOO regret = %.6f**（vs S0 %.6f，Δ%+.6f）"
              % (rs_loo, s0_in, rs_loo - s0_in))
        result["random_search"]["loo"] = rs_loo
        result["random_search"]["loo_evals"] = n_folds * args.trials
        result["random_search"]["loo_folds"] = n_folds

    out = args.out or os.path.join("state", "ab", "fast_loo.json")
    out_p = out if os.path.isabs(out) else os.path.join(ROOT, out)
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out_p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
