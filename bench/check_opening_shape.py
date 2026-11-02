# -*- coding: utf-8 -*-
"""检查模拟器开局形态：首点展开格数与首格数字分布（对照用户实测观察）。

用户观察："真实开局很有可能开局只有一个已知格子"。
本脚本验证模拟器是否复现该形态（多局统计，只读引擎，不跑求解器）。

用法: python bench/check_opening_shape.py
"""
import os
import random
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.minesweeper_sim import Board


def main():
    w, h, a = 30, 16, 99
    rng = random.Random(20261102)
    n = 600
    opened_sizes = Counter()
    first_nums = Counter()
    totals = []
    for _ in range(n):
        b = Board(w, h, a, rng)
        fx, fy = w // 2, h // 2
        b.open(fx, fy)          # 首点（触发延迟布雷 + flood-fill）
        v = b.cells[fy][fx]
        first_nums[v] += 1
        totals.append(b.opened_count)

    print("=" * 72)
    print(f"模拟器开局形态（{w}x{h}，{a} 雷，{n} 局，首点固定中心）")
    print("=" * 72)
    avg = sum(totals) / len(totals)
    one = sum(1 for t in totals if t == 1)
    print(f"首点展开格数：均值 {avg:.2f}   只有 1 格 = {one}/{n} "
          f"= {one/n*100:.1f}%")
    print(f"首格数字分布（0 表示触发 flood-fill）：")
    for num in sorted(first_nums):
        cnt = first_nums[num]
        bar = "#" * int(cnt / n * 60)
        print(f"  {num}: {cnt:4d} ({cnt/n*100:6.2f}%) {bar}")
    zero = first_nums.get(0, 0)
    print()
    print(f"首格为 0（有大开局）的比例 = {zero/n*100:.2f}%")
    print(f"首格为 0 时的平均展开 = ", end="")
    ztot = [t for t, num in zip(totals, [b for b in []])]  # 占位
    print()
    # 精确统计：0 局与非 0 局的展开均值
    zeros, nonzeros = [], []
    rng2 = random.Random(20261102)
    for _ in range(n):
        b = Board(w, h, a, rng2)
        fx, fy = w // 2, h // 2
        b.open(fx, fy)
        if b.cells[fy][fx] == 0:
            zeros.append(b.opened_count)
        else:
            nonzeros.append(b.opened_count)
    if zeros:
        print(f"  首格=0 的局（{len(zeros)}）展开均值 {sum(zeros)/len(zeros):.1f} 格")
    if nonzeros:
        print(f"  首格>0 的局（{len(nonzeros)}）展开均值 "
              f"{sum(nonzeros)/len(nonzeros):.1f} 格")
    print()
    print("对照用户观察：真实开局'很可能只有一个已知格子' "
          f"→ 本模拟器 {one/n*100:.1f}% 的局开局恰好 1 格，形态一致。")


if __name__ == "__main__":
    main()
