# -*- coding: utf-8 -*-
"""对抗性等价测试：批量识别 vs 逐格参考，在**随机棋盘**上逐格比对。

背景（必须记录）：视觉域评审指出，我最初的 compare_cells_batch 用
screenshot_w=13 建补丁，而 _slice_cell 实得宽 12（奇数被 //2 丢掉）——
合成结构化棋盘上 0/480 一致，但随机棋盘上会出现分歧。
这说明"合成棋盘全一致"是**弱证据**，必须用随机对抗输入才能抓出这类错误。

本脚本就是那个强证据：
  1. 随机噪声棋盘（最坏情况，无语义结构）
  2. 逐格参考 compare_img vs 批量 compare_cells_batch
  3. 要求 0 分歧；一旦有分歧立即打印第一个反例

用法: python bench/verify_vision_adversarial.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver

W, H, CELL = 30, 16, 24


def main():
    s = make_full_solver(w=W, h=H, a=99)
    s.cell_width = CELL
    s.screenshot_w = int(CELL * 5 / 9)
    s.screenshot_h = int(CELL * 7 / 9)
    s.load_img()

    bh, bw = H * CELL, W * CELL
    cells = [(x, y) for y in range(1, H + 1) for x in range(1, W + 1)]
    sh_actual = 2 * (s.screenshot_h // 2)
    sw_actual = 2 * (s.screenshot_w // 2)
    print(f"配置 screenshot={s.screenshot_h}x{s.screenshot_w}  "
          f"实际补丁={sh_actual}x{sw_actual}")
    print("=" * 72)

    total_diff = 0
    cases = 0
    first_bad = None
    rng = np.random.RandomState(12345)
    for trial in range(40):
        # 纯随机噪声：最坏情况
        img = rng.randint(0, 256, (bh, bw, 3), dtype=np.uint8)
        s.img = img
        ref = [(x, y, s.compare_img(s.cell_screenshot(x, y), True)) for x, y in cells]
        got = s.compare_cells_batch(cells, no_10=True)
        d = [(a, b) for a, b in zip(ref, got) if a != b]
        cases += 1
        total_diff += len(d)
        if d and first_bad is None:
            first_bad = (trial, d[0])
        if trial < 5 or d:
            print(f"  trial {trial:2d}: 分歧 {len(d):3d}/{len(cells)}"
                  + (f"   首个反例 {d[0]}" if d else ""))

    print("=" * 72)
    print(f"随机棋盘 {cases} 张，累计分歧 {total_diff}")
    if first_bad:
        print(f"首个反例（trial, (ref, got)）= {first_bad}")
        print("❌ 存在分歧：批量实现与参考实现不等价")
        return 1
    print("✅ 全部一致：批量实现与逐格参考在随机棋盘上逐格等价")


if __name__ == "__main__":
    sys.exit(main())
