# -*- coding: utf-8 -*-
"""UI 渲染开销实测：update_heatmap 一帧要花多少时间（offscreen，不需真屏）。

主会话在 review/主报告.md 里把"每帧 480 个按钮 setStyleSheet"列为待评估项，
本脚本给出真实读数，判断它是否值得优化。

用法: python bench/bench_ui_render.py
"""
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
from PyQt5.QtWidgets import QApplication

from tests.helpers import make_full_solver
import main as main_mod

W, H = 30, 16


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    win = main_mod.MyMainWindow()
    win.resize(1200, 800)

    cv = np.full((H + 2, W + 2), 9, np.int32)
    cv[0, :] = cv[-1, :] = 0
    cv[:, 0] = cv[:, -1] = 0
    # 一半格子已开（模拟中局）
    for j in range(1, H + 1):
        for i in range(1, W + 1):
            if (i + j) % 2 == 0:
                cv[j, i] = (i * j) % 9

    prob = {(i, j): (i * 7 % 100) / 100.0 for i in range(1, W + 1) for j in range(1, H + 1)}

    def frame(p):
        win.update_heatmap({"prob": p, "best": (5, 5), "best_conf": 0.9,
                            "total": 1000.0, "cell_value": cv, "played": 10, "win": 6})

    # 冷帧（全部要 setStyleSheet）
    t0 = time.perf_counter()
    frame(prob)
    cold = (time.perf_counter() - t0) * 1000

    # 热帧（同样 payload → 样式去重生效，应几乎不做事）
    ts = []
    for _ in range(20):
        t0 = time.perf_counter()
        frame(prob)
        ts.append((time.perf_counter() - t0) * 1000)
    hot = statistics.median(ts)

    # 变化帧（概率全变 → 每格都要重设样式）
    ts = []
    for k in range(10):
        p2 = {key: ((v + 0.01 * (k + 1)) % 1.0) for key, v in prob.items()}
        t0 = time.perf_counter()
        frame(p2)
        ts.append((time.perf_counter() - t0) * 1000)
    changing = statistics.median(ts)

    print(f"按钮总数 = {W*H}，已开格 ≈ {sum(1 for j in range(1,H+1) for i in range(1,W+1) if cv[j,i]!=9)}")
    print("=" * 56)
    print(f"冷帧（全部重设样式）  {cold:8.2f} ms")
    print(f"热帧（去重生效）      {hot:8.2f} ms")
    print(f"变化帧（概率全变）    {changing:8.2f} ms")
    print("=" * 56)
    print("对照：单步总墙钟 487 ms，其中视觉 309.6 ms")
    print(f"→ 变化帧占单步 {changing/487*100:.1f}%")
    print("说明：Qt 样式解析是纯 CPU 开销，且发生在 UI 线程 —— 若每步都全量重设，")
    print("      它会与决策/视觉争抢，成为交互卡顿来源，但对'决策总耗时'影响有限。")


if __name__ == "__main__":
    main()
