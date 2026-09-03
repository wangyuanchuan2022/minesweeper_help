# -*- coding: utf-8 -*-
"""wr_test.json 局面的胜率正确性与 C++/Python 速度对比（number5_1 端到端）。

数据：tests/data/wr_test.json（{"cell_value": 矩阵}，30x16 专家局快照）。
对比要点：
- 禁用 number5_1 结尾的扫雷窗口重扫（bench 无窗口，且重扫会丢推理标记）；
- 两侧强制走胜率计算分支：patch native.tuned 恒 True（两侧配置一致），
  并预置 _time_budget={"wr":{"t":1e9}} 使 wr_threshold 极大，limitation 必然
  小于阈值 → number5_1 恒走 win_rate 精确枚举，不会滑入 pbs 近似分支；
- 核心对比是两侧算出的逐格"不是雷"概率（win_rate 的 clicks2p）是否相同，
  另对比棋盘/最佳点击/总局面数。
用法：  python bench/compare_wr.py
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_full_solver
from utils import native

FAILURES = []


def main():
    assert native.available, "native 模块未加载，无法对比 C++/Python"
    data_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "data", "wr_test.json",
    )
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    cv = np.array(data["cell_value"], dtype=np.int32)
    a = 99  # 30x16 专家局
    print(f"局面：{cv.shape[1]-2}x{cv.shape[0]-2}  a={a}  "
          f"未开={int((cv == 9).sum())}  标记雷={int((cv == 10).sum())}")

    # 恒 tuned：available 只切换实现路径，两侧的阈值/加成/模型状态保持一致
    native.tuned = lambda: True

    def run_e2e(use_native):
        native.available = use_native
        np.random.seed(20260903)
        s = make_full_solver(w=cv.shape[1] - 2, h=cv.shape[0] - 2, a=a)
        # 禁用结尾的扫雷窗口重扫（bench 无窗口；同时保留推理标记 10/11）
        s.complete_scan = lambda board, *args, **kwargs: board
        # 强制走胜率计算分支：阈值极大 → limitation 必然 <= wr_threshold
        s._time_budget = {"wr": {"t": 1e9}}
        t0 = time.perf_counter()
        out = s.number5_1(cv.copy())
        dt = time.perf_counter() - t0
        hm = getattr(s, "_last_heatmap", None) or {}

        def norm_best(b):
            # best 可能是 tuple/list/numpy 标量，统一成 int 元组便于跨实现比较
            if isinstance(b, (tuple, list)):
                return tuple(int(v) for v in b)
            if b is None:
                return None
            return (int(b),)

        prob = hm.get("prob") or {}
        # 坐标 → 胜率 映射：按格子坐标一一对应逐格比对。
        # 不能用"排序后比序列"：那样只能证明概率值的多重集合相同，
        # 无法排除 A/B 两格胜率互换而序列不变的假一致。
        prob_map = {
            (int(k[0]), int(k[1])): round(float(v), 12) for k, v in prob.items()
        }
        norm = (
            np.asarray(out).tolist(),                       # 决策后棋盘
            [norm_best(hm.get("best"))],                    # 最佳点击
            round(float(hm.get("total", 0.0)), 9),          # 总局面数
            prob_map,                                       # 逐格胜率映射（核心对比项）
        )
        return norm, dt, prob_map

    (out_n, dt_n, prob_n) = run_e2e(True)
    (out_p, dt_p, prob_p) = run_e2e(False)
    native.available = True

    # 逐格比对：先查格集合是否一致，再按坐标一一对应比胜率
    only_n = sorted(set(prob_n) - set(prob_p))
    only_p = sorted(set(prob_p) - set(prob_n))
    common = sorted(set(prob_n) & set(prob_p))
    max_diff = 0.0
    worst = None
    for k in common:
        d = abs(prob_n[k] - prob_p[k])
        if d > max_diff:
            max_diff, worst = d, k

    ok = out_n == out_p and not only_n and not only_p and max_diff == 0.0
    board_n, best_n, total_n, _ = out_n
    print(f"[{'OK ' if ok else 'FAIL'}] number5_1 端到端结果一致"
          + ("" if ok else f"\n  C++    {str(out_n)[:150]}\n  Python {str(out_p)[:150]}"))
    print(f"    逐格胜率（按坐标一一对应）：n={len(common)}  最大差={max_diff:.2e}"
          + (f" @格{worst}" if worst else "")
          + (f"  仅C++格={only_n}  仅Python格={only_p}" if (only_n or only_p) else ""))
    print(f"    总局面数：{total_n:.2f}  最佳点击：{best_n[0] if best_n else '—'}")
    print(f"    number5_1 端到端耗时：C++ {dt_n:.3f}s  Python {dt_p:.3f}s  "
          f"提速 {dt_p / max(dt_n, 1e-9):.1f}x")

    if not ok:
        FAILURES.append("端到端结果不一致")
    if only_n or only_p:
        FAILURES.append("两侧胜率的格集合不一致")
    if max_diff > 0.0:
        FAILURES.append(f"逐格胜率最大差 {max_diff} @格{worst}")
    print()
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL CONSISTENT")


if __name__ == "__main__":
    main()
