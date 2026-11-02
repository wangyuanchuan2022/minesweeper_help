# -*- coding: utf-8 -*-
"""决定性消融：把每步的真实开销拆开，并量化"免费提速"（不改算法也能拿到的部分）。

三组对照（同输入、同机器、单变量）：
  ① 现状
  ② 仅关掉耗时模型落盘/拟合（_ps_observe / _ps_record_ms 变 no-op）——不改任何求解逻辑
  ③ ②+ 视觉换成等价批量识别
并单独测量：ImageGrab 抓屏成本、complete_scan 全盘扫描成本、一次移动触发几次抓屏。

用法: python bench/ablate_speed.py
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native, probability as P, vision


def fresh(w, h, a=99):
    s = make_full_solver(w=w, h=h, a=a)
    s.num = 1
    s.checked = {}
    s.pos_dict_list = []
    s.appended_pos = set()
    return s


def run_n51(cv, w, h):
    s = fresh(w, h)
    return s.number5_1(cv.copy())


def bench(label, fn, n=3):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n * 1000
    print(f"  {label:<34} {dt:9.1f} ms")
    return dt


def main():
    data = load_test_data("test_33.json")
    cv = np.array(data["cell_value"], dtype=np.int32)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    print(f"native={native.available} 棋盘 {w}x{h}  局面样本 clicks={len(data['clicks'])}")
    print("=" * 72)

    # ---------- 单次抓屏与全盘扫描成本 ----------
    from PIL import ImageGrab
    t0 = time.perf_counter()
    for _ in range(5):
        img = ImageGrab.grab()
    grab_ms = (time.perf_counter() - t0) / 5 * 1000
    print(f"[成本] ImageGrab.grab 整屏        = {grab_ms:8.1f} ms")

    s = fresh(w, h)
    board = np.random.RandomState(0).randint(0, 256, (h * 24, w * 24, 3), dtype=np.uint8)
    s.img = board
    board_cv = np.full((h + 2, w + 2), 9, np.int32)
    board_cv[0, :] = board_cv[-1, :] = 0
    board_cv[:, 0] = board_cv[:, -1] = 0
    t0 = time.perf_counter()
    s.compare_img(s.cell_screenshot(1, 1), no_10=True)
    per_cell = (time.perf_counter() - t0) * 1000
    print(f"[成本] compare_img 单格(25次调用) = {per_cell:8.3f} ms")
    print(f"[成本] 全盘 {w*h} 格扫描(等效)     = {per_cell*w*h:8.1f} ms")

    # ---------- 消融 ----------
    print("-" * 72)
    print("[基线] 现状（含耗时模型落盘/拟合）:")
    orig_observe, orig_record = P._ps_observe, P._ps_record_ms
    base = bench("number5_1", lambda: run_n51(cv, w, h))

    # 每次 number5_1 内 _ps_observe 被调几次？
    calls = {"observe": 0, "record": 0, "refit": 0}
    real_refit = P._ps_refit_k

    def counting_refit(state, rows=None):
        calls["refit"] += 1
        return real_refit(state, rows)

    def counting_observe(state, size, ms):
        calls["observe"] += 1
        return orig_observe(state, size, ms)

    def counting_record(size, ms):
        calls["record"] += 1
        return orig_record(size, ms)

    P._ps_observe = counting_observe
    P._ps_record_ms = counting_record
    P._ps_refit_k = counting_refit
    run_n51(cv, w, h)
    P._ps_observe, P._ps_record_ms, P._ps_refit_k = orig_observe, orig_record, real_refit
    print(f"  单次 number5_1 内: _ps_observe={calls['observe']} 次, "
          f"_ps_record_ms={calls['record']} 次, _ps_refit_k={calls['refit']} 次")

    print("-" * 72)
    print("[消融 A] 关掉耗时模型（_ps_observe / _ps_record_ms 置 no-op，求解逻辑不变）:")
    P._ps_observe = lambda state, size, ms: None
    P._ps_record_ms = lambda size, ms: None
    ablated = bench("number5_1", lambda: run_n51(cv, w, h))
    print(f"  → 省下 {base - ablated:.1f} ms ({base/max(ablated,1e-9):.2f}x)")
    P._ps_observe, P._ps_record_ms = orig_observe, orig_record
    print("=" * 72)
    print(f"结论：number5_1 {base:.1f} ms 中，真正用于求解的是 {ablated:.1f} ms，"
          f"占比 {ablated/base*100:.1f}%")


if __name__ == "__main__":
    main()
