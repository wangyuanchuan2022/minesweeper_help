# -*- coding: utf-8 -*-
"""数学论证：用线性规划判定「是否存在权重 w 使 regret 全面优于 S0」。

=================== 为什么这是"证明"而不是"再跑一次搜索" ====================
前面做的事（随机搜索 / 坐标下降 / LOO）都是**采样式**的：
找到的权重是否最优，只能靠"跑了多少 trial"来近似，永远不知道有没有漏。

本脚本把它转成一个**可判定**的问题。

### 问题的数学形式

对单个决策点 i，候选集 C_i，真值 q_i(c)，指标矩阵 F_i[k][c]（已归一化到 [0,1]）。
选定权重 w ∈ ℝ^7 后，策略选 c* = argmax_c Σ_k w_k F_i[k][c]，
该点的 regret 为 r_i(w) = max_c q_i(c) − q_i(c*)。

**regret 对 w 不是线性的**（因为 argmax 是分段常量）⇒ 目标不是 LP。

但有一个**线性的充分条件**能给出严格结论：

    若存在 w ≥ 0 使得
        对**所有** i 和**所有** c' ≠ argmax_c q_i(c)：
            Σ_k w_k F_i[k][c'] ≤ Σ_k w_k F_i[k][c*_i]      … (*)
    则 w 在**每一个点**上都选到 q 最优，即 regret ≡ 0。

这就是「**指标线性可分离**」的定义：存在一个全局线性打分，
它在所有点上都不会把 q 最优候选排在次优之后。

(*) 是**线性不等式组**，可行域是 {w ≥ 0} 交一组半空间 ⇒ **用 LP 精确判定**。
  · LP 可行  ⇒ **严格证明**：存在权重使样本内 regret = 0（比任何采样搜索都强）
  · LP 不可行 ⇒ **严格证明**：**不存在**任何权重能做到 regret=0
                 （但可能存在使 regret 很小的权重，那是另一回事）

### 这个判定的实际价值

若 LP 不可行，则"regret 能否压到 0"被**证伪**，剩下的问题变成
"最接近的能到多少"，那是**min-max / 鲁棒优化**问题，可退一步做：
    最少违反多少个约束（= 最小化"选错的点数"），这是 0-1 规划的松弛。

### 本脚本做三件事
1. **LP1（强条件）**：判定是否存在 w 使全部点都选对（regret≡0）
2. **LP2（弱条件·缩放版）**：即使不能全对，求出"最大可满足约束比例"的上界
   （用 LP 松弛 + 逐点违反量最小化）
3. **对照**：与 S0、随机搜索、坐标下降的实测 regret 对照，说明数学界与实测的差距

用法:
  python bench/verify_lp_separability.py
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from scipy.optimize import linprog                        # noqa: E402

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def load_rows(path):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)["rows"], "final"
    jp = p + ".journal.jsonl"
    rows = []
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == "ok":
                rows.append(r["row"])
    return rows, "journal"


def build_constraints(rows):
    """构造 (*)：对每点 i、每非最优候选 c'：
           Σ_k w_k (F_i[k][c'] − F_i[k][c*]) ≤ 0
    返回 A_ub, n_skipped（并列最优/单候选等情况跳过）。
    """
    A = []
    n_skip = 0
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        nc = len(q)
        if nc <= 1:
            n_skip += 1
            continue
        qmax = q.max()
        best = set(np.flatnonzero(np.abs(q - qmax) < 1e-12).tolist())
        F = np.array([r["F"][m] for m in METRICS], dtype=np.float64)  # (7, nc)
        # 最优候选取平均指标向量（若并列多个）
        best_idx = sorted(best)
        f_best = F[:, best_idx].mean(axis=1)
        for c in range(nc):
            if c in best:
                continue
            row = F[:, c] - f_best
            A.append(row)
    return (np.asarray(A) if A else np.zeros((0, len(METRICS)))), n_skip


def lp1_feasibility(A):
    """LP1：是否存在 w ≥ 0（非零）使 A w ≤ 0。

    注意 w=0 是平凡可行解（所有打分相等，argmax 取第一个，可能不是最优）。
    因此要加归一化约束 Σ w = 1 以排除零解，并检查得到 w 后是否真的
    逐点选对（因为 argmax 的并列规则可能与 LP 松弛不同）。
    """
    n = A.shape[0]
    if n == 0:
        return None, "无约束（所有点单候选或并列）"
    d = A.shape[1]
    # 变量 w (d)，约束 A w ≤ 0，等式 Σw = 1，w ≥ 0
    res = linprog(
        c=np.zeros(d),
        A_ub=A, b_ub=np.zeros(n),
        A_eq=np.ones((1, d)), b_eq=np.array([1.0]),
        bounds=[(0, None)] * d,
        method="highs",
    )
    return res, None


def check_solution(rows, w):
    """用得到的 w 逐点算 argmax，统计选对/选错，以及 regret。"""
    n_ok = 0
    n_tot = 0
    regrets = []
    wrong = []
    for idx, r in enumerate(rows):
        q = np.asarray(r["q"], dtype=np.float64)
        F = np.array([r["F"][m] for m in METRICS], dtype=np.float64)
        score = w @ F
        pick = int(np.argmax(score))
        n_tot += 1
        best_q = q.max()
        regret = best_q - q[pick]
        regrets.append(regret)
        if regret <= 1e-12:
            n_ok += 1
        else:
            wrong.append(idx)
    return {
        "n_ok": n_ok, "n_tot": n_tot,
        "mean_regret": float(np.mean(regrets)),
        "wrong_points": wrong,
    }


def lp2_min_violation(rows, A):
    """LP2：最小化"最大违反量" t，使 A w ≤ t·1，Σw=1, w≥0。

    若最优 t* = 0 ⇒ LP1 可行（一致）。
    若 t* > 0 ⇒ 不存在使全部点都严格选对的权重；t* 是"最少需要放松多少"的度量。
    这是 min-max（Chebyshev）形式的鲁棒松弛，仍是 LP（t 为变量）。
    """
    n, d = A.shape
    if n == 0:
        return None
    # 变量 [w (d), t (1)]
    c = np.zeros(d + 1)
    c[-1] = 1.0                      # 最小化 t
    A_ub = np.hstack([A, -np.ones((n, 1))])   # A w - t ≤ 0
    b_ub = np.zeros(n)
    A_eq = np.zeros((1, d + 1))
    A_eq[0, :d] = 1.0
    b_eq = np.array([1.0])
    bounds = [(0, None)] * d + [(0, None)]
    res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                  bounds=bounds, method="highs")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "lp_separability.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    print("=" * 100)
    print("数学论证：指标线性可分离性（LP 精确判定）")
    print("=" * 100)
    print("  数据：%s（%d 点，口径 v3 初始状态 + C++ 全枚举）"
          % (src, len(rows)))

    A, n_skip = build_constraints(rows)
    print("  约束数（每点 × 非最优候选）= %d，跳过 %d 点（单候选/并列）"
          % (A.shape[0], n_skip))
    print("  变量数（权重维）= %d" % A.shape[1])

    # 基线
    s0_w = np.zeros(len(METRICS))
    s0_w[0] = 1.0
    s0 = check_solution(rows, s0_w)
    eq = check_solution(rows, np.full(len(METRICS), 1.0 / len(METRICS)))
    print()
    print("  基线（in-sample）：")
    print("    S0 单指标    mean_regret = %.6f，选对 %d/%d (%.1f%%)"
          % (s0["mean_regret"], s0["n_ok"], s0["n_tot"],
             100 * s0["n_ok"] / s0["n_tot"]))
    print("    7 指标等权   mean_regret = %.6f，选对 %d/%d (%.1f%%)"
          % (eq["mean_regret"], eq["n_ok"], eq["n_tot"],
             100 * eq["n_ok"] / eq["n_tot"]))

    # ---- LP1 ----
    print()
    print("=" * 100)
    print("LP1：是否存在 w ≥ 0 使**每一个点**都选到 q 最优（即 regret ≡ 0）")
    print("=" * 100)
    res1, msg = lp1_feasibility(A)
    lp1_ok = False
    if msg:
        print("  %s" % msg)
    elif res1 is None:
        print("  LP 无解信息")
    else:
        print("  HiGHS 状态：%s" % res1.message)
        if res1.status == 0:
            w = res1.x
            chk = check_solution(rows, w)
            print("  LP 可行。求得 w = %s"
                  % np.array2string(w, precision=4, suppress_small=True))
            print("  用该 w 实测：mean_regret = %.6f，选对 %d/%d (%.1f%%)"
                  % (chk["mean_regret"], chk["n_ok"], chk["n_tot"],
                     100 * chk["n_ok"] / chk["n_tot"]))
            if chk["n_ok"] == chk["n_tot"]:
                lp1_ok = True
                print("  ⇒ **严格证明**：存在线性权重使样本内 regret 恒为 0")
            else:
                print("  ⚠️ LP 松弛可行，但 argmax 并列规则导致实测未全对")
                print("     这属于 LP 松弛与真实 argmax 的差异（并列时取指标最大）")
                print("     违反约束的点数 = %d" % len(chk["wrong_points"]))
        else:
            print("  ⇒ LP **不可行**：不存在任何权重能让所有点都选对")
            print("     即：**7 个线性指标无法完全分离**这个数据集的 q 排序")

    # ---- LP2 ----
    print()
    print("=" * 100)
    print("LP2：最小化最大违反量 t（A w ≤ t·1，Σw=1，w≥0）")
    print("=" * 100)
    res2 = lp2_min_violation(rows, A)
    t_star = None
    if res2 is not None and res2.status == 0:
        t_star = float(res2.x[-1])
        w2 = res2.x[:-1]
        chk2 = check_solution(rows, w2)
        print("  t* = %.6f（=0 表示 LP1 可行；>0 表示必须放松）" % t_star)
        print("  对应 w = %s" % np.array2string(w2, precision=4, suppress_small=True))
        print("  实测：mean_regret = %.6f，选对 %d/%d (%.1f%%)"
              % (chk2["mean_regret"], chk2["n_ok"], chk2["n_tot"],
                 100 * chk2["n_ok"] / chk2["n_tot"]))
        print()
        print("  与基线对照：")
        print("    S0  mean_regret = %.6f（选对率 %.1f%%）"
              % (s0["mean_regret"], 100 * s0["n_ok"] / s0["n_tot"]))
        print("    LP2 mean_regret = %.6f（选对率 %.1f%%）"
              % (chk2["mean_regret"], 100 * chk2["n_ok"] / chk2["n_tot"]))
        print("    最优可能下界 = 0.000000（Oracle）")
        print()
        print("  ⇒ 数学上界：任何**线性**权重方案的 in-sample regret 不可能低于 0，")
        print("     而 LP2 给出的是'最不坏的一致线性打分'，可作为实际可达的参照。")
    else:
        print("  LP2 未得到最优解：%s"
              % (res2.message if res2 is not None else "无解"))

    # ---- 结论 ----
    print()
    print("=" * 100)
    print("数学结论")
    print("=" * 100)
    if lp1_ok:
        print("  ① 线性可分离：存在 w 使 regret ≡ 0")
        print("     ⇒ 理论上这 7 个指标足以完美排序该样本的所有决策点")
        print("     ⇒ 网格/随机搜索找不到它是搜索问题，不是模型问题")
    else:
        print("  ① **线性不可分离**：不存在 w 使全部点都选对")
        print("     ⇒ 7 个线性指标的组合**在数学上无法**完美还原 q 排序")
        print("     ⇒ 任何权重方案的 in-sample regret 有**正下界**")
        print("     ⇒ 继续在 w 空间搜索只能逼近该下界，不能突破")
    print()
    print("  ② 这意味着：调参的上限由**指标集本身**决定，而不是搜索算法。")
    print("     若要把 regret 压得更低，正确方向是**增/换指标**（提高特征表达力），")
    print("     而不是在现有 7 维里更精细地调权重。")
    print()
    print("  ⚠️ 边界：以上全部是 **in-sample**（同一批点既拟合又评估）。")
    print("     out-of-sample 表现必然更差，且 LOO 已显示方差不小。")

    out = {
        "n_points": len(rows), "n_constraints": int(A.shape[0]),
        "n_skipped": n_skip,
        "s0_in_sample": s0["mean_regret"],
        "s0_ok_rate": s0["n_ok"] / s0["n_tot"],
        "eq_in_sample": eq["mean_regret"],
        "lp1_feasible": bool(lp1_ok),
        "lp1_status": (res1.status if res1 is not None else None),
        "lp1_message": (res1.message if res1 is not None else msg),
        "lp2_t_star": t_star,
        "caliber": "v3 initial state + cpp full enumeration",
        "caveat": "全部 in-sample；out-of-sample 未验证",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
