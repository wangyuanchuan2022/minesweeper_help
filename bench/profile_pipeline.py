# -*- coding: utf-8 -*-
"""端到端耗时分解：视觉识别 / 枚举决策 / 点击等待 各占多少墙钟时间。

不依赖真实游戏窗口（不动用户进程）：
- 视觉部分用合成格子图驱动 compare_img 真实计算路径（与真机同代码）；
- 决策部分用 tests/data 的真实局面；
- 点击等待按源码常量计（pyautogui.click + time.sleep），可回归比对。

用法: python bench/profile_pipeline.py
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native


def bench_vision_per_cell(solver, repeats=20):
    """单格 compare_img 耗时（25 次模板匹配的真实路径）。"""
    cw = solver.cell_width
    sw, sh = solver.screenshot_w, solver.screenshot_h
    cell = (np.random.rand(sh, sw, 3) * 255).astype(np.uint8)
    t0 = time.perf_counter()
    for _ in range(repeats):
        solver.compare_img(cell, no_10=True)
    return (time.perf_counter() - t0) / repeats * 1000.0


def bench_vision_full_board(solver):
    """全盘扫描耗时估算 = 单格耗时 × 格子数（仅未开格才扫，按最坏 100% 计）。"""
    per = bench_vision_per_cell(solver)
    cells = solver.w * solver.h
    return per, per * cells / 1000.0


def main():
    print("native.available =", native.available)
    print("=" * 78)
    data = load_test_data("test_33.json")
    cv = np.array(data["cell_value"], dtype=np.int32)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    solver = make_full_solver(w=w, h=h, a=99)
    solver.is_play = False

    # ---------- 1. 视觉 ----------
    per_ms, board_s = bench_vision_full_board(solver)
    print(f"[视觉] 单格 compare_img = {per_ms:.3f} ms")
    print(f"[视觉] 全盘 {w}x{h}={w*h} 格 → {board_s:.3f} s/次扫描")

    # ---------- 2. 决策 ----------
    def timeit(fn, n=1):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n

    for n_clicks in (5, 8, len(data["clicks"])):
        clicks = [tuple(c) for c in data["clicks"][:n_clicks]]
        dt = timeit(lambda: solver.part_solve(clicks, cv.copy(), data["num10"],
                                              data["num9"], data["cs"], _try=False), 3)
        print(f"[枚举] part_solve[{n_clicks:2d} 格] = {dt*1000:9.2f} ms")

    def run_n51():
        s = make_full_solver(w=w, h=h, a=99)
        s.num = 1
        s.checked = {}
        s.pos_dict_list = []
        s.appended_pos = set()
        return s.number5_1(cv.copy())

    # number5_1 内部会调用 complete_scan（真屏截图），此处用假实现隔离计时
    orig_scan = solver.complete_scan
    solver.complete_scan = lambda c, no_10=True: c
    try:
        dt51 = timeit(lambda: run_n51(), 1)
        print(f"[决策] number5_1 端到端（不含视觉重扫）= {dt51*1000:9.1f} ms")
    finally:
        solver.complete_scan = orig_scan

    # ---------- 3. 墙钟等待常量 ----------
    waits = [
        ("决策后点击 sleep", 0.1),
        ("每局开始 sleep", 1.0),
        ("胜负弹窗 sleep", 1.2),
        ("重开窗口 sleep", 0.5),
    ]
    print("[等待] " + " | ".join(f"{n}={v}s" for n, v in waits))

    # ---------- 4. 单步合并模型 ----------
    print("=" * 78)
    print("单步（一次决策 + 一次点击后重扫）墙钟构成估算：")
    print(f"  视觉全盘扫描     : {board_s*1000:8.1f} ms")
    print(f"  枚举+决策        : {dt51*1000:8.1f} ms")
    print(f"  点击等待         : {100:8.1f} ms")
    print(f"  → 合计（最坏）   : {board_s*1000 + dt51*1000 + 100:8.1f} ms")
    print(f"  视觉占比         : {board_s/(board_s+dt51+0.1)*100:8.1f} %")


if __name__ == "__main__":
    main()
