# -*- coding: utf-8 -*-
"""变异体自测：证明对抗性等价测试确实能抓出"补丁尺寸错位"这类缺陷。

若把 compare_cells_batch 的补丁尺寸改回错误的 screenshot_w（13），
测试必须变红。否则说明测试是无效的（假绿）。

用法: python bench/verify_adversarial_mutation.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver

W, H, CELL = 30, 16, 24


def run_case(s, img, cells, use_wrong_patch):
    """用正确 / 错误的补丁尺寸跑批量识别，返回与参考的分歧数。"""
    sh_correct = 2 * (s.screenshot_h // 2)
    sw_correct = 2 * (s.screenshot_w // 2)
    if use_wrong_patch:
        sh, sw = s.screenshot_h, s.screenshot_w      # 变异：按配置值取（错位 1px）
    else:
        sh, sw = sh_correct, sw_correct

    ref = [(x, y, s.compare_img(s.cell_screenshot(x, y), True)) for x, y in cells]

    # 内联复刻批量逻辑（与生产同构），只改补丁尺寸来源
    act = s._active_templates()
    xs = np.array([c[0] for c in cells], np.int64)
    ys = np.array([c[1] for c in cells], np.int64)
    lx = xs * s.cell_width - s.cell_width // 2 - sw // 2
    ly = ys * s.cell_width - s.cell_width // 2 - sh // 2
    yy = ly[:, None, None] + np.arange(sh)[None, :, None]
    xx = lx[:, None, None] + np.arange(sw)[None, None, :]
    try:
        patches = s.img[yy, xx, :].astype(np.float32)
    except IndexError:
        return -1   # 越界也算检出（错误尺寸导致取窗越界）
    best = np.full(len(cells), np.inf)
    best_row = np.zeros(len(cells), np.int32)
    for row_i, tem in act:
        th, tw = tem.shape[:2]
        ph, pw = sh - th + 1, sw - tw + 1
        total = np.zeros(len(cells), np.float64)
        t2 = float(np.sum(tem * tem))
        for dy in range(ph):
            for dx in range(pw):
                win = patches[:, dy:dy + th, dx:dx + tw, :]
                d = win - tem[None]
                num = np.einsum("mhwc,mhwc->m", d, d)
                den = np.sqrt(np.maximum(np.einsum("mhwc,mhwc->m", win, win) * t2, 1e-12))
                total += num / den
        better = total < best
        best[better] = total[better]
        best_row[better] = row_i
    got = [(x, y, int(best_row[k])) for k, (x, y) in enumerate(cells)]
    return sum(1 for a, b in zip(ref, got) if a != b)


def main():
    s = make_full_solver(w=W, h=H, a=99)
    s.cell_width = CELL
    s.screenshot_w = int(CELL * 5 / 9)
    s.screenshot_h = int(CELL * 7 / 9)
    s.load_img()
    print(f"配置 screenshot={s.screenshot_h}x{s.screenshot_w}, "
          f"实际补丁={2*(s.screenshot_h//2)}x{2*(s.screenshot_w//2)}")
    print("=" * 66)

    cells = [(x, y) for y in range(1, H + 1) for x in range(1, W + 1)]
    rng = np.random.RandomState(7)
    n_wrong_total = 0
    n_right_total = 0
    trials = 6
    for t in range(trials):
        img = rng.randint(0, 256, (H * CELL, W * CELL, 3), dtype=np.uint8)
        s.img = img
        r = run_case(s, img, cells, use_wrong_patch=False)
        w = run_case(s, img, cells, use_wrong_patch=True)
        n_right_total += max(r, 0)
        n_wrong_total += max(w, 0)
        print(f"  trial {t}: 正确补丁分歧={r:3d}   错误补丁分歧={w:3d}"
              + ("  (越界)" if w < 0 else ""))

    print("=" * 66)
    print(f"正确补丁累计分歧 = {n_right_total}")
    print(f"错误补丁累计分歧 = {n_wrong_total}")
    if n_wrong_total > 0 and n_right_total == 0:
        print("✅ 变异检出：错误补丁尺寸必红，正确实现全绿 → 测试有效")
        return 0
    print("❌ 变异未检出：测试可能无效（假绿），需要加强对抗输入")
    return 1


if __name__ == "__main__":
    sys.exit(main())
