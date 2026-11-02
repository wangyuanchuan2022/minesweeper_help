# -*- coding: utf-8 -*-
"""修正独立判定器：先搞清 part_solve 的参数语义，再谈"项目算错"。

前两版的错误（必须显式记录，否则会误报项目 bug）：
- 我传 num10=0, num9=len(unknown)，但 part_solve 内部用 self.a（总雷数）
  做 mine_count <= a 的上界剪枝，且 num9/num10 实际未参与过滤
- 更关键：我把"只有部分格子未开"的局面直接喂给 part_solve，但 part_solve
  期望的是"一个分组内的前沿格集合"，配合 cs（约束数字格）使用；
  全盘未开格混在一起、又没有正确的 num10（已标雷数），语义就错位了

正确做法：用 number5_1 端到端（它会自己算 clicks/clicks9/num9/num10 并选分支），
再与暴力参照比；或者严格按 part_solve 的前置契约构造输入。

本脚本改用 number5_1 端到端，并对照暴力参照。

用法: python bench/locate_prob_bug.py
"""
import itertools
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver


def brute_all(board, w, h, total_mines):
    """暴力：枚举全盘恰好 total_mines 个雷的所有布局，返回每未开格雷概率。"""
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
    for combo in itertools.combinations(range(len(unknown)), total_mines):
        mines = {unknown[c] for c in combo}
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
    print(f"棋盘 {w}x{h} 总雷数 {total}；用 number5_1 端到端 vs 暴力枚举")
    print("=" * 74)

    ok = bad = weak = 0
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
        covered = 0
        for j in range(1, h + 1):
            for i in range(1, w + 1):
                if (i, j) not in mines and covered < 5 and rng.rand() < 0.5:
                    board[j, i] = 9
                    covered += 1

        counts, valid, unknown = brute_all(board, w, h, total)
        if valid == 0:
            continue
        ref = {p: counts[p] / valid for p in unknown}
        has_grad = any(1e-9 < v < 1 - 1e-9 for v in ref.values())
        if not has_grad:
            weak += 1
            print(f"局面{trial+1}: 布局数={valid} 区分度=无 → 跳过（证明力弱）")
            continue

        s = make_full_solver(w=w, h=h, a=total)
        s.num = 1
        s.checked = {}
        s.pos_dict_list = []
        s.appended_pos = set()
        s.complete_scan = lambda c, no_10=True: c
        try:
            s.number5_1(board.copy())
            prob = (getattr(s, "_last_heatmap", {}) or {}).get("prob") or {}
        except Exception as e:
            print(f"局面{trial+1}: number5_1 抛错 {type(e).__name__}: {e}")
            bad += 1
            continue

        common = [p for p in unknown if p in prob]
        if not common:
            print(f"局面{trial+1}: 项目未给出这些格的概率（可能走了随机分支）")
            continue
        # prob 是"不是雷"的概率；暴力 ref 是"是雷"的概率
        diff = max(abs((1.0 - ref[p]) - prob[p]) for p in common)
        if diff < 1e-6:
            ok += 1
            print(f"局面{trial+1}: 布局数={valid} 对照格={len(common)} 最大差={diff:.8f} ✅")
        else:
            bad += 1
            print(f"局面{trial+1}: 布局数={valid} 对照格={len(common)} 最大差={diff:.8f} ❌")
            print(f"        暴力(非雷概率): {[round(1-ref[p],4) for p in common]}")
            print(f"        项目(非雷概率): {[round(prob[p],4) for p in common]}")

    print("=" * 74)
    print(f"一致={ok}  不一致={bad}  无区分度跳过={weak}")
    print("注意：number5_1 在 limitation 超阈值时走近似的 pbs 分支（非精确），")
    print("      小棋盘上一般走精确分支；若显示不一致，需先确认走的是哪条分支。")


if __name__ == "__main__":
    main()
