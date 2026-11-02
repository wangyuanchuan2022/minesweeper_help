# -*- coding: utf-8 -*-
"""大局面基准 v2：用"真有解、真爆炸"的合规棋盘。

关键教训（v1 的错误）：随便填数字会导致约束无解，part_solve 在第一个约束处
就剪枝，测出 0.02ms 的假读数 —— 零成本是因为"没有解"，不是因为"算得快"。

正确做法：先随机布雷 → 由雷位反推每个数字格的值 → 这才是合规棋盘。
这样前沿越大、解数越多，才反映真实大局面成本。

用法: python bench/bench_large_board.py
"""
import os
import statistics
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver
from utils import native

W, H = 30, 16


def legit_board(n_mines=99, seed=0):
    """生成合规棋盘：随机布 num10='num9' 语义的未开区，已开区数字由真实雷反推。"""
    rng = np.random.RandomState(seed)
    cv = np.zeros((H + 2, W + 2), np.int32)
    cv[0, :] = cv[-1, :] = 0
    cv[:, 0] = cv[:, -1] = 0

    # 全部先标未开
    for j in range(1, H + 1):
        for i in range(1, W + 1):
            cv[j, i] = 9

    # 选已开区域：左侧一列整开 + 上方若干行整开，制造一条“L 形”前沿
    open_cells = [(i, j) for j in range(1, 4) for i in range(1, W + 1)]
    for i, j in open_cells:
        cv[j, i] = 0

    # 在剩余未开区随机布雷
    frontier = [(i, j) for i, j in open_cells if j <= 3]
    candidates = [(i, j) for j in range(1, H + 1) for i in range(1, W + 1) if cv[j, i] == 9]
    idx = rng.choice(len(candidates), n_mines, replace=False)
    mines = {candidates[int(k)] for k in idx}

    # 已开格填真实数字
    for j in range(1, H + 1):
        for i in range(1, W + 1):
            if cv[j, i] == 0:
                cnt = 0
                for n in range(j - 1, j + 2):
                    for m in range(i - 1, i + 2):
                        if (m, n) in mines:
                            cnt += 1
                cv[j, i] = cnt
    return cv, mines


def frontier_of(cv):
    """所有与数字格相邻的未开格 = clicks；其余未开格 = clicks9。"""
    clicks, clicks9 = [], []
    for j in range(1, H + 1):
        for i in range(1, W + 1):
            if cv[j, i] != 9:
                continue
            adj = False
            for n in range(j - 1, j + 2):
                for m in range(i - 1, i + 2):
                    if 0 < cv[n, m] < 9:
                        adj = True
                        break
                if adj:
                    break
            (clicks if adj else clicks9).append((i, j))
    return clicks, clicks9


def bench(fn, n=3):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    print(f"native={native.available}")
    solver = make_full_solver(w=W, h=H, a=99)
    solver.is_play = False

    print("=" * 80)
    for mines in (40, 60, 80, 99):
        cv, mine_set = legit_board(mines)
        clicks, clicks9 = frontier_of(cv)
        if not clicks:
            continue
        num10 = 0
        num9 = len(clicks) + len(clicks9)
        cs = [(i, j) for j in range(1, H + 1) for i in range(1, W + 1) if 0 < cv[j, i] < 9]
        try:
            dt = bench(lambda: solver.part_solve(list(clicks), cv.copy(), num10, num9, cs,
                                                 _try=False), 3)
            res, num, _ = solver.part_solve(list(clicks), cv.copy(), num10, num9, cs, _try=False)
            print(f"雷数{mines:3d}  前沿格{len(clicks):3d}  内部格{len(clicks9):3d}  "
                  f"数字格{len(cs):3d}  part_solve={dt:9.2f} ms  解数={num}")
        except Exception as e:
            print(f"雷数{mines:3d}  前沿格{len(clicks):3d}  ERR {type(e).__name__}: {e}")

    print("=" * 80)
    print("再看真实局面 test_4（clicks=4 但 num9=64，属于'大内部区'）:")
    import json
    d = json.load(open("tests/data/test_4.json", encoding="utf-8"))
    cv4 = np.array(d["cell_value"], np.int32)
    print(f"  clicks={len(d['clicks'])}  cs={len(d['cs'])}  num9={d['num9']}")
    dt = bench(lambda: solver.part_solve([tuple(c) for c in d["clicks"]], cv4.copy(),
                                         d["num10"], d["num9"], d["cs"], _try=False), 3)
    print(f"  part_solve = {dt:.2f} ms")


if __name__ == "__main__":
    main()
