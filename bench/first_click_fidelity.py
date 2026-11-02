# -*- coding: utf-8 -*-
"""首点安全区保真度检查：Arbiter 真实规则 vs 模拟器旧假定（3x3）。

问题来源（用户实测观察）：真实开局「很可能只有一个已知格子」。
若首点只保证 **1 格**安全，则首格数字 = 0 的概率只有 ~15%，
其余 ~85% 的局面开局只有 1 个数字格 —— 与用户观察一致。

而旧模拟器假设「首点 3x3 无雷」，这强制首格数字恒为 0，
必然触发 flood-fill 展开一大片 —— 等于每局白送一大片开局信息。

本脚本用**纯组合数学**算两种规则的精确概率，不跑求解器：
  A 真实（Arbiter 延迟布雷：只跳过已开的 1 格）
  B 旧模拟器（首点 3x3 排除）

用法: python bench/first_click_fidelity.py
"""
import math
import sys


def p_first_zero(neighbors, total_cells, mines):
    """首格 8 邻域全部无雷的概率（= 首格数字为 0 的概率，精确组合数）。

    C(N-8, M) / C(N, M)，其中 N=可布雷格数、M=雷数。
    """
    return math.comb(total_cells - neighbors, mines) / math.comb(total_cells, mines)


def main():
    w, h, a = 30, 16, 99
    n_cells = w * h
    # 规则 A：只保证首点本格安全 → 可布雷池 = 全盘 - 1
    poolA = n_cells - 1
    pA = p_first_zero(8, poolA, a)
    # 规则 B：3x3 排除 → 可布雷池 = 全盘 - 9
    poolB = n_cells - 9
    pB = p_first_zero(8, poolB, a)

    print("=" * 72)
    print(f"棋盘 {w}x{h}，{a} 雷，雷密度 {a/n_cells*100:.1f}%")
    print("=" * 72)
    print("首格数字为 0 的概率（= 会触发 flood-fill 展开 = 开局有大片信息）：")
    print(f"  A 真实 Arbiter（只跳过首点 1 格）：{pA*100:6.2f}%")
    print(f"  B 旧模拟器（首点 3x3 排除）      ：{pB*100:6.2f}%")
    print(f"  → 旧模拟器让'有大开局'的概率虚高 {pB/pA:.1f} 倍")
    print()
    print("开局只有一个已知数字格的比例：")
    print(f"  A 真实：{(1-pA)*100:.2f}%  ← 与用户实测观察一致")
    print(f"  B 旧模拟器：{(1-pB)*100:.2f}%  ← 几乎不可能")
    print()

    # 实际平均开局展开面积（用真实布雷 + flood-fill 模拟，多局平均）
    try:
        import os
        ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import random
        from sim.minesweeper_sim import Board

        for tag, exclude3x3 in (("A 真实（只跳过首点1格）", False),
                                ("B 旧模拟器（3x3排除）", True)):
            rng = random.Random(20261102)
            sizes = []
            zeros = 0
            for _ in range(400):
                b = Board(w, h, a, rng)
                # 直接按两种规则布雷
                fx, fy = w // 2, h // 2
                if exclude3x3:
                    safe = {(fx + dx, fy + dy) for dy in (-1, 0, 1)
                            for dx in (-1, 0, 1)
                            if 0 <= fx + dx < w and 0 <= fy + dy < h}
                else:
                    safe = {(fx, fy)}
                pool = [(x, y) for y in range(h) for x in range(w)
                        if (x, y) not in safe]
                b.mines = set(rng.sample(pool, a))
                b.generated = True
                b.opened_count = 0
                b.cells = [[9] * w for _ in range(h)]
                b._flood(fx, fy)
                sizes.append(b.opened_count)
                n0 = b._count(fx, fy)
                if n0 == 0:
                    zeros += 1
            avg = sum(sizes) / len(sizes)
            ones = sum(1 for s in sizes if s == 1)
            print(f"  {tag}: 首点展开格数 均值 {avg:.1f}  "
                  f"（=1格的比例 {ones/len(sizes)*100:.1f}%，"
                  f"首格为0的比例 {zeros/len(sizes)*100:.1f}%）")
    except Exception as e:  # noqa: BLE001
        print(f"实测部分跳过（{e}）")

    print()
    print("结论：真实机制下只保证首点**本格**安全，开局信息量远小于 3x3 排除。")


if __name__ == "__main__":
    main()
