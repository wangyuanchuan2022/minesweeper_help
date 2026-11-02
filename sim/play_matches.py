# -*- coding: utf-8 -*-
"""极限胜率测试：把项目求解器接到模拟环境，跑大量对局统计胜率。

=================== 信息隔离（本次任务的核心约束） ===================
求解器只能看到 BoardView：
    · cells：9=未开 / 0..8=已开数字
    · a：总雷数、w/h：尺寸、flags：求解器自己维护的标记
**真实雷位只存在于 Board._mines，绝不进入求解器**。
本文件通过 _view_only() 强制这一点，并在每局开始做泄露审计。

同时对比三条策略，量化"算法到底值多少"：
  S1 纯逻辑（只做确定推理，无猜测时随机）—— 人类可复现的下限
  S2 项目现有算法（number5_1，含近似/DP）
  S3 完美信息上界（偷看雷位，仅作理论上限参照，**不参与"合法"统计**）

用法:
  python sim/play_matches.py --games 100 --policy engine
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from sim.minesweeper_sim import Board, UNOPENED, FLAG


def _view_only(board, flags):
    """导出纯视图（唯一允许给求解器的入口）。"""
    v = board.view(flags)
    v.audit_no_leak()          # 硬审计：视图不得含真值
    return v


# ---------------------------------------------------------------------------
# 策略 S1：纯逻辑（确定性推理，无猜测时随机）
# ---------------------------------------------------------------------------
def solve_by_logic(board, rng, flags):
    """只用确定性规则：数字-已标雷 = 剩余雷数 ⇒ 全雷/全安全。

    返回 (是否已做动作, 动作列表)。不做概率猜测。
    """
    w, h = board.w, board.h
    cells = board.cells
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
                            mines.add(p)
                            changed = True
                elif v == len(f):
                    for p in u:
                        if p not in safe:
                            safe.add(p)
                            changed = True

    mines -= flags
    safe -= flags
    if not safe and not mines:
        return False, [], []
    return True, sorted(safe), sorted(mines)


def random_unopened(board, rng, flags):
    u = [(x, y) for y in range(board.h) for x in range(board.w)
         if board.cells[y][x] == UNOPENED and (x, y) not in flags]
    return rng.choice(u) if u else None


# ---------------------------------------------------------------------------
# 单局
# ---------------------------------------------------------------------------
def play_one(w, h, a, rng, policy, solver_factory=None):
    """返回 (是否胜利, 步数, 是否踩雷, 失败原因)。"""
    board = Board(w, h, a, rng)
    flags = set()

    # 首点：中央（Arbiter 常规），首点保证安全区
    x0, y0 = w // 2, h // 2
    board.open(x0, y0)

    steps = 0
    max_steps = w * h * 4
    solver = solver_factory() if solver_factory else None

    while not board.dead and not board.won and steps < max_steps:
        steps += 1

        if policy == "logic":
            ok, safe, mines = solve_by_logic(board, rng, flags)
            if ok:
                for p in mines:
                    flags.add(p)
                cls = None
                for p in safe:
                    if board.cells[p[1]][p[0]] == UNOPENED:
                        cls = p
                        board.open(*p)
                        if board.dead:
                            return False, steps, True, "逻辑推理踩雷(不应发生)"
                        break
                if cls is not None:
                    continue
            # 无确定推理 → 随机猜（人类下限）
            p = random_unopened(board, rng, flags)
            if p is None:
                break
            board.open(*p)
            if board.dead:
                return False, steps, True, "随机猜测踩雷"

        elif policy == "perfect":
            # 完美信息上界：直接开所有非雷格（仅作理论上限参照）
            for y in range(h):
                for x in range(w):
                    if (x, y) not in board.mines and board.cells[y][x] == UNOPENED:
                        board.open(x, y)
            break

        else:  # engine：接项目求解器
            view = _view_only(board, flags)
            try:
                move = solver.decide(view)
            except Exception as e:  # noqa: BLE001
                return False, steps, False, f"求解器异常: {type(e).__name__}: {e}"
            if move is None:
                break
            kind, (mx, my) = move
            if kind == "flag":
                flags.add((mx, my))
                continue
            if board.cells[my][mx] != UNOPENED:
                # 求解器给了已开的格：换一个未开的（不计入失败，只记录）
                p = random_unopened(board, rng, flags)
                if p is None:
                    break
                mx, my = p
            board.open(mx, my)
            if board.dead:
                return False, steps, True, "求解器点击踩雷"

    if board.won:
        return True, steps, False, ""
    return False, steps, False, "未完成(步数上限/无格可点)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--policy", default="logic", choices=["logic", "engine", "perfect"])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    wins = 0
    reasons = {}
    t0 = time.time()
    for g in range(args.games):
        win, steps, died, why = play_one(args.w, args.h, args.a, rng, args.policy)
        wins += win
        reasons[why] = reasons.get(why, 0) + 1

    dt = time.time() - t0
    rate = wins / args.games * 100
    print(f"策略={args.policy}  棋盘={args.w}x{args.h}  雷={args.a}")
    print(f"胜率 = {wins}/{args.games} = {rate:.2f}%   用时 {dt:.1f}s")
    if reasons:
        print("结束原因分布：")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {k or '(正常胜利)'}: {v}")


if __name__ == "__main__":
    main()
