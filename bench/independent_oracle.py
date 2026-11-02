# -*- coding: utf-8 -*-
"""独立判定器 v2：构造有区分度的局面，真正能抓出概率错误。

v1 的问题：局面约束太强（合法布局数=1），所有概率都是 1.0，什么实现都能过。
本版构造"部分已知"的局面——露出部分格子，留下多个未开格，使合法布局数 > 1，
这样每格概率是 0 与 1 之间的真分数，才有区分度。

用法: python bench/independent_oracle.py
"""
import itertools
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver


def brute_force(board, w, h, total_mines):
    """穷举全盘雷布局，返回 {未开格: 雷概率} 与合法布局数。与项目实现零共享代码。"""
    unknown = [(i, j) for j in range(1, h + 1) for i in range(1, w + 1)
               if board[j, i] == 9]
    constraints = []
    for j in range(1, h + 1):
        for i in range(1, w + 1):
            v = int(board[j, i])
            if 0 < v <= 8:
                nbrs = [(m, n) for n in range(j - 1, j + 2) for m in range(i - 1, i + 2)
                        if 1 <= m <= w and 1 <= n <= h and board[n, m] == 9]
                constraints.append((v, nbrs))
    counts = {p: 0 for p in unknown}
    valid = 0
    nun = len(unknown)
    for combo in itertools.combinations(range(nun), total_mines):
        mines = {unknown[k] for k in combo}
        if all(sum(1 for p in nbrs if p in mines) == want for want, nbrs in constraints):
            valid += 1
            for p in unknown:
                if p in mines:
                    counts[p] += 1
    return counts, valid, unknown


def main():
    w = h = 4
    total = 4
    rng = np.random.RandomState(11)
    print(f"棋盘 {w}x{h}，总雷数 {total}")
    print("=" * 74)

    tested = 0
    for trial in range(8):
        mines = set()
        while len(mines) < total:
            mines.add((int(rng.randint(1, w + 1)), int(rng.randint(1, h + 1))))
        board = np.full((h + 2, w + 2), 9, np.int32)
        board[0, :] = board[-1, :] = 0
        board[:, 0] = board[:, -1] = 0
        for j in range(1, h + 1):
            for i in range(1, w + 1):
                if (i, j) not in mines:
                    board[j, i] = sum(1 for n in range(j - 1, j + 2)
                                      for m in range(i - 1, i + 2) if (m, n) in mines)
        # 只"露出"一部分：把一半非雷格盖回 9，制造不确定性
        covered = 0
        for j in range(1, h + 1):
            for i in range(1, w + 1):
                if (i, j) not in mines and covered < 5 and rng.rand() < 0.5:
                    board[j, i] = 9
                    covered += 1

        counts, valid, unknown = brute_force(board, w, h, total)
        if valid == 0 or not unknown:
            continue
        brute = {p: counts[p] / valid for p in unknown}
        # 区分度判据：至少有一个概率是严格介于 0 和 1 之间
        has_grad = any(1e-9 < v < 1 - 1e-9 for v in brute.values())
        tested += 1

        s = make_full_solver(w=w, h=h, a=total)
        s.is_play = False
        cs = [(i, j) for j in range(1, h + 1) for i in range(1, w + 1) if 0 < board[j, i] <= 8]
        try:
            res_list, num, _ = s.part_solve(list(unknown), board.copy(), 0, len(unknown), cs,
                                            _try=False)
            acc = np.zeros(len(unknown), np.float64)
            for row in res_list:
                acc += np.asarray(row, np.float64)
            proj = {unknown[k]: acc[k] / num for k in range(len(unknown))} if num else {}
        except Exception as e:
            print(f"局面{trial+1}: 项目抛错 {type(e).__name__}: {e}")
            continue

        if not proj:
            print(f"局面{trial+1}: 解数=0（暴力给 {valid} 个合法布局）→ ❌ 项目漏解")
            continue
        diff = max(abs(brute[p] - proj[p]) for p in unknown)
        flag = "✅" if diff < 1e-6 else "❌"
        print(f"局面{trial+1}: 合法布局={valid:3d} 区分度={'有' if has_grad else '无'} "
              f"最大差={diff:.8f} {flag}")
        if not has_grad:
            print(f"        （本局面全 0/1，无区分度，证明力弱）")

    print("=" * 74)
    print(f"有效对照局面数 = {tested}")
    print("结论：只要出现 diff>0 即说明存在真实概率错误；diff=0 且布局数>1 才是有效证据。")


if __name__ == "__main__":
    main()
