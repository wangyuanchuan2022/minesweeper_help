# -*- coding: utf-8 -*-
"""端到端模拟：在同一"大局面"上对比 现状 vs 优化后 的完整单步耗时。

与前面只看分段的区别：本脚本把"决策→点击→重扫"整条流程拼起来跑，
用真实的 number5_1 + 真实的视觉识别路径（用合成棋盘图，不碰真实游戏窗口），
给出可复现的整体读数。

用法: python bench/bench_e2e_optimized.py
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

W, H, CELL = 30, 16, 24


def synth_board(rng, opened_ratio=0.55):
    """合成一张棋盘截图（尺寸与 _grab_board 一致）。"""
    img = np.full((H * CELL, W * CELL, 3), 200, np.uint8)
    n = int(W * H * opened_ratio)
    for k in rng.choice(W * H, n, replace=False):
        y, x = divmod(int(k), W)
        img[y * CELL:(y + 1) * CELL, x * CELL:(x + 1) * CELL] = 150
    return np.clip(img.astype(np.int16) + rng.randint(-10, 11, img.shape), 0, 255).astype(np.uint8)


def scan_current(solver, img):
    """现状全盘识别：逐格 compare_img。"""
    solver.img = img
    cv = np.full((H + 2, W + 2), 9, np.int32)
    cv[0, :] = cv[-1, :] = 0
    cv[:, 0] = cv[:, -1] = 0
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            cv[y, x] = solver.compare_img(solver.cell_screenshot(x, y), no_10=True)
    return cv


def build_batch_scorer(solver):
    """构建等价批量打分器（与 bench_vision_vector.py 已验证的 18.3x 方案同构）。"""
    sh, sw = solver.screenshot_h, solver.screenshot_w
    tpl = []
    for row_i, row in enumerate(solver.images):
        for t in row:
            if t is not None and t.shape[0] <= sh and t.shape[1] <= sw:
                tpl.append((row_i, t.astype(np.float32)))
    ys = np.array([y * CELL - CELL // 2 - sh // 2 for y in range(1, H + 1) for x in range(1, W + 1)])
    xs = np.array([x * CELL - CELL // 2 - sw // 2 for y in range(1, H + 1) for x in range(1, W + 1)])

    def score(img):
        cv = np.full((H + 2, W + 2), 9, np.int32)
        cv[0, :] = cv[-1, :] = 0
        cv[:, 0] = cv[:, -1] = 0
        best = np.full(W * H, np.inf)
        best_row = np.zeros(W * H, np.int32)
        for row_i, tem in tpl:
            th, tw = tem.shape[:2]
            yy = ys[:, None, None] + np.arange(th)[None, :, None]
            xx = xs[:, None, None] + np.arange(tw)[None, None, :]
            win = img[yy, xx, :].astype(np.float32)
            t2 = float(np.sum(tem * tem))
            num = np.einsum("mhwc,mhwc->m", win - tem[None], win - tem[None])
            den = np.sqrt(np.maximum(np.einsum("mhwc,mhwc->m", win, win) * t2, 1e-12))
            s = num / den
            better = s < best
            best[better] = s[better]
            best_row[better] = row_i
        for k in range(W * H):
            y, x = divmod(k, W)
            v = int(best_row[k])
            cv[y + 1, x + 1] = 9 if v == 10 else v
        return cv

    return score


def med(fn, n=3):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    d = load_test_data("test_33.json")
    board = np.array(d["cell_value"], np.int32)

    solver = make_full_solver(w=W, h=H, a=99)
    solver.cell_width = CELL
    solver.screenshot_w = int(CELL * 5 / 9)
    solver.screenshot_h = int(CELL * 7 / 9)
    solver.load_img()
    solver.is_play = False

    rng = np.random.RandomState(3)
    img = synth_board(rng)

    # --- 视觉两版本 ---
    t_cur = med(lambda: scan_current(solver, img), 3)
    batch = build_batch_scorer(solver)
    t_batch = med(lambda: batch(img), 3)
    print("=" * 70)
    print(f"视觉全盘识别   现状 {t_cur:8.2f} ms  批量 {t_batch:8.2f} ms  "
          f"加速 {t_cur/t_batch:5.1f}x")

    # --- 决策：同一局面 ---
    def decision():
        s = make_full_solver(w=W, h=H, a=99)
        s.is_play = False
        s.num = 1
        s.checked = {}
        s.pos_dict_list = []
        s.appended_pos = set()
        s.complete_scan = lambda c, no_10=True: c
        return s.number5_1(board.copy())

    t_dec = med(decision, 3)
    print(f"决策 number5_1（不含视觉）  {t_dec:8.2f} ms")

    # --- 综合单步 ---
    CLICK_WAIT = 100.0
    GRAB = 37.2 * 0.4
    cur_step = t_cur + t_dec + CLICK_WAIT + GRAB
    opt_step = t_batch + t_dec + CLICK_WAIT + GRAB
    print("=" * 70)
    print(f"单步（现状）  = {t_cur:.1f} + {t_dec:.1f} + {CLICK_WAIT:.0f} + {GRAB:.1f} "
          f"= {cur_step:.1f} ms")
    print(f"单步（仅视觉优化） = {t_batch:.1f} + {t_dec:.1f} + {CLICK_WAIT:.0f} + {GRAB:.1f} "
          f"= {opt_step:.1f} ms   提速 {cur_step/opt_step:.2f}x")
    print("=" * 70)
    print("注意：仅换视觉只能到 ~2.4x。要 10x 必须同时削掉点击等待(100ms)并把决策压到 10ms 级。")


if __name__ == "__main__":
    main()
