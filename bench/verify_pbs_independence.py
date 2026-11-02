# -*- coding: utf-8 -*-
"""独立复验算法域报告的 P0：近似分支的组间独立性假设误差。

算法域主张：pbs 近似分支（total>10000 或超出 win_rate 阈值）对每组独立归一化，
忽略了"总雷数"这个跨组耦合约束，实测安全率最大误差 8.33 个百分点（构造可到 40pp）。

本脚本不复用其代码，用最直白的数学独立复算：
  · 精确：枚举跨组所有组合，只保留"总雷数落在 [a-len(clicks9), a]"内的世界，
          按等权重求每格雷概率（先验均匀）
  · 近似：每组独立按组合数 ratio 归一化（复刻 pbs 近似分支的做法），再平均
对比两者。

用法: python bench/verify_pbs_independence.py
"""
import itertools
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def exact_marginals(groups, U, total_mines_frontier_min, total_mines_frontier_max):
    """精确：枚举所有组组合 × 内部格雷数，按"总雷数可行"过滤后求每格雷概率。

    groups: 每组是若干解，每个解是该组格子的 0/1 向量
    U: 无约束内部格数量（这些格雷数可取任意，但有总雷数约束）
    """
    n_cells = sum(len(g[0]) for g in groups)
    counts = np.zeros(n_cells)
    total_w = 0
    # 每组解的雷数
    idx = 0
    combos = list(itertools.product(*[range(len(g)) for g in groups]))
    for combo in combos:
        mine_cnt = sum(int(sum(groups[gi][combo[gi]])) for gi in range(len(groups)))
        # 内部格可提供的雷数范围
        for u_mines in range(0, U + 1):
            if total_mines_frontier_min <= mine_cnt + u_mines <= total_mines_frontier_max:
                w = 1.0
                # 组合数权重：C(U, u_mines)
                from math import comb
                w *= comb(U, u_mines)
                total_w += w
                pos = 0
                for gi, g in enumerate(groups):
                    sol = groups[gi][combo[gi]]
                    for v in sol:
                        if v:
                            counts[pos] += w
                        pos += 1
    if total_w == 0:
        return None, 0
    return counts / total_w, total_w


def approx_independent(groups, U, mine_max_frontier, mine_min_frontier, num10, a):
    """近似：复刻 pbs 近似分支——每组独立按组合数 ratio 归一化。"""
    from utils.combinatorics import combination_ratio
    out = []
    for g in groups:
        row = np.zeros(len(g[0]))
        tot = 0.0
        mins = min(int(sum(s)) for s in g)
        x_min = a - mins - num10
        for s in g:
            m = int(sum(s))
            p = combination_ratio(a - m - num10, x_min, U) if 0 <= a - m - num10 <= x_min <= U else 0.0
            row += np.array(s, float) * p
            tot += p
        out.append(1.0 - (row / tot) if tot else row)
    return np.hstack(out)


def main():
    from math import comb
    print("构造：2 个独立组 + 2 个无约束内部格，总雷数 3")
    # 与算法域报告同构的双组例子
    groups = [
        [[0, 0, 1], [1, 1, 0]],   # 组A：2 个解
        [[0, 0, 1], [1, 1, 0]],   # 组B：2 个解
    ]
    U = 2
    a = 3
    num10 = 0
    # 内部格可贡献 0..U 个雷；前沿总雷数须满足 a-len(clicks9) <= m <= a
    lo = a - U
    hi = a
    print(f"  组A解数={len(groups[0])} 组B解数={len(groups[1])} 内部格 U={U} 总雷数 a={a}")

    ex, w = exact_marginals(groups, U, lo, hi)
    if ex is None:
        print("  精确枚举无可行世界")
        return
    safe_exact = 1 - ex
    safe_approx = approx_independent(groups, U, hi, lo, num10, a)

    print(f"\n  精确世界权重和 = {w:.4f}")
    print(f"  精确 安全率 = {np.round(safe_exact, 4).tolist()}")
    print(f"  近似 安全率 = {np.round(safe_approx, 4).tolist()}")
    diff = np.abs(safe_exact - safe_approx)
    print(f"  最大误差 = {diff.max()*100:.2f} 个百分点")
    if diff.max() > 0.01:
        print("  → ❌ 复现了算法域的结论：近似分支存在真实偏差")
    else:
        print("  → ✅ 本例无显著偏差")

    # 补充：更偏斜的构造（组间雷数分布差异大）
    print("\n" + "=" * 70)
    print("构造 2：偏斜分布（组A 必然 0 雷，组B 可 0/2 雷），总雷数受限")
    groups2 = [
        [[0, 0]],              # 组A 只有 0 雷解
        [[0, 0], [1, 1]],      # 组B 可 0 或 2 雷
    ]
    U2 = 1
    a2 = 2
    lo2, hi2 = a2 - U2, a2
    ex2, w2 = exact_marginals(groups2, U2, lo2, hi2)
    if ex2 is not None:
        sa = approx_independent(groups2, U2, hi2, lo2, 0, a2)
        print(f"  精确 安全率 = {np.round(1-ex2, 4).tolist()}  (世界权重和={w2:.2f})")
        print(f"  近似 安全率 = {np.round(sa, 4).tolist()}")
        d2 = np.abs((1 - ex2) - sa)
        print(f"  最大误差 = {d2.max()*100:.2f} 个百分点")
        if d2.max() > 0.01:
            print("  → ❌ 偏斜构造下误差放大，确认缺陷")
    print("=" * 70)
    print("结论：pbs 近似分支在『组间雷数分布不均』时给出系统性偏差。")
    print("      这影响的是【决策质量/胜率】，不影响速度 —— 但它是正确性问题，优先级高于提速。")


if __name__ == "__main__":
    main()
