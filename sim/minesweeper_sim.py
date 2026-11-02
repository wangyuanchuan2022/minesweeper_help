# -*- coding: utf-8 -*-
"""扫雷模拟环境（Minesweeper Arbiter 规则忠实复刻）+ 信息隔离。

=================== 信息隔离契约（本文件最重要的一条） ===================
求解器只能拿到 BoardView（人类能看到的信息）：
    · 每格状态：未开(9) / 已开数字(0-8) / 标记(10)
    · 总雷数 a、棋盘尺寸 w/h
    · 剩余雷数 = a - 已标记数
它**不能**拿到真实雷位。真实雷位只存在于 Board._mines，且：
    1. BoardView 是通过 copy 生成的独立对象，不含任何雷位字段
    2. 求解期间 board 对象不会以任何形式传入求解器
    3. 审计钩子 audit_view() 可验证视图里确实没有雷位泄漏
======================================================================

规则（与 Arbiter 一致的关键点）：
    · 首点安全：第一次左键点击的格子保证不是雷，且（专家局常规做法）该点及
      其 8 邻域不含雷 —— Arbiter 默认首点开出的区域不可能是数字>0
    · 数字 = 3x3 邻域雷数；点开数字 0 时 flood-fill 展开
    · 踩雷即负；所有非雷格开完即胜
    · 剩余雷数 = 总雷数 - 已标记（标记由求解器自行维护，不影响真实棋盘）

用法:
    python sim/minesweeper_sim.py --games 200 --w 30 --h 16 --a 99
"""
import argparse
import random
import sys
import time
from dataclasses import dataclass, field

# 格子编码（与项目 utils 一致）
UNOPENED = 9
FLAG = 10
MINE = -1  # 仅内部真实棋盘使用


class BoardView:
    """人类可见的棋盘视图（求解器唯一允许访问的对象）。

    刻意不提供任何访问真实雷位的接口；copy() 产出新对象。
    """

    __slots__ = ("w", "h", "a", "cells", "flags")

    def __init__(self, w, h, a, cells, flags):
        self.w = w
        self.h = h
        self.a = a
        self.cells = cells          # list[list[int]] 9=未开，0..8=已开数字
        self.flags = flags          # set[(x,y)] 求解器自己标记的雷

    def copy(self):
        return BoardView(self.w, self.h, self.a,
                         [row[:] for row in self.cells], set(self.flags))

    @property
    def remaining_mines(self):
        """剩余雷数（人类可以从计数器读到）。"""
        return self.a - len(self.flags)

    def unopened(self):
        out = []
        for y in range(self.h):
            for x in range(self.w):
                if self.cells[y][x] == UNOPENED:
                    out.append((x, y))
        return out

    def opened(self):
        out = []
        for y in range(self.h):
            for x in range(self.w):
                if 0 <= self.cells[y][x] <= 8:
                    out.append((x, y))
        return out

    def audit_no_leak(self):
        """审计：视图里不得存在任何能暴露真实雷位的字段/值。"""
        for name in dir(self):
            if name in ("cells", "flags", "w", "h", "a"):
                continue
            if name.startswith("_") and any(k in name.lower() for k in
                                            ("mine", "truth", "secret", "answer")):
                raise AssertionError(f"视图泄漏字段: {name}")
        for row in self.cells:
            for v in row:
                if v not in (UNOPENED,) and not (0 <= v <= 8):
                    raise AssertionError(f"cells 中出现非法值 {v}（疑似泄漏真值）")
        return True


class Board:
    """真实棋盘（含雷位）。求解器绝不能持有本对象。"""

    def __init__(self, w, h, a, rng):
        self.w, self.h, self.a = w, h, a
        self.rng = rng
        self.mines = set()
        self.cells = [[UNOPENED] * w for _ in range(h)]
        self.generated = False
        self.opened_count = 0
        self.flagged = set()
        self.dead = False

    # ---------------- 首点安全生成 ----------------
    def generate(self, first_x, first_y):
        """按 Arbiter 规则生成：首点及其 8 邻域不含雷。"""
        safe = set()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                x, y = first_x + dx, first_y + dy
                if 0 <= x < self.w and 0 <= y < self.h:
                    safe.add((x, y))
        pool = [(x, y) for y in range(self.h) for x in range(self.w)
                if (x, y) not in safe]
        die = len(pool) < self.a or self.a > self.w * self.h - len(safe)
        if die:
            raise ValueError("雷数过多：首点安全区内无法容纳")
        self.mines = set(self.rng.sample(pool, self.a))
        self.generated = True

    # ---------------- 数字计算 ----------------
    def _is_mine(self, x, y):
        return (x, y) in self.mines

    def _count(self, x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.w and 0 <= ny < self.h and self._is_mine(nx, ny):
                    c += 1
        return c

    # ---------------- 玩家动作 ----------------
    def open(self, x, y):
        """左键点开。返回是否踩雷。"""
        if self.dead:
            return True
        if not self.generated:
            self.generate(x, y)
        if (x, y) in self.flagged:
            return False
        if self.cells[y][x] != UNOPENED:
            return False
        if self._is_mine(x, y):
            self.dead = True
            return True
        self._flood(x, y)
        return False

    def _flood(self, x, y):
        """展开：0 格触发 flood-fill（与扫雷标准规则一致）。"""
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if not (0 <= cx < self.w and 0 <= cy < self.h):
                continue
            if self.cells[cy][cx] != UNOPENED:
                continue
            if self._is_mine(cx, cy):
                continue
            n = self._count(cx, cy)
            self.cells[cy][cx] = n
            self.opened_count += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((cx + dx, cy + dy))

    def toggle_flag(self, x, y):
        if (x, y) in self.flagged:
            self.flagged.discard((x, y))
        else:
            self.flagged.add((x, y))

    @property
    def won(self):
        return self.opened_count == self.w * self.h - self.a

    # ---------------- 视图导出 ----------------
    def view(self, solver_flags=None):
        """导出人类可见视图。solver_flags 由求解器维护，不带任何真值。"""
        cells = [row[:] for row in self.cells]
        return BoardView(self.w, self.h, self.a, cells,
                         set(solver_flags) if solver_flags else set())


def play_random_first(board, rng):
    """按 Arbiter 习惯：首点取棋盘中央附近。"""
    x0, y0 = board.w // 2, board.h // 2
    board.open(x0, y0)
    return x0, y0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    wins = 0
    for g in range(args.games):
        b = Board(args.w, args.h, args.a, rng)
        play_random_first(b, rng)
        # 这里只验证引擎自身：随机点未开格，量化"无智能"基线
        while not b.dead and not b.won:
            u = [(x, y) for y in range(b.h) for x in range(b.w)
                 if b.cells[y][x] == UNOPENED]
            if not u:
                break
            x, y = rng.choice(u)
            b.open(x, y)
        if b.won:
            wins += 1
    print(f"随机基线：{wins}/{args.games} = {wins/args.games*100:.2f}% 胜率")
    print("（该数字仅用于确认引擎可跑通，不代表任何智能策略）")


if __name__ == "__main__":
    main()
