# -*- coding: utf-8 -*-
"""首点规则对比实验：Arbiter 的"首点安全区"到底值多少胜率。

背景（逆向结论）：ms_arbiter.exe 脱壳后确认为 Borland Delphi 程序，
雷位由 CryptGenRandom 产生（FUN_004d47ac 是唯一 RNG 包装，唯一调用者是
初始化 FUN_0052dd48）。因此雷位本身不可预测；"有规律"的部分只能是
**生成时的排除约束**，最典型就是首点安全区。

本脚本量化三种首点规则对胜率的影响：
  R0 纯随机布雷，首点不保证安全（首点可能直接踩雷）
  R1 首点安全（首点格及其 8 邻域无雷）—— Arbiter/WinMines 常规做法
  R2 首点安全区更大（首点 5x5 无雷）—— 部分变种的"保证开局"

用法: python sim/first_click_rules.py --games 60 --policy logic
"""
import argparse
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from sim.minesweeper_sim import Board, UNOPENED


class BoardVariant(Board):
    """支持多种首点规则的棋盘。"""

    def generate(self, first_x, first_y, rule="safe3x3"):
        if rule == "random":
            safe = set()
        elif rule == "safe3x3":
            safe = {(first_x + dx, first_y + dy)
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                    if 0 <= first_x + dx < self.w and 0 <= first_y + dy < self.h}
        elif rule == "safe5x5":
            safe = {(first_x + dx, first_y + dy)
                    for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2)
                    if 0 <= first_x + dx < self.w and 0 <= first_y + dy < self.h}
        else:
            raise ValueError(rule)
        pool = [(x, y) for y in range(self.h) for x in range(self.w)
                if (x, y) not in safe]
        if len(pool) < self.a:
            raise ValueError("雷数过多")
        self.mines = set(self.rng.sample(pool, self.a))
        self.generated = True


def solve_by_logic(board, flags):
    """纯逻辑推理（人类可复现）。返回 (是否有进展, 安全格, 雷格)。"""
    w, h, cells = board.w, board.h, board.cells
    nbrs = {}
    for y in range(h):
        for x in range(w):
            nbrs[(x, y)] = [(nx, ny) for ny in range(y - 1, y + 2)
                            for nx in range(x - 1, x + 2)
                            if 0 <= nx < w and 0 <= ny < h and (nx, ny) != (x, y)]
    safe, mines = set(), set()
    changed = True
    while changed:
        changed = False
        for y in range(h):
            for x in range(w):
                v = cells[y][x]
                if not (0 <= v <= 8):
                    continue
                adj = nbrs[(x, y)]
                f = [p for p in adj if p in flags]
                u = [p for p in adj if cells[p[1]][p[0]] == UNOPENED and p not in flags]
                if not u:
                    continue
                if v - len(f) == len(u):
                    for p in u:
                        if p not in mines:
                            mines.add(p); changed = True
                elif v == len(f):
                    for p in u:
                        if p not in safe:
                            safe.add(p); changed = True
    return sorted(safe - flags), sorted(mines - flags)


def play(w, h, a, rng, rule, use_probab_guess=True):
    b = BoardVariant(w, h, a, rng)
    flags = set()
    x0, y0 = w // 2, h // 2
    b.generate(x0, y0, rule)
    b.open(x0, y0)
    if b.dead:
        return False, "首点踩雷"

    for _ in range(w * h * 4):
        if b.dead or b.won:
            break
        safe, mines = solve_by_logic(b, flags)
        for p in mines:
            flags.add(p)
        if safe:
            p = safe[0]
            b.open(*p)
            continue
        # 无确定推理 → 按剩余雷密度猜（比纯随机略好的"人类下限"）
        u = [(x, y) for y in range(h) for x in range(w)
             if b.cells[y][x] == UNOPENED and (x, y) not in flags]
        if not u:
            break
        # 简化：均匀随机（不做概率计算），代表"纯逻辑+瞎猜"
        p = rng.choice(u)
        b.open(*p)
    return b.won, ("胜" if b.won else "踩雷" if b.dead else "未完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print(f"棋盘 {args.w}x{args.h}, {args.a} 雷, 每档 {args.games} 局")
    print("策略：纯逻辑推理 + 无解时均匀随机猜（人类下限）")
    print("=" * 70)
    for rule in ("random", "safe3x3", "safe5x5"):
        rng = random.Random(args.seed)
        wins = 0
        reasons = {}
        for _ in range(args.games):
            ok, why = play(args.w, args.h, args.a, rng, rule)
            wins += ok
            reasons[why] = reasons.get(why, 0) + 1
        print(f"  {rule:<10} 胜率 {wins}/{args.games} = {wins/args.games*100:6.2f}%   "
              f"分布 {reasons}")


if __name__ == "__main__":
    main()
