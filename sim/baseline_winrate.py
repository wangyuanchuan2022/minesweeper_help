# -*- coding: utf-8 -*-
"""基线胜率：纯瞎猜 vs 纯推理+瞎猜（标准扫雷规则，大量样本）。

两条基线定义（口径写死，避免歧义）：
  B1 纯瞎猜：首点后每次均匀随机点一个未开格（完全不做任何推理）
  B2 纯推理：只用确定性规则（数字-已标雷=剩余 → 全雷/全安全），
            无确定解时均匀随机点一个未开格（不做概率计算）

棋盘规则（与 Arbiter 一致）：
  · 30x16，99 雷
  · 首点保证安全（首点及其 8 邻域无雷）——Arbiter 的常规行为
  · 点开 0 触发 flood-fill
  · 踩雷即负；所有非雷格开完即胜

统计口径：
  · 每档 N 局，报胜率 + 95% 置信区间（Wilson 区间）
  · 同时报"平均第几步踩雷"以说明死因分布

用法: python sim/baseline_winrate.py --games 1000
"""
import argparse
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from sim.minesweeper_sim import Board, UNOPENED


def wilson_ci(k, n, z=1.96):
    """Wilson 95% 置信区间。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def neighbors(w, h):
    nb = {}
    for y in range(h):
        for x in range(w):
            nb[(x, y)] = [(nx, ny) for ny in range(y - 1, y + 2)
                          for nx in range(x - 1, x + 2)
                          if 0 <= nx < w and 0 <= ny < h and (nx, ny) != (x, y)]
    return nb


def deduce(board, flags, nb):
    """确定性推理：返回 (确定安全集, 确定雷集)。纯人类可复现规则。"""
    w, h, cells = board.w, board.h, board.cells
    safe, mines = set(), set()
    changed = True
    while changed:
        changed = False
        for y in range(h):
            for x in range(w):
                v = cells[y][x]
                if not (0 <= v <= 8):
                    continue
                adj = nb[(x, y)]
                f = sum(1 for p in adj if p in flags)
                u = [p for p in adj if cells[p[1]][p[0]] == UNOPENED and p not in flags]
                if not u:
                    continue
                if v - f == len(u):          # 剩余格全是雷
                    for p in u:
                        if p not in mines:
                            mines.add(p); changed = True
                elif v == f:                 # 剩余格全是安全
                    for p in u:
                        if p not in safe:
                            safe.add(p); changed = True
    return safe - flags, mines - flags


def play(w, h, a, rng, policy, nb):
    """跑一局。返回 (是否胜利, 步数, 死因)。"""
    board = Board(w, h, a, rng)
    flags = set()
    x0, y0 = w // 2, h // 2
    board.open(x0, y0)          # 首点安全区（Board.generate 已保证）
    steps = 0

    while not board.dead and not board.won and steps < w * h * 2:
        steps += 1
        if policy == "logic":
            safe, mines = deduce(board, flags, nb)
            for p in mines:
                flags.add(p)
            if safe:
                # 优先点最能展开的（这里简化：任取一个确定安全格）
                board.open(*next(iter(safe)))
                continue
        # 无确定推理（B1 则从不推理）→ 均匀随机
        u = [(x, y) for y in range(h) for x in range(w)
             if board.cells[y][x] == UNOPENED and (x, y) not in flags]
        if not u:
            break
        board.open(*rng.choice(u))

    if board.won:
        return True, steps, ""
    return False, steps, ("踩雷" if board.dead else "未完成")


def run(policy, games, w, h, a, seed):
    rng = random.Random(seed)
    nb = neighbors(w, h)
    wins = 0
    deaths = []
    reasons = {}
    for _ in range(games):
        ok, steps, why = play(w, h, a, rng, policy, nb)
        wins += ok
        reasons[why] = reasons.get(why, 0) + 1
        if not ok and board_dead_hint(why):
            deaths.append(steps)
    lo, hi = wilson_ci(wins, games)
    return wins, games, lo, hi, (sum(deaths) / len(deaths) if deaths else 0), reasons


def board_dead_hint(why):
    return why == "踩雷"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    print(f"棋盘 {args.w}x{args.h}，{args.a} 雷；首点 3x3 安全区")
    print(f"每档 {args.games} 局；首点固定取棋盘中心")
    print("=" * 78)
    results = {}
    for policy, name in (("random", "B1 纯瞎猜（从不推理）"),
                         ("logic", "B2 纯推理+瞎猜（无解即随机）")):
        t0 = time.time()
        wins, n, lo, hi, avg_death, reasons = run(
            policy, args.games, args.w, args.h, args.a, args.seed)
        dt = time.time() - t0
        rate = wins / n * 100
        results[policy] = (wins, n, rate, lo * 100, hi * 100)
        print(f"{name}")
        print(f"  胜率 = {wins}/{n} = {rate:.3f}%   "
              f"95%CI [{lo*100:.2f}%, {hi*100:.2f}%]   用时 {dt:.1f}s")
        print(f"  死因分布: {reasons}")
        if avg_death:
            print(f"  平均踩雷步数: {avg_death:.1f}")
        print()

    print("=" * 78)
    print("口径说明：")
    print("  · B1/B2 都是'人类可复现'的基线，不含任何概率计算或搜索")
    print("  · 项目实际算法（number5_1 含概率枚举/胜率搜索）应显著高于 B2")
    print("  · 置信区间用 Wilson 95%；样本量越大区间越窄")


if __name__ == "__main__":
    main()
