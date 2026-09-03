# -*- coding: utf-8 -*-
"""wr_test.json 局面的胜率正确性与 C++/Python 速度对比（number5_1 端到端）。

数据：tests/data/wr_test.json（{"cell_value": 矩阵}，30x16 专家局快照）。
流程：同一固定随机种子下，分别以 C++（native）与纯 Python 跑完整决策，
对比棋盘结果、剩余胜率（till_now_winrate）与最佳点击是否一致，并输出耗时。
阈值用 MSW_NATIVE_TUNE=0 固定，保证两条路径的决策分支一致。

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

    def run_e2e(use_native):
        native.available = use_native
        np.random.seed(20260903)
        s = make_full_solver(w=cv.shape[1] - 2, h=cv.shape[0] - 2, a=a)
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

        norm = (
            np.asarray(out).tolist(),                       # 决策后棋盘
            [norm_best(hm.get("best"))],                    # 最佳点击
            round(float(hm.get("total", 0.0)), 9),          # 总局面数
            round(float(getattr(s, "till_now_winrate", 1.0)), 12),  # 剩余胜率
        )
        return norm, dt

    try:
        os.environ["MSW_NATIVE_TUNE"] = "0"  # 固定阈值 → 两路径决策分支一致
        (out_n, dt_n) = run_e2e(True)
        (out_p, dt_p) = run_e2e(False)
    finally:
        os.environ.pop("MSW_NATIVE_TUNE", None)
        native.available = True

    ok = out_n == out_p
    wr_n, wr_p = out_n[3], out_p[3]
    print(f"[{'OK ' if ok else 'FAIL'}] number5_1 端到端结果一致"
          + ("" if ok else f"\n  C++    {str(out_n)[:150]}\n  Python {str(out_p)[:150]}"))
    print(f"    剩余胜率：C++ {wr_n:.6f}  Python {wr_p:.6f}")
    print(f"    number5_1 端到端耗时：C++ {dt_n:.3f}s  Python {dt_p:.3f}s  "
          f"提速 {dt_p / max(dt_n, 1e-9):.1f}x")

    if not ok:
        FAILURES.append("端到端结果不一致")
    print()
    if FAILURES:
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL CONSISTENT")


if __name__ == "__main__":
    main()
