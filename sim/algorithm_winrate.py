# -*- coding: utf-8 -*-
"""算法极限胜率：只评估「决策算法」，剔除视觉输入与点击输出。

======================= 口径（写死，避免歧义） =======================
【包含】
  · 项目真实的决策链：number5_1（含分组 / part_solve / win_rate / pbs / DP）
  · 真实棋盘规则：30x16、99 雷、首点 3x3 安全、flood-fill 展开
  · 求解器只能看到人类可见信息（未开/已开数字/剩余雷数）

【排除】
  · 视觉识别（complete_scan 被替换为恒等：直接把真值矩阵给算法，
    因此本测度衡量的是「若识别 100% 正确，算法能到多少胜率」）
  · 点击与等待（不 sleep、不 pyautogui、不走 UI）
  · 任何雷位泄漏（BoardView 里没有雷位字段，有审计）

因此本数字是**算法上限**：视觉/点击再差也不会让这个数变高。
算法改进若有效，这个数字应当上升。

用法:
  python sim/algorithm_winrate.py --games 200
"""
import argparse
import math
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from sim.minesweeper_sim import Board, UNOPENED


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


class AlgorithmSolver:
    """只包决策算法，屏蔽视觉与点击。"""

    def __init__(self, w, h, a, limit=80):
        from utils.solver import Solver
        self.w, self.h, self.a = w, h, a
        self.s = Solver()
        self.s.w = w
        self.s.h = h
        self.s.a = a
        self.s.limit = limit
        self.s.is_play = False
        # 关键：屏蔽视觉重扫，算法直接用我们给的棋盘矩阵
        self.s.complete_scan = lambda cell_value, no_10=True: cell_value

    def decide(self, view):
        """输入人类可见视图，返回 ('open',(x,y))。0-based。"""
        cv = np.zeros((self.h + 2, self.w + 2), np.int32)
        for y in range(self.h):
            for x in range(self.w):
                v = view.cells[y][x]
                cv[y + 1, x + 1] = v if v != UNOPENED else 9
        for (fx, fy) in view.flags:
            if cv[fy + 1, fx + 1] == 9:
                cv[fy + 1, fx + 1] = 10

        self.s.num = 1
        self.s.checked = {}
        self.s.pos_dict_list = []
        self.s.appended_pos = set()
        try:
            self.s.number5_1(cv.copy())
        except Exception:
            return None
        hm = getattr(self.s, "_last_heatmap", {}) or {}
        best = hm.get("best")
        if best is not None:
            bx, by = best
            if 1 <= bx <= self.w and 1 <= by <= self.h and view.cells[by - 1][bx - 1] == UNOPENED:
                return ("open", (bx - 1, by - 1))
        # 兜底：取概率最高且未开的格
        prob = hm.get("prob") or {}
        cand = [(p, v) for p, v in prob.items()
                if 1 <= p[0] <= self.w and 1 <= p[1] <= self.h
                and view.cells[p[1] - 1][p[0] - 1] == UNOPENED]
        if cand:
            p, _ = max(cand, key=lambda kv: kv[1])
            return ("open", (p[0] - 1, p[1] - 1))
        return None


def play_one(w, h, a, rng, solver, trace=False):
    board = Board(w, h, a, rng)
    flags = set()
    x0, y0 = w // 2, h // 2
    board.open(x0, y0)          # 首点 3x3 安全（generate 保证）
    steps = 0
    events = []
    while not board.dead and not board.won and steps < w * h * 2:
        steps += 1
        view = board.view(flags)
        view.audit_no_leak()
        mv = solver.decide(view)
        if mv is None:
            events.append("no_move")
            break
        _, (mx, my) = mv
        if board.cells[my][mx] != UNOPENED:
            u = [(x, y) for y in range(h) for x in range(w)
                 if board.cells[y][x] == UNOPENED]
            if not u:
                break
            mx, my = rng.choice(u)
            events.append("dup_click")
        board.open(mx, my)
    if board.won:
        return True, steps, events
    return False, steps, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    print("=" * 74)
    print("算法极限胜率（不含视觉、不含点击）")
    print(f"棋盘 {args.w}x{args.h}，{args.a} 雷，首点 3x3 安全")
    print(f"每档 {args.games} 局")
    print("=" * 74)

    rng = random.Random(args.seed)
    solver = AlgorithmSolver(args.w, args.h, args.a)
    wins = 0
    total_steps = 0
    anomalies = {}
    t0 = time.time()
    for i in range(args.games):
        ok, steps, ev = play_one(args.w, args.h, args.a, rng, solver)
        wins += ok
        total_steps += steps
        for e in ev:
            anomalies[e] = anomalies.get(e, 0) + 1
        if (i + 1) % max(1, args.games // 10) == 0:
            print(f"  ... {i+1}/{args.games}  当前胜率 {wins/(i+1)*100:.2f}%")
    dt = time.time() - t0

    lo, hi = wilson_ci(wins, args.games)
    print("=" * 74)
    print(f"算法胜率 = {wins}/{args.games} = {wins/args.games*100:.2f}%")
    print(f"95% 置信区间 [{lo*100:.2f}%, {hi*100:.2f}%]")
    print(f"平均步数 {total_steps/args.games:.1f}   用时 {dt:.1f}s")
    if anomalies:
        print(f"异常计数: {anomalies}")
    print("=" * 74)
    print("口径：本数字是「识别 100% 正确」前提下的算法上限；")
    print("      视觉/点击/等待全部排除，因此它只反映决策算法本身。")


if __name__ == "__main__":
    main()
