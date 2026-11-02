# -*- coding: utf-8 -*-
"""诊断：求解器踩的雷，是"概率猜测失败"还是"确定推理错误"？

判据（关键区分）：
  · 若踩的格子在**确定性推理**下本应安全（周围约束已完全确定）→ 算法/适配器 BUG
  · 若踩的格子本来就只能靠猜（多解世界）→ 正常概率损失，是胜率的固有上限

诊断时**允许读真值**（仅用于事后评估），但真值在任何时刻都不进入求解器。

用法: python sim/diagnose_engine.py --games 4
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
from sim.engine_adapter import EngineSolver


def deterministically_safe(board, flags):
    """用纯逻辑推出"确定安全"的格子集合（人类可复现）。"""
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
                    for p in safe:
                        pass
                    for p in u:
                        if p not in safe:
                            safe.add(p); changed = True
    return safe - flags, mines - flags


def run_game(seed, w, h, a, verbose=True):
    rng = random.Random(seed)
    board = Board(w, h, a, rng)
    flags = set()
    engine = EngineSolver(w, h, a)
    board.open(w // 2, h // 2)

    stats = {"steps": 0, "guesses": 0, "guess_mine": 0,
             "safe_opens": 0, "safe_mine": 0}
    first_bad = None

    for step in range(2000):
        if board.dead or board.won:
            break
        stats["steps"] += 1
        view = board.view(flags)
        view.audit_no_leak()
        mv = engine.decide(view)
        if mv is None:
            break
        kind, (mx, my) = mv
        if kind == "flag":
            flags.add((mx, my)); continue
        if board.cells[my][mx] != UNOPENED:
            break

        # 诊断：该格在纯逻辑下是否确定安全
        det_safe, det_mines = deterministically_safe(board, flags)
        is_det_safe = (mx, my) in det_safe
        truly_mine = (mx, my) in board.mines

        if is_det_safe:
            stats["safe_opens"] += 1
            if truly_mine:
                stats["safe_mine"] += 1
                if first_bad is None:
                    first_bad = (step, mx, my, "确定安全却踩雷")
        else:
            stats["guesses"] += 1
            if truly_mine:
                stats["guess_mine"] += 1

        board.open(mx, my)

    if verbose:
        print(f"  seed={seed} 步数={stats['steps']} 胜={board.won} "
              f"雷={board.dead}")
        print(f"    确定安全格点击 {stats['safe_opens']} 次，其中踩雷 "
              f"{stats['safe_mine']} 次")
        print(f"    猜测点击     {stats['guesses']} 次，其中踩雷 "
              f"{stats['guess_mine']} 次")
        if first_bad:
            print(f"    ❌ 首个确定性误判: {first_bad}")
    return stats, board.won, first_bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    tot = {"safe_opens": 0, "safe_mine": 0, "guesses": 0, "guess_mine": 0}
    wins = 0
    bads = 0
    print(f"诊断 {args.games} 局（{args.w}x{args.h}, {args.a} 雷）")
    print("=" * 68)
    for g in range(args.games):
        st, won, bad = run_game(args.seed + g, args.w, args.h, args.a)
        wins += won
        bads += (bad is not None)
        for k in tot:
            tot[k] += st[k]

    print("=" * 68)
    print(f"胜 {wins}/{args.games}")
    print(f"确定安全格：点击 {tot['safe_opens']} 次，踩雷 {tot['safe_mine']} 次")
    print(f"概率猜测  ：点击 {tot['guesses']} 次，踩雷 {tot['guess_mine']} 次")
    if tot["safe_opens"]:
        print(f"确定推理准确率 = "
              f"{100*(1-tot['safe_mine']/tot['safe_opens']):.4f}%")
    if tot["guesses"]:
        print(f"猜测命中率     = "
              f"{100*(1-tot['guess_mine']/tot['guesses']):.2f}%（期望值越高越好）")
    print()
    if tot["safe_mine"] > 0:
        print("❌ 存在确定性推理误判 → 算法或适配器有 BUG")
    else:
        print("✅ 无确定性误判：所有踩雷都发生在只能猜的局面（属概率损失）")


if __name__ == "__main__":
    main()
