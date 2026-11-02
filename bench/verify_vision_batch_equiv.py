# -*- coding: utf-8 -*-
"""验证已接入的批量识别（compare_cells_batch）与逐格 compare_img 逐位等价。

这是接入生产代码后的等价性门：不是"看起来差不多"，而是 480 格逐格比较。
覆盖面：纯色板、噪声板、开/未开混合板、含数字纹理板。

用法: python bench/verify_vision_batch_equiv.py
"""
import os
import sys
import time
import statistics

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver

W, H, CELL = 30, 16, 24


def make_board(seed, kind="mixed"):
    rng = np.random.RandomState(seed)
    img = np.full((H * CELL, W * CELL, 3), 200, np.uint8)
    if kind == "solid":
        return img
    n = int(W * H * 0.6)
    for k in rng.choice(W * H, n, replace=False):
        y, x = divmod(int(k), W)
        img[y * CELL:(y + 1) * CELL, x * CELL:(x + 1) * CELL] = 150
    if kind == "mixed":
        # 再随机画一些"数字纹理"
        for _ in range(120):
            y = rng.randint(0, H * CELL - 4)
            x = rng.randint(0, W * CELL - 4)
            img[y:y + 4, x:x + 4] = rng.randint(0, 255, (4, 4, 3))
    noise = rng.randint(-10, 11, img.shape)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def main():
    s = make_full_solver(w=W, h=H, a=99)
    s.cell_width = CELL
    s.screenshot_w = int(CELL * 5 / 9)
    s.screenshot_h = int(CELL * 7 / 9)
    s.load_img()

    cells = [(x, y) for y in range(1, H + 1) for x in range(1, W + 1)]
    print(f"补丁 {s.screenshot_h}x{s.screenshot_w}  格子 {len(cells)}")
    print("=" * 70)

    all_ok = True
    for kind in ("solid", "mixed", "noise"):
        for seed in (0, 1, 2):
            s.img = make_board(seed, kind)
            # 逐格参考
            ref = [(x, y, s.compare_img(s.cell_screenshot(x, y), True)) for x, y in cells]
            # 批量
            got = s.compare_cells_batch(cells, no_10=True)
            diff = sum(1 for a, b in zip(ref, got) if a != b)
            ok = diff == 0
            all_ok &= ok
            print(f"  {kind:<6} seed={seed}  不一致 {diff:3d}/{len(cells)}  "
                  f"{'✅' if ok else '❌'}")

    print("=" * 70)
    # 计时
    s.img = make_board(0, "mixed")
    def t_batch():
        s.compare_cells_batch(cells, no_10=True)
    def t_ref():
        for x, y in cells:
            s.compare_img(s.cell_screenshot(x, y), True)

    tb = statistics.median([(lambda t0: (t_batch(), time.perf_counter() - t0)[1])(time.perf_counter())
                            for _ in range(3)]) * 1000
    tr = statistics.median([(lambda t0: (t_ref(), time.perf_counter() - t0)[1])(time.perf_counter())
                            for _ in range(3)]) * 1000
    print(f"逐格参考 {tr:8.2f} ms   批量 {tb:8.2f} ms   加速 {tr/tb:5.1f}x")
    print("=" * 70)
    print(f"等价性判定：{'全部逐格一致 ✅' if all_ok else '存在不一致 ❌'}")


if __name__ == "__main__":
    main()
