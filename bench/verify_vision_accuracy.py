# -*- coding: utf-8 -*-
"""识别准确率实测：用真实模板 + 真实格子图，检查 compare_img 的分类是否可信。

做法（不依赖真实游戏窗口）：
1. 从 image/ 里取每个数字的模板，把它"贴"到一个标准格子里合成已知答案的样本；
2. 跑现状 compare_img，看是否识别为同一数字；
3. 加上实际会遇到的扰动（JPEG 噪点、半像素偏移、亮度漂移）测鲁棒性。

这能回答："18.3x 矢量化的前提——现状识别是对的——是否成立"。

用法: python bench/verify_vision_accuracy.py
"""
import os
import sys

import cv2 as cv
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver

CELL = 24


def main():
    s = make_full_solver(w=30, h=16, a=99)
    s.cell_width = CELL
    s.screenshot_w = int(CELL * 5 / 9)
    s.screenshot_h = int(CELL * 7 / 9)
    s.load_img()
    ph, pw = s.screenshot_h, s.screenshot_w
    print(f"补丁 {ph}x{pw}；模板行数 {len(s.images)}")

    # 统计每个数字实际可用的模板（能落进补丁的）
    usable = {}
    dead = 0
    for digit, row in enumerate(s.images):
        for t in row:
            if t is None:
                continue
            if t.shape[0] <= ph and t.shape[1] <= pw:
                usable.setdefault(digit, []).append(t)
            else:
                dead += 1
    print(f"可用于识别的模板数字: {sorted(usable)}；死模板 {dead} 个")
    print("=" * 70)

    def classify(cell):
        return s.compare_img(cell, no_10=True)

    def make_cell_from_template(t, noise=0, dx=0, dy=0, bright=0):
        """把模板贴到灰底格子里，居中（可加偏移/噪声/亮度）。"""
        cell = np.full((ph, pw, 3), 190, np.uint8)
        th, tw = t.shape[:2]
        y0 = (ph - th) // 2 + dy
        x0 = (pw - tw) // 2 + dx
        y0 = max(0, min(ph - th, y0))
        x0 = max(0, min(pw - tw, x0))
        cell[y0:y0 + th, x0:x0 + tw] = t
        if noise:
            cell = np.clip(cell.astype(np.int16) + np.random.randint(-noise, noise + 1, cell.shape),
                           0, 255).astype(np.uint8)
        if bright:
            cell = np.clip(cell.astype(np.int16) + bright, 0, 255).astype(np.uint8)
        return cell

    print("\n[1] 无扰动：模板贴回格子，现状识别是否还原原数字")
    total = ok = 0
    wrong = []
    for digit, tpls in usable.items():
        for t in tpls:
            got = classify(make_cell_from_template(t))
            total += 1
            if got == digit:
                ok += 1
            else:
                wrong.append((digit, got))
    print(f"    准确率 {ok}/{total} = {ok/total*100:.1f}%")
    if wrong:
        print(f"    错分样本(真值,识别值): {wrong}")

    print("\n[2] 加噪声后的鲁棒性（模拟真实截图的压缩/抗锯齿噪声）")
    for noise in (5, 15, 30):
        total = ok = 0
        for digit, tpls in usable.items():
            for t in tpls:
                got = classify(make_cell_from_template(t, noise=noise))
                total += 1
                ok += (got == digit)
        print(f"    noise=±{noise:<3} 准确率 {ok}/{total} = {ok/total*100:.1f}%")

    print("\n[3] 亚像素偏移（真实游戏格子对齐不会完美）")
    for d in (0, 1, 2):
        total = ok = 0
        for digit, tpls in usable.items():
            for t in tpls:
                got = classify(make_cell_from_template(t, dx=d, dy=d))
                total += 1
                ok += (got == digit)
        print(f"    偏移 {d}px      准确率 {ok}/{total} = {ok/total*100:.1f}%")

    print("\n[4] 亮度漂移（主题/系统缩放差异）")
    for b in (-20, 0, 20):
        total = ok = 0
        for digit, tpls in usable.items():
            for t in tpls:
                got = classify(make_cell_from_template(t, bright=b))
                total += 1
                ok += (got == digit)
        print(f"    亮度 {b:+3d}      准确率 {ok}/{total} = {ok/total*100:.1f}%")

    print("\n" + "=" * 70)
    print("注意：本测是「模板贴回格子」的自洽性检验，偏乐观——真实截图还含")
    print("      字体渲染差异、窗口缩放、半透明叠加。它只能证明'链路本身能工作'，")
    print("      不能证明'真实场景准确率'。后者需要真实截图样本集（当前仓库没有）。")


if __name__ == "__main__":
    main()
