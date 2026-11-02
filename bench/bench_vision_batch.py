# -*- coding: utf-8 -*-
"""视觉识别两种实现的等价性与加速比实测。

A: 现状 —— 每格单独裁剪后，逐模板 matchTemplate（480 格 × 25 模板 = 12000 次调用）
B: 批式 —— 对整盘图逐模板 matchTemplate（25 次调用），再按格子偏移取响应值

数学上 B 在格子左上角处的响应 == A 对该格裁剪图做同样调用的响应（同一定义域），
因此 B 不是"近似"，而是等价重排；本脚本用随机图与真实模板双向校验等价性。

用法: python bench/bench_vision_batch.py
"""
import os
import sys
import time

import cv2 as cv
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver

W, H = 30, 16
CELL = 24


def make_board_img(solver, seed=0):
    """合成一张整盘截图（尺寸与真实 _grab_board 一致）。"""
    rng = np.random.RandomState(seed)
    bw = W * CELL
    bh = H * CELL
    return rng.randint(0, 256, (bh, bw, 3), dtype=np.uint8)


def current_impl(solver, board_img, cell_value):
    """A：现状实现（逐格逐模板）。"""
    solver.img = board_img
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            if cell_value[y, x] == 9:
                cell_value[y, x] = solver.compare_img(solver.cell_screenshot(x, y), no_10=True)
    return cell_value


def batched_impl(solver, board_img, cell_value, templates_flat):
    """B：批式实现（逐模板整盘匹配，再按格取响应）。"""
    # 对每个模板做一次整盘匹配，得到 (rows, cols) 响应图
    resp = []
    for tem in templates_flat:
        r = cv.matchTemplate(board_img, tem, cv.TM_SQDIFF_NORMED)
        # 补边，使索引与格子左上角对齐（matchTemplate 输出尺寸 = 大图-模板+1）
        pad_h = board_img.shape[0] - r.shape[0]
        pad_w = board_img.shape[1] - r.shape[1]
        r = np.pad(r, ((0, pad_h), (0, pad_w)), constant_values=np.inf)
        resp.append(r)
    resp = np.stack(resp)  # (n_templates, bh, bw)

    sw, sh = solver.screenshot_w, solver.screenshot_h
    hw, hh = sw // 2, sh // 2
    out = cell_value.copy()
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            if cell_value[y, x] != 9:
                continue
            # 与 _slice_cell 完全相同的左上角
            lx = x * CELL - CELL // 2 - hw
            ly = y * CELL - CELL // 2 - hh
            vals = resp[:, max(ly, 0), max(lx, 0)]
            out[y, x] = int(np.argmin(vals))
    return out


def main():
    solver = make_full_solver(w=W, h=H, a=99)
    solver.cell_width = CELL
    solver.screenshot_w = int(CELL * 5 / 9)
    solver.screenshot_h = int(CELL * 7 / 9)
    solver.load_img()
    templates_flat = [t for row in solver.images for t in row]
    print(f"模板数 = {len(templates_flat)}，格子 {W}x{H} = {W*H}")

    board = make_board_img(solver)
    cv0 = np.full((H + 2, W + 2), 9, dtype=np.int32)
    cv0[0, :] = cv0[-1, :] = 0
    cv0[:, 0] = cv0[:, -1] = 0

    # 等价性校验：同一张图两条路径的分类结果必须逐格一致
    a = current_impl(solver, board, cv0.copy())
    b = batched_impl(solver, board, cv0.copy(), templates_flat)
    diff = int(np.sum(a != b))
    print(f"等价性: 不一致格数 = {diff} / {W*H}")

    # 计时
    n = 5
    t0 = time.perf_counter()
    for _ in range(n):
        current_impl(solver, board, cv0.copy())
    ta = (time.perf_counter() - t0) / n * 1000

    t0 = time.perf_counter()
    for _ in range(n):
        batched_impl(solver, board, cv0.copy(), templates_flat)
    tb = (time.perf_counter() - t0) / n * 1000

    print(f"现状 A: {ta:8.2f} ms/整盘")
    print(f"批式 B: {tb:8.2f} ms/整盘")
    print(f"加速比: {ta/tb:8.1f}x")


if __name__ == "__main__":
    main()
