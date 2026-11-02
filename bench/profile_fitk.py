# -*- coding: utf-8 -*-
"""验证：耗时模型自适应拟合的开销随样本量如何增长（是否为热路径退化）。"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from utils import probability as P
from utils import native


def bench(size_n, sample):
    """用 n 条样本测 _ps_fit_k / _ps_observe 的耗时。"""
    rows = []
    for k in range(size_n):
        rows.append({"ts": "2026-09-04T22:54:21", "size": 30 + (k % 20),
                     "ms": 1.0 + (k % 7) * 0.1, "native": True})

    t0 = time.perf_counter()
    k = P._ps_fit_k(rows, True)
    dt_fit = (time.perf_counter() - t0) * 1000

    state = {"ps_native": True, "ps_base_ms": 3.0, "ps_k": 1.1}
    t0 = time.perf_counter()
    P._ps_refit_k(state, rows)
    dt_refit = (time.perf_counter() - t0) * 1000

    return dt_fit, dt_refit, k


def main():
    real_path = P._PS_SAMPLES_PATH
    backup = real_path + ".benchbak"
    if os.path.exists(real_path):
        shutil.copy2(real_path, backup)
    print(f"真实样本文件: {real_path}")
    try:
        with open(real_path, encoding="utf-8") as f:
            real = json.load(f)
        print(f"当前实际样本数: {len(real)}")
        dt_fit, dt_refit, k = bench(len(real), real)
        print(f"  用全部 {len(real)} 条: _ps_fit_k={dt_fit:.1f} ms  _ps_refit_k={dt_refit:.1f} ms  k={k}")
    except Exception as e:
        print("读真实样本失败:", e)

    print("\n样本量 → 拟合耗时（模拟样本，形状与真实一致）:")
    print(f"{'n':>6} {'_ps_fit_k(ms)':>14} {'_ps_refit_k(ms)':>16} {'理论 O(n²)':>12}")
    for n in (50, 100, 200, 400, 800, 1500, 3000):
        dt_fit, dt_refit, _ = bench(n, None)
        print(f"{n:6d} {dt_fit:14.2f} {dt_refit:16.2f} {n*n/1e9*1000:12.0f}x")

    if os.path.exists(backup):
        shutil.move(backup, real_path)


if __name__ == "__main__":
    main()
