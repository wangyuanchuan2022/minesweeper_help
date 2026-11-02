# -*- coding: utf-8 -*-
"""大情况真正的算法成本：win_rate 在 limitation 临界处的爆炸曲线。

背景：number5_1 用 limitation = len(clicks9)*0.8 + log2(total) 判定走哪条路：
  limitation <= wr_threshold → win_rate（精确胜率搜索，可能指数爆炸）
  否则                        → process_bigger_situation（近似）

state/time_budget.json 里 wr 阈值当前是 10.5。阈值越高，越容易触发昂贵的
win_rate。这个阈值是"耗时反馈自动调节"的——也就是说程序会自己往贵的方向爬，
直到撞上 30s 预算。

本脚本量化：clicks9 数量增加时 win_rate 的真实耗时曲线。
这决定了"大情况"是否真的会成为瓶颈。

用法: python bench/bench_winrate_scaling.py
"""
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


def med(fn, n=1):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    print(f"native={native.available}")
    d = load_test_data("test_33.json")
    cv = np.array(d["cell_value"], np.int32)
    clicks = [tuple(c) for c in d["clicks"]]
    cs = [tuple(c) for c in d["cs"]]

    s0 = make_full_solver(w=30, h=16, a=99)
    s0.is_play = False
    res_a, n_a, _ = s0.part_solve(clicks[:10], cv.copy(), d["num10"], d["num9"], cs, _try=False)
    print(f"参考分组：解数={n_a}")
    if n_a == 0:
        print("解数为 0，无法构造 win_rate 输入")
        return

    all9 = [(i, j) for j in range(1, 17) for i in range(1, 31)
            if cv[j, i] == 9 and (i, j) not in set(clicks)]
    print(f"可用 clicks9 池 = {len(all9)} 个")
    print("=" * 72)
    print(f"{'clicks9数':>10} {'limitation':>12} {'win_rate(ms)':>14} {'备注':>20}")

    for k in (1, 2, 3, 4, 5, 6, 7, 8):
        clicks9 = all9[:k]
        limitation = len(clicks9) * 0.8 + np.log2(max(n_a, 1))
        s = make_full_solver(w=30, h=16, a=99)
        s.is_play = False
        try:
            dt = med(lambda: s.win_rate(list(clicks[:10]), list(clicks9), [res_a],
                                        cv.copy(), [n_a], d["num10"]), 1)
            note = "超 30s 预算" if dt > 30000 else ("接近预算" if dt > 5000 else "")
            print(f"{k:>10} {limitation:>12.2f} {dt:>14.1f} {note:>20}")
            if dt > 60000:
                print("（已超过 1 分钟，停止继续放大）")
                break
        except Exception as e:
            print(f"{k:>10} {limitation:>12.2f} {'ERR':>14}  {type(e).__name__}: {str(e)[:40]}")
            break

    print("=" * 72)
    print("判读：")
    print("  若 win_rate 随 clicks9 呈指数增长，则大情况确实会撞 30s 预算——")
    print("  这才是『大情况要 10x』的真实对象，而不是视觉。")
    print("  若增长平缓，则大情况的瓶颈仍是每步一次的全盘视觉重识别。")


if __name__ == "__main__":
    main()
