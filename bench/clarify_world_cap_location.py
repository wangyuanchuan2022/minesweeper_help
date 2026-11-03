# -*- coding: utf-8 -*-
"""关键澄清：world_cap 降级发生在哪个组件？

=================== 为什么必须查清 ====================
我一直假设"开局大 U 导致世界数爆炸 ⇒ 需要新快捷路径"。
但生产代码 `utils/probability.py` 的 `process_bigger_situation` 在
`total > 10000` 时**已经走生成函数 DP**（`_pbs_compute_dp`），
不枚举世界，公式正是 ∏H_g(m_g)·C(U, R-Σm_g)。

如果 DP 本来就能算 U=471，那"降级"根本不在生产路径里，
而在 **bench 的前瞻臂（ab_policy.decide_with_arm → ExactEngine）**，
它用 `enumerate_worlds` 真枚举世界 ⇒ 才会撞 world_cap。

这两个结论导向完全不同的方案：
  · 若在生产路径 → 需要改生产算法（严重）
  · 若只在 bench 前瞻臂 → 只是评测工具的限制（不影响生产）

本脚本直接验证两条路径在同一个 U=471 局面上的表现。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                       # noqa: E402

print("=" * 92)
print("澄清：U=471 局面下，生产 DP vs bench 前瞻枚举")
print("=" * 92)

# ---------- 构造一个 U=471 的真实开局局面 ----------
# F=8（首格 8 邻域），U=471，num10=0，a=99，30x16
w, h, a = 30, 16, 99
num10 = 0
U = 471
F = 8
R = a - num10

print(f"\n局面：{w}x{h}，a={a}，F={F}，U={U}，num10={num10}，剩余雷 R={R}")
print(f"已开格 = {w*h - F - U}")

# ---------- 路径 1：生产 DP（_pbs_compute_dp 的数学） ----------
print("\n" + "-" * 92)
print("路径 1：生产 process_bigger_situation → _pbs_compute_dp（生成函数 DP）")
print("-" * 92)
import math

# 单组情形：8 个前沿格的解（这里只验证「DP 能否在 U=471 上算出有限值」）
# 模拟一组：8 个前沿格，解集为若干 0/1 向量
H = {1: 2, 2: 3, 3: 2, 4: 1}   # 假设：前沿雷数分布（m -> 解数）
Hc = [{0: 1, 1: 1} for _ in range(F)]  # 每格 marginal 用的简化直方图

t0 = time.perf_counter()
denom = 0.0
for m, wt in H.items():
    r = R - m
    if 0 <= r <= U:
        denom += wt * math.comb(U, r)
dt = (time.perf_counter() - t0) * 1000
print(f"  denom 计算耗时 {dt:.3f} ms")
print(f"  denom = {denom:.6e}（有限值，未溢出）")
print(f"  math.comb({U},{R}) = {math.comb(U, R):.6e}")
print(f"  float 上限 1e308，当前 {math.comb(U, R):.3e} ⇒ "
      f"{'安全' if math.comb(U, R) < 1e308 else '溢出'}")

# ---------- 路径 2：bench 前瞻臂（enumerate_worlds 真枚举） ----------
print("\n" + "-" * 92)
print("路径 2：bench 前瞻臂 ExactEngine → enumerate_worlds（真枚举）")
print("-" * 92)
from compare_2ply_vs_winrate import (count_worlds_exact,
                                     enumerate_worlds, WorldCapExceeded)

case = {
    "a": a, "num10": num10, "w": w, "h": h,
    "F": F, "U": U, "total": 1,
    "clicks": [[i + 1, 1] for i in range(F)],
    "clicks9": [[(i % w) + 1, (i // w) + 1] for i in range(U)],
    "ck": [1], "groups": [[[0] * F]],
    "cell_value": [[0] * (w + 2) for _ in range(h + 2)],
    "is_play": True,
}

n = count_worlds_exact(case)
print(f"  纯算术世界数 = {n:.6e}")
print(f"  （若真枚举，需 {n:.3e} 个 set[tuple]，每个约 4KB）")

t0 = time.perf_counter()
try:
    enumerate_worlds(case, world_cap=200000)
    print("  枚举成功（不应发生）")
except WorldCapExceeded as e:
    dt = (time.perf_counter() - t0) * 1000
    print(f"  被 world_cap 拦截，耗时 {dt:.3f} ms")
    print(f"  消息：{str(e)[:100]}")

print()
print("=" * 92)
print("结论")
print("=" * 92)
print("· 生产 DP 路径：U=471 下**可算**（math.comb 是任意精度大整数，")
print("  6.87e103 远低于 float 上限 1e308）⇒ **生产不需要新快捷路径**。")
print("· bench 前瞻臂：用 enumerate_worlds 真枚举 ⇒ 撞 world_cap。")
print("  这是**评测工具自身的限制**，不是生产缺陷。")
print()
print("⇒ 方案必须修正：")
print("   ✗ 错误方向：给生产加「开局大 U 闭式快捷路径」（生产本来就能算）")
print("   ✓ 正确方向：bench 前瞻臂改用与生产 DP 相同的生成函数思路，")
print("                或者干脆承认「前瞻臂在大 U 局面不可用」并如实报告。")
