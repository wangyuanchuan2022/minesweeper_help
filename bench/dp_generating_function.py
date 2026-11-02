# -*- coding: utf-8 -*-
"""生成函数 DP：修复"各组雷数独立"的错误假设（正确重建 + 暴力枚举对拍）。

问题（已独立复验）：现有 process_bigger_situation 的近似分支对每组独立归一化，
忽略"总雷数"这个跨组耦合约束，实测安全率误差可达 46.67 个百分点。

正确做法：
  设第 g 组的雷数直方图 H_g(m) = 该组雷数为 m 的解数
               H_{g,c}(m) = 该组雷数为 m 且格子 c 为雷的解数
  内部无约束格 U 个，全局总雷数约束下"前沿剩余雷数"需求为 r
  全局权重  W(m_1..m_G) = ∏H_g(m_g) × C(U, R - Σm_g)

  用卷积把 ∏H_g 合并成单一分布 T(t) = Σ_{Σm_g=t} ∏H_g(m_g)
  则每格雷概率 = Σ_m H_{g,c}(m) · Σ_t T_{-g}(t) · C(U, R-m-t)
                / Σ_t T(t) · C(U, R-t)

本脚本先只做"数学正确性"实现 + 暴力对拍，暂不接入主流程。

用法: python bench/dp_generating_function.py
"""
import itertools
import os
import sys
from math import comb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def group_histograms(solutions):
    """返回 (H, Hc)：H[m]=雷数为m的解数；Hc[c][m]=该解数且c为雷的计数。"""
    if not solutions:
        return [], []
    n = len(solutions[0])
    H = {}
    Hc = [dict() for _ in range(n)]
    for sol in solutions:
        m = int(sum(sol))
        H[m] = H.get(m, 0) + 1
        for c, v in enumerate(sol):
            if v:
                Hc[c][m] = Hc[c].get(m, 0) + 1
    return H, Hc


def convolve(hist_list):
    """把多组雷数直方图卷积成全局分布 T(t)。"""
    T = {0: 1}
    for H in hist_list:
        newT = {}
        for t, wt in T.items():
            for m, wm in H.items():
                newT[t + m] = newT.get(t + m, 0) + wt * wm
        T = newT
    return T


def dp_safe_probabilities(groups, U, R):
    """生成函数 DP：返回每个前沿格"不是雷"的概率（与组顺序一致拼接）。

    groups: 每组是解列表（每个解是 0/1 序列）
    U: 无约束内部格数量
    R: 前沿+内部总共还需放置的雷数
    """
    hists = [group_histograms(g) for g in groups]
    Hs = [h[0] for h in hists]
    Hcs = [h[1] for h in hists]

    # 全局分布
    T = convolve(Hs)
    # 分母：Σ_t T(t)·C(U, R-t)
    def feasible(t):
        return 0 <= (R - t) <= U

    denom = sum(wt * comb(U, R - t) for t, wt in T.items() if feasible(t))
    if denom == 0:
        return None, 0

    out = []
    for gi, (H, Hc) in enumerate(zip(Hs, Hcs)):
        # 去掉本组后的分布 T_{-g}
        others = [h for k, h in enumerate(Hs) if k != gi]
        Tm = convolve(others)
        for c in range(len(Hc)):
            num = 0
            for m, wmc in Hc[c].items():
                for t, wt in Tm.items():
                    r = R - m - t
                    if 0 <= r <= U:
                        num += wmc * wt * comb(U, r)
            out.append(1.0 - (num / denom))
    return out, denom


def brute_exact(groups, U, R):
    """暴力：枚举所有组解组合 × 内部格雷数，只保留总雷数==R 的世界，等权求概率。"""
    n_frontier = sum(len(g[0]) for g in groups)
    counts = [0.0] * n_frontier
    total = 0.0
    for combo in itertools.product(*[range(len(g)) for g in groups]):
        fm = sum(int(sum(groups[gi][combo[gi]])) for gi in range(len(groups)))
        need = R - fm
        if not (0 <= need <= U):
            continue
        w = comb(U, need)
        total += w
        pos = 0
        for gi, g in enumerate(groups):
            for v in g[combo[gi]]:
                if v:
                    counts[pos] += w
                pos += 1
    if total == 0:
        return None
    return [1.0 - c / total for c in counts]


def main():
    cases = [
        # (描述, 各组解, 内部格数, 剩余雷数)
        ("双组对称", [[[0, 0, 1], [1, 1, 0]], [[0, 0, 1], [1, 1, 0]]], 2, 3),
        ("偏斜分布", [[[0, 0]], [[0, 0], [1, 1]]], 1, 2),
        ("三组混合", [[[0, 1], [1, 0]], [[0, 0, 1], [1, 1, 0]], [[0], [1]]], 2, 3),
        ("无内部格", [[[0, 1], [1, 0]], [[0, 1]]], 0, 2),
        ("单组", [[[0, 1, 0], [1, 0, 1], [0, 0, 0]]], 3, 2),
    ]
    print("生成函数 DP vs 暴力枚举（正确性对拍）")
    print("=" * 74)
    all_ok = True
    for desc, groups, U, R in cases:
        dp, denom = dp_safe_probabilities(groups, U, R)
        bf = brute_exact(groups, U, R)
        if dp is None or bf is None:
            print(f"  {desc:<12} 无可行世界（DP={dp is not None}, 暴力={bf is not None}）")
            continue
        diff = max(abs(a - b) for a, b in zip(dp, bf)) if dp else 0.0
        ok = diff < 1e-9
        all_ok &= ok
        print(f"  {desc:<12} DP={[round(x,4) for x in dp]}")
        print(f"  {'':<12} 暴力={[round(x,4) for x in bf]}  最大差={diff:.2e} "
              f"{'✅' if ok else '❌'}")

    print("=" * 74)
    print("对照：现有 pbs 近似分支（各组独立归一化，忽略总雷数耦合）")
    from utils.combinatorics import combination_ratio
    for desc, groups, U, R in cases:
        bf = brute_exact(groups, U, R)
        if bf is None:
            continue
        # 复刻现有近似：逐组按 combination_ratio 归一化，a 视为 R+内部已算
        approx = []
        for g in groups:
            row = [0.0] * len(g[0])
            tot = 0.0
            mins = min(int(sum(s)) for s in g)
            x_min = R - mins
            for s in g:
                m = int(sum(s))
                x = R - m
                p = combination_ratio(x, x_min, U) if 0 <= x <= x_min <= U else 0.0
                for c, v in enumerate(s):
                    row[c] += v * p
                tot += p
            approx.extend([1.0 - (r / tot) if tot else r for r in row])
        diff = max(abs(a - b) for a, b in zip(approx, bf))
        print(f"  {desc:<12} 近似最大误差 = {diff*100:6.2f} 个百分点")

    print("=" * 74)
    print(f"DP 实现正确性：{'全部通过 ✅' if all_ok else '存在不一致 ❌'}")


if __name__ == "__main__":
    main()
