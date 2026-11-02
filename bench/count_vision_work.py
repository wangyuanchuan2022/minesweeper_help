# -*- coding: utf-8 -*-
"""决定性测量：一次「大局面决策」里到底扫了多少次屏、识别了多少格。

大情况下最贵的不是枚举（实测 C++ part_solve 仅 2ms 级），而是**反复整盘重识别**。
本脚本统计 _number5_1_core 一次调用内的 complete_scan 次数与累计识别格数。

用法: python bench/count_vision_work.py
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
from utils import native, probability as P


def run(cv, w, h, stats):
    s = make_full_solver(w=w, h=h, a=99)
    s.num = 1
    s.checked = {}
    s.pos_dict_list = []
    s.appended_pos = set()

    real_scan = s.complete_scan

    def scan(cell_value, no_10=True):
        n_cells = int(np.sum(cell_value == 9))
        t0 = time.perf_counter()
        out = real_scan(cell_value, no_10)
        dt = time.perf_counter() - t0
        stats["scans"] += 1
        stats["cells"] += n_cells
        stats["scan_ms"] += dt
        return out

    s.complete_scan = scan

    real_cmp = s.compare_img
    cmp_calls = [0]

    def cmp(img, no_10):
        cmp_calls[0] += 1
        return real_cmp(img, no_10)

    s.compare_img = cmp

    t0 = time.perf_counter()
    s.number5_1(cv.copy())
    stats["total_ms"] = (time.perf_counter() - t0) * 1000
    stats["compare_calls"] = cmp_calls[0]
    return s


def main():
    data = load_test_data("test_33.json")
    cv = np.array(data["cell_value"], np.int32)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    print(f"native={native.available}  棋盘 {w}x{h}  未开格 num9={data['num9']}  前沿 clicks={len(data['clicks'])}")

    runs = []
    for _ in range(3):
        st = {"scans": 0, "cells": 0, "scan_ms": 0.0, "total_ms": 0.0, "compare_calls": 0}
        run(cv, w, h, st)
        runs.append(st)

    def med(k):
        return statistics.median(r[k] for r in runs)

    print("=" * 70)
    print(f"单次 number5_1：complete_scan 调用 {med('scans'):.0f} 次")
    print(f"               累计识别格数       {med('cells'):.0f} 格")
    print(f"               累计视觉耗时       {med('scan_ms')*1000:.1f} ms")
    print(f"               总墙钟             {med('total_ms'):.1f} ms")
    print(f"               视觉占比           {med('scan_ms')*1000/med('total_ms')*100:.1f}%")
    per_cell = med("scan_ms") * 1000 / max(med("cells"), 1)
    print(f"               单格识别           {per_cell:.3f} ms/格")
    print("=" * 70)
    print("说明：number5_1 收尾会把整盘重置为 9 再全量重扫（_number5_1_core 末尾），")
    print("      因此大局面每决策一次 = 一次全盘重识别，与局面难度无关、随棋盘面积线性增长。")


if __name__ == "__main__":
    main()
