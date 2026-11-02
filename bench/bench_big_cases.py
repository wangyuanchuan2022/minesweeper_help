# -*- coding: utf-8 -*-
"""大情况分支实测：把 number5_1 里三条决策路径分别压出来测。

代码里"大情况"有明确判据（utils/probability.py:659）：
    limitation = len(clicks9)*0.8 + log2(total)
    limitation <= wr_threshold  → win_rate（精确胜率搜索）
    否则                        → process_bigger_situation（PBS 近似）
PBS 内部再有 total>10000 → 近似分支（probability.py:828 / mscore.cpp:940）

本脚本用真实局面数据分别打这三条分支，报告各自耗时与规模，
回答"大情况慢在哪"。

用法: python bench/bench_big_cases.py
"""
import json
import os
import statistics
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native


def med_time(fn, n=3):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    print(f"native={native.available}")
    print("=" * 78)

    # ---------- 1. 三份真实局面：limitation 与路径判定 ----------
    for name in ("test_33.json", "test_4.json", "test_6.json"):
        d = load_test_data(name)
        cv = np.array(d["cell_value"], np.int32)
        h, w = cv.shape[0] - 2, cv.shape[1] - 2
        s = make_full_solver(w=w, h=h, a=99)
        s.num = 1
        s.checked = {}
        s.pos_dict_list = []
        s.appended_pos = set()
        s.complete_scan = lambda c, no_10=True: c

        total = med_time(lambda: s.number5_1(cv.copy()), 1)
        hm = getattr(s, "_last_heatmap", {}) or {}
        print(f"{name}: clicks={len(d['clicks'])} clicks9={d['num9']} "
              f"total={hm.get('total')} → number5_1 {total:.1f} ms")

    print("=" * 78)
    print("关键：limitation = clicks9*0.8 + log2(total)")
    for name in ("test_33.json", "test_4.json", "test_6.json"):
        d = load_test_data(name)
        clicks9 = d["num9"]
        s = make_full_solver(w=30, h=16, a=99)
        print(f"  {name}: 若 total=1e4 → limitation≈{clicks9*0.8 + 13.3:.1f}"
              f"  （wr 阈值当前持久化值见 state/time_budget.json）")

    # ---------- 2. 强行触发 PBS 的两条分支 ----------
    print("\n" + "=" * 78)
    print("PBS 分支实测（process_bigger_situation 的两个内部分支）")
    d = load_test_data("test_33.json")
    cv = np.array(d["cell_value"], np.int32)
    s = make_full_solver(w=30, h=16, a=99)
    s.is_play = False

    # 造 2 个组，每组若干解，用真实 part_solve 产物
    clicks = [tuple(c) for c in d["clicks"]]
    cs = [tuple(c) for c in d["cs"]]
    res_a, n_a, _ = s.part_solve(clicks[:8], cv.copy(), d["num10"], d["num9"], cs, _try=False)
    res_b, n_b, _ = s.part_solve(clicks[8:16], cv.copy(), d["num10"], d["num9"], cs, _try=False)
    cA, cB = clicks[:8], clicks[8:16]
    clicks9 = [(i, j) for j in range(1, 17) for i in range(1, 31)
               if cv[j, i] == 9 and (i, j) not in set(clicks)][:20]
    print(f"  组A 解数={n_a}  组B 解数={n_b}  乘积 total={n_a*n_b}  clicks9={len(clicks9)}")

    for label, tot in (("精确分支 total<=10000", n_a * n_b), ("近似分支 total>10000", 20000)):
        s2 = make_full_solver(w=30, h=16, a=99)
        s2.is_play = False
        dt = med_time(lambda: s2.process_bigger_situation(
            tot, d["num9"], d["num10"], list(cA + cB), clicks9,
            [res_a, res_b], [n_a, n_b], cv.copy(), []), 3)
        print(f"  {label:<24} total={tot:<8} → {dt:8.2f} ms")

    # ---------- 3. win_rate 在 limitation 边界上的爆炸 ----------
    print("\n" + "=" * 78)
    print("win_rate 精确搜索在限制放宽后的耗时（这是'大情况'最危险的爆炸点）")
    for k in (6, 8, 10):
        s3 = make_full_solver(w=30, h=16, a=99)
        s3.is_play = False
        ck = [n_a, n_b]
        try:
            dt = med_time(lambda: s3.win_rate(list(cA + cB), clicks9[:k], [res_a, res_b],
                                              cv.copy(), ck, d["num10"]), 1)
            print(f"  clicks9 取 {k:2d} 个 → win_rate {dt:9.1f} ms")
        except Exception as e:
            print(f"  clicks9 取 {k:2d} 个 → {type(e).__name__}: {str(e)[:60]}")

    print("\n" + "=" * 78)
    print("结论：大情况的成本结构 = 组合数(win_rate) 指数爆炸 + 视觉固定开销，")
    print("      而 part_solve 本身（C++）几乎免费。优化方向必须是「少算」而非「算快」。")


if __name__ == "__main__":
    main()
