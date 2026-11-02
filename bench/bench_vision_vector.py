# -*- coding: utf-8 -*-
"""视觉识别优化实测：严格等价重排（矢量化）+ 颜色预筛。

现状语义（utils/vision.py compare_img）必须逐位复现，才能谈"等价"：
  - 模板 25 个分两种尺寸：11 个 (18,12)、14 个 (20,28)
  - 补丁尺寸 (13,18)：小模板可匹配（响应图 1x2），大模板抛 cv.error 被静默吞掉
  ⇒ 实际只有 11 个模板参与打分，另外 14 个是**死模板**（本脚本顺带量化）
  - 打分 = np.sum(整张响应图)，取 11 个模板里和最小时的数字行

优化点：
  A 现状：480 格 × 25 次 cv.matchTemplate（其中 14 次必然异常）
  B 矢量化：一次取全部补丁，用 numpy 批量算同语义分值（无 12000 次调用开销）
  C 预筛+B：未开格底色是稳定判据，先廉价判 9 再对剩余格做 B

用法: python bench/bench_vision_vector.py
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

W, H, CELL = 30, 16, 24


def active_templates(solver):
    """只保留能落进补丁的模板（等价于现状中不会抛 cv.error 的那部分）。"""
    sh, sw = solver.screenshot_h, solver.screenshot_w
    out, dead = [], 0
    for i, row in enumerate(solver.images):
        for t in row:
            if t is None:
                continue
            if t.shape[0] <= sh and t.shape[1] <= sw:
                out.append((i, t.astype(np.float32)))
            else:
                dead += 1
    return out, dead


def make_board(seed=0, opened_ratio=0.65):
    rng = np.random.RandomState(seed)
    img = np.full((H * CELL, W * CELL, 3), 200, np.uint8)
    for n in rng.choice(W * H, int(W * H * opened_ratio), replace=False):
        y, x = divmod(n, W)
        img[y * CELL:(y + 1) * CELL, x * CELL:(x + 1) * CELL] = 150
    return np.clip(img.astype(np.int16) + rng.randint(-12, 13, img.shape), 0, 255).astype(np.uint8)


def offsets(solver):
    sw, sh = solver.screenshot_w, solver.screenshot_h
    ys = np.array([y * CELL - CELL // 2 - sh // 2 for y in range(1, H + 1) for x in range(1, W + 1)])
    xs = np.array([x * CELL - CELL // 2 - sw // 2 for y in range(1, H + 1) for x in range(1, W + 1)])
    return ys, xs


def patches_for(board, ys, xs, idx, sh, sw):
    yy = ys[idx][:, None, None] + np.arange(sh)[None, :, None]
    xx = xs[idx][:, None, None] + np.arange(sw)[None, None, :]
    return board[yy, xx, :].astype(np.float32)


def sqdiff_norm_maps(patches, tem, sh, sw):
    """严格复现 cv.matchTemplate(patches, tem, TM_SQDIFF_NORMED) 并对全部位置求和。

    patches (M,sh,sw,3) float32；tem (th,tw,3) float32。
    TM_SQDIFF_NORMED: R = Σ(I-T)² / sqrt(ΣI² · ΣT²)，逐位置。
    返回 (M,) 每张补丁的"响应图之和"。
    """
    th, tw = tem.shape[:2]
    t2 = float(np.sum(tem * tem))
    ph, pw = sh - th + 1, sw - tw + 1
    total = np.zeros(len(patches), np.float64)
    for dy in range(ph):
        for dx in range(pw):
            win = patches[:, dy:dy + th, dx:dx + tw, :]
            d = win - tem[None]
            num = np.einsum("mhwc,mhwc->m", d, d)
            den = np.sqrt(np.maximum(np.einsum("mhwc,mhwc->m", win, win) * t2, 1e-12))
            total += num / den
    return total


def impl_a(solver, board, cell_value):
    solver.img = board
    out = cell_value.copy()
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            if out[y, x] == 9:
                out[y, x] = solver.compare_img(solver.cell_screenshot(x, y), no_10=True)
    return out


def classify_batch(board, ys, xs, solver, act_templ, idx):
    """对 idx 指定的格子批量打分，返回每格数字行（严格等价 compare_img）。"""
    sh, sw = solver.screenshot_h, solver.screenshot_w
    patches = patches_for(board, ys, xs, idx, sh, sw)
    best = np.full(len(idx), np.inf)
    best_row = np.zeros(len(idx), np.int32)
    for row_i, tem in act_templ:
        s = sqdiff_norm_maps(patches, tem, sh, sw)
        better = s < best
        best[better] = s[better]
        best_row[better] = row_i
    return best_row


def impl_b(solver, board, cell_value, ys, xs, act_templ, covered_rgb=None, tol=6.0):
    out = cell_value.copy()
    sh, sw = solver.screenshot_h, solver.screenshot_w
    unknown = np.array([(y - 1) * W + (x - 1) for y in range(1, H + 1)
                        for x in range(1, W + 1) if out[y, x] == 9], np.int64)
    if len(unknown) == 0:
        return out
    if covered_rgb is not None:
        p = patches_for(board, ys, xs, unknown, sh, sw)
        is_cov = np.abs(p.mean(axis=(1, 2)) - covered_rgb).max(axis=1) <= tol
        unknown = unknown[~is_cov]
    if len(unknown):
        rows = classify_batch(board, ys, xs, solver, act_templ, unknown)
        for k, fi in enumerate(unknown):
            y, x = divmod(int(fi), W)
            v = int(rows[k])
            out[y + 1, x + 1] = 9 if v == 10 else v
    return out


def main():
    solver = make_full_solver(w=W, h=H, a=99)
    solver.cell_width = CELL
    solver.screenshot_w = int(CELL * 5 / 9)
    solver.screenshot_h = int(CELL * 7 / 9)
    solver.load_img()
    act_templ, dead = active_templates(solver)
    ys, xs = offsets(solver)
    print(f"补丁 {solver.screenshot_h}x{solver.screenshot_w}；"
          f"参与打分的模板 {len(act_templ)} 个，被 cv.error 静默跳过的死模板 {dead} 个")

    board = make_board()
    cv0 = np.full((H + 2, W + 2), 9, np.int32)
    cv0[0, :] = cv0[-1, :] = 0
    cv0[:, 0] = cv0[:, -1] = 0

    a = impl_a(solver, board, cv0.copy())
    b = impl_b(solver, board, cv0.copy(), ys, xs, act_templ)
    covered_rgb = np.array([200, 200, 200], np.float32)
    c = impl_b(solver, board, cv0.copy(), ys, xs, act_templ, covered_rgb=covered_rgb)
    print(f"等价性 A vs B(纯批量):   {int((a != b).sum())} / {W*H} 格不一致")
    print(f"等价性 A vs C(预筛+批量): {int((a != c).sum())} / {W*H} 格不一致")

    def timeit(fn, n=5):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t0) / n * 1000

    ta = timeit(lambda: impl_a(solver, board, cv0.copy()))
    tb = timeit(lambda: impl_b(solver, board, cv0.copy(), ys, xs, act_templ))
    tc = timeit(lambda: impl_b(solver, board, cv0.copy(), ys, xs, act_templ, covered_rgb=covered_rgb))
    print(f"\nA 现状         {ta:8.2f} ms   （12000 次 cv.matchTemplate 调用）")
    print(f"B 纯批量等价   {tb:8.2f} ms   加速 {ta/tb:6.1f}x")
    print(f"C 预筛+批量    {tc:8.2f} ms   加速 {ta/tc:6.1f}x")


if __name__ == "__main__":
    main()
