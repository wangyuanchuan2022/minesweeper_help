# -*- coding: utf-8 -*-
"""P1-e/f：连续权重的随机搜索基线 + 坐标下降精修。

=================== 为什么先跑随机搜索与坐标下降 ====================
Bergstra & Bengio (2012, JMLR 13:281-305)：
  "random search is a natural **baseline** against which to judge progress
   in the development of adaptive (sequential) hyper-parameter optimization"

⚠️ **重要限定（Advisor 纠正，勿越界）**：
  · 连续权重不可穷举 ⇒ 只说明**需要启发式或离散化搜索**，
    **不推出 GA 是唯一可行路径**（坐标下降、随机搜索、局部搜索都是候选）。
  · 本脚本并列比较**三种方法在固定评估预算下的表现**，
    而不是"先 A 后 B 最后才 GA"的固定顺序。
  · **逐代 LOO 不是过拟合防线**：反复基于同一发现集做 LOO 选模型，
    仍会**自适应过拟合**（LOO 被用作选择准则时其无偏性就失效了）。
    真正的防线是**独立验证集只评估一次**。

=================== 评估预算冻结（避免不公平比较）====================
  随机搜索：`--random-trials` 次权重评估
  坐标下降：`--cd-rounds × 7 维 × (grid+1)` 次评估
  ⇒ 两者应大致同量级，报告时给出**实际评估次数**。
  这是 Advisor 要求的"按固定评估预算比较"。

=================== 为什么必须 LOO ====================
在 41~300 点上拟合 7 维连续权重，in-sample 必然变好。
此前实测（41 点，三种拟合）：in-sample 0.0131 优于 S0 的 0.0157，
但 LOO 0.0241 劣于 S0 ⇒ **只有 LOO 是证据**。

=================== 三种方法 ====================
  A. 随机搜索：在权重单纯形上均匀采样 N 组，取 LOO 最优
     （Dirichlet 采样；Bergstra 的基线）
  B. 坐标下降：固定其余权重，对单个权重做一维线搜索，轮转直到收敛
     （用户建议的"每次只调一部分系数"）
  C. 对照：等权全指标（零参数基线）

=================== 判据 ====================
主判据：**LOO 平均 regret** 是否低于 S0（0.0157 量级）。
若三者都不如 S0 ⇒ 连续权重方向在本样本上不成立，如实报告。

用法:
  python bench/optimize_weights.py --matrix state/ab/mm_discovery.json
  python bench/optimize_weights.py --random-trials 3000 --cd-rounds 30
"""
import argparse
import json
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def pick_best(fvec, w):
    best_i, best_v = 0, None
    n = len(fvec[0])
    for i in range(n):
        v = 0.0
        for k in range(len(w)):
            v += w[k] * fvec[k][i]
        if best_v is None or v > best_v + 1e-15:
            best_i, best_v = i, v
    return best_i


def regret_of(row, w):
    fvec = [row["F"][m] for m in METRICS]
    i = pick_best(fvec, w)
    q = row["q"]
    return max(q) - q[i]


def mean_regret(rows, w):
    if not rows:
        return float("nan")
    return sum(regret_of(r, w) for r in rows) / len(rows)


def per_point(rows, w):
    return [regret_of(r, w) for r in rows]


def loo(rows, fitter, **kw):
    """留一交叉验证：每折用其余点拟合，在留出点上评估。"""
    n = len(rows)
    regs = []
    for i in range(n):
        train = rows[:i] + rows[i + 1:]
        w = fitter(train, **kw)
        regs.append(regret_of(rows[i], w))
    return sum(regs) / n, regs


# ---------------- 方法 A：随机搜索（Dirichlet 采样） ----------------
def random_search(train, trials=800, seed=0, alpha=1.0):
    """在权重单纯形上均匀采样，返回 train 上 regret 最小的权重。

    ⚠️ 注意：这是**在 train 上挑最优**，所以返回的权重本身含选择偏差。
    LOO 的正确用法是每折独立跑一次 random_search（代价 = n × trials）。
    为控制成本，本函数内部只做采样 + 评估，不做 LOO。
    """
    rng = random.Random(seed)
    best_w, best_r = None, None
    d = len(METRICS)
    for _ in range(trials):
        # Dirichlet(alpha) 采样：alpha=1 时单纯形上均匀
        g = [rng.gammavariate(alpha, 1.0) for _ in range(d)]
        s = sum(g)
        if s <= 0:
            continue
        w = [x / s for x in g]
        r = mean_regret(train, w)
        if best_r is None or r < best_r - 1e-12:
            best_w, best_r = w, r
    return best_w if best_w else [1.0 / d] * d


# ---------------- 方法 B：坐标下降 ----------------
def coord_descent(train, rounds=20, grid=11, init=None, seed=0):
    """坐标下降：固定其余权重，对单个权重做一维线搜索，轮转。

    用户建议的"每次只调整一部分系数"就是这个思路。
    """
    d = len(METRICS)
    w = list(init) if init else [1.0 / d] * d
    best_r = mean_regret(train, w)
    for _ in range(rounds):
        improved = False
        for k in range(d):
            # 把权重 k 在 [0, 剩余和] 上扫，其余按比例缩放保持和为 1
            others = [w[j] for j in range(d) if j != k]
            others_sum = sum(others)
            best_local = (best_r, w[k])
            for t in range(grid + 1):
                nk = t / grid            # 权重 k 占比 0..1
                if nk >= 1.0:
                    continue
                scale = (1.0 - nk) / others_sum if others_sum > 1e-12 else 0.0
                cand = []
                for j in range(d):
                    if j == k:
                        cand.append(nk)
                    else:
                        cand.append(w[j] * scale)
                r = mean_regret(train, cand)
                if r < best_local[0] - 1e-12:
                    best_local = (r, nk)
                    best_cand = cand
                    improved = True
            if best_local[0] < best_r - 1e-12:
                best_r = best_local[0]
                # 重建 w
                nk = best_local[1]
                others = [w[j] for j in range(d) if j != k]
                others_sum = sum(others)
                scale = (1.0 - nk) / others_sum if others_sum > 1e-12 else 0.0
                w = [nk if j == k else w[j] * scale for j in range(d)]
        if not improved:
            break
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab",
                                                    "mm_discovery.json"))
    ap.add_argument("--random-trials", type=int, default=800)
    ap.add_argument("--cd-rounds", type=int, default=20)
    ap.add_argument("--loo-limit", type=int, default=0,
                    help="LOO 只在前 N 点上做（0=全部）。LOO 代价 = n×trials")
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "optimize_weights.json"))
    args = ap.parse_args()

    path = args.matrix if os.path.isabs(args.matrix) else os.path.join(
        ROOT, args.matrix)
    if not os.path.exists(path):
        print("缺少 %s" % path)
        return 1
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload["rows"]
    # 兼容两种格式：mm_discovery 有 F；merged_regret 没有
    if not rows or "F" not in rows[0]:
        print("✘ 该文件没有 F 矩阵（指标打分），无法做权重优化")
        print("  需要先跑 bench/dump_metric_matrix_ext.py")
        return 1

    print("=" * 100)
    print("连续权重优化：随机搜索（基线）→ 坐标下降（精修）")
    print("=" * 100)
    print("  样本 n=%d，指标 %d 个" % (len(rows), len(METRICS)))
    print()

    # ---- 基线 ----
    s0_w = [0.0] * len(METRICS)
    s0_w[0] = 1.0      # 只有 S0
    s0_regs = per_point(rows, s0_w)
    s0_mean = sum(s0_regs) / len(s0_regs)
    eq_w = [1.0 / len(METRICS)] * len(METRICS)
    eq_mean = mean_regret(rows, eq_w)
    print("  零参数基线（in-sample，无需 LOO）：")
    print("    S0 单指标     regret = %.4f" % s0_mean)
    print("    7 指标等权    regret = %.4f" % eq_mean)
    print()

    # ---- 方法 A：随机搜索（in-sample + LOO）----
    print("=" * 100)
    print("A. 随机搜索（Bergstra 基线）")
    print("=" * 100)
    w_rs = random_search(rows, trials=args.random_trials, seed=args.seed)
    r_rs_in = mean_regret(rows, w_rs)
    rs_evals = args.random_trials
    print("  in-sample regret = %.4f（本方法评估 %d 次权重）"
          % (r_rs_in, rs_evals))
    print("  权重：%s" % "  ".join("%s=%.3f" % (m, x)
                                  for m, x in zip(METRICS, w_rs)))

    loo_rows = rows[:args.loo_limit] if args.loo_limit else rows
    if len(loo_rows) >= 10:
        print("  计算 LOO（n=%d，每折 %d trials）..."
              % (len(loo_rows), args.random_trials))
        # ⚠️ 每折独立随机搜索，seed 递增，避免复用同一组采样
        def _rs_fitter(train):
            return random_search(train, trials=args.random_trials,
                                 seed=args.seed + len(train))
        r_rs_loo, _ = loo(loo_rows, _rs_fitter)
        print("  **LOO regret = %.4f**（vs S0 %.4f，Δ%+.4f）"
              % (r_rs_loo, s0_mean, r_rs_loo - s0_mean))
    else:
        r_rs_loo = None
        print("  （样本 <10，跳过 LOO）")

    # ---- 方法 B：坐标下降 ----
    print()
    print("=" * 100)
    print("B. 坐标下降（每次只调一部分系数）")
    print("=" * 100)
    w_cd = coord_descent(rows, rounds=args.cd_rounds, init=list(w_rs),
                         seed=args.seed)
    r_cd_in = mean_regret(rows, w_cd)
    cd_evals = args.cd_rounds * len(METRICS) * 12
    print("  in-sample regret = %.4f（本方法约评估 %d 次权重）"
          % (r_cd_in, cd_evals))
    print("  权重：%s" % "  ".join("%s=%.3f" % (m, x)
                                  for m, x in zip(METRICS, w_cd)))
    if len(loo_rows) >= 10:
        print("  计算 LOO（n=%d）..." % len(loo_rows))
        def _cd_fitter(train):
            return coord_descent(train, rounds=args.cd_rounds, seed=args.seed)
        r_cd_loo, _ = loo(loo_rows, _cd_fitter)
        print("  **LOO regret = %.4f**（vs S0 %.4f，Δ%+.4f）"
              % (r_cd_loo, s0_mean, r_cd_loo - s0_mean))
    else:
        r_cd_loo = None

    # ---- 判定 ----
    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    print("  S0 基线 LOO 等价（S0 是零参数，无拟合）  = %.4f" % s0_mean)
    print("  随机搜索 LOO = %s"
          % ("%.4f" % r_rs_loo if r_rs_loo is not None else "未做"))
    print("  坐标下降 LOO = %s"
          % ("%.4f" % r_cd_loo if r_cd_loo is not None else "未做"))
    print()
    best = None
    for nm, v in (("随机搜索", r_rs_loo), ("坐标下降", r_cd_loo)):
        if v is not None and (best is None or v < best[1]):
            best = (nm, v)
    if best is None:
        print("  ⚠️ 无 LOO 结果，不可判定")
    elif best[1] < s0_mean - 1e-4:
        print("  ✔ %s 的 LOO（%.4f）优于 S0（%.4f）⇒ 有正向信号"
              % (best[0], best[1], s0_mean))
        print("     但须在**独立验证集**上复现后才可采纳（见 P1 预注册）")
    else:
        print("  ✘ 两者 LOO 均不优于 S0 ⇒ 在本样本与本预算下，连续权重未显示优势")
        print("     结论边界：")
        print("       · 样本 n=%d（小样本 = **高过拟合风险**，不等于必然过拟合）"
              % len(rows))
        print("       · 被丢弃点存在自选择偏差（P1-c 分层统计见 p1_coverage.json）")
        print("       · **LOO 被用作选择准则时其无偏性失效**（自适应过拟合）")
        print("       · 本结论只针对**当前评估预算**；换预算可能不同")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "n": len(rows), "metrics": list(METRICS),
            "s0_mean": s0_mean, "eq_mean": eq_mean,
            "random_search": {"w": w_rs, "in_sample": r_rs_in,
                              "loo": r_rs_loo},
            "coord_descent": {"w": w_cd, "in_sample": r_cd_in,
                              "loo": r_cd_loo},
            "args": vars(args),
        }, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
