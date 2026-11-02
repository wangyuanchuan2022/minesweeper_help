# -*- coding: utf-8 -*-
"""验证已接入主流程的 DP 聚合：与暴力枚举对拍。

直接调用 Solver._pbs_compute_dp（生产代码路径），与暴力枚举结果比对，
确认本次正确性修复真的生效（而不是只在 bench 里的独立实现正确）。

用法: python bench/verify_pbs_dp_integration.py
"""
import itertools
import os
import sys
from math import comb

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver


def brute(groups, U, R):
    n = sum(len(g[0]) for g in groups)
    counts = np.zeros(n)
    total = 0.0
    for combo in itertools.product(*[range(len(g)) for g in groups]):
        fm = sum(int(sum(groups[gi][combo[gi]])) for gi in range(len(groups)))
        need = R - fm
        if not (0 <= need <= U):
            continue
        w = comb(U, need)
        total += w
        pos = 0
        for gi, g in enumerate(groups):
            for v in g[combo[gi]]:
                if v:
                    counts[pos] += w
                pos += 1
    if total == 0:
        return None
    return 1.0 - counts / total


def main():
    a_total = 3
    num10 = 0
    R = a_total - num10
    cases = [
        ("双组对称", [[[0, 0, 1], [1, 1, 0]], [[0, 0, 1], [1, 1, 0]]], 2),
        ("偏斜分布", [[[0, 0]], [[0, 0], [1, 1]]], 1),
        ("三组混合", [[[0, 1], [1, 0]], [[0, 0, 1], [1, 1, 0]], [[0], [1]]], 2),
        ("无内部格", [[[0, 1], [1, 0]], [[0, 1]]], 0),
        ("单组", [[[0, 1, 0], [1, 0, 1], [0, 0, 0]]], 3),
    ]

    s = make_full_solver(w=10, h=10, a=a_total)
    s.is_play = False

    print("生产代码 _pbs_compute_dp vs 暴力枚举")
    print("=" * 72)
    all_ok = True
    for desc, groups, U in cases:
        res_list = [np.array(g, dtype=np.int32) for g in groups]
        clicks = [("c", i) for i in range(sum(len(g[0]) for g in groups))]
        clicks9 = [("i", k) for k in range(U)]
        ref = brute(groups, U, R)
        if ref is None:
            print(f"  {desc:<10} 无可行世界，跳过")
            continue
        got, mine, denom = s._pbs_compute_dp(num10, clicks, clicks9, res_list)
        diff = float(np.max(np.abs(got - ref))) if len(got) == len(ref) else float("inf")
        ok = diff < 1e-6
        all_ok &= ok
        print(f"  {desc:<10} DP={np.round(got,4).tolist()}")
        print(f"  {'':<10} 暴力={np.round(ref,4).tolist()}  最大差={diff:.2e} "
              f"{'✅' if ok else '❌'}")

    print("=" * 72)
    print(f"结论：生产 DP 路径 {'与暴力枚举完全一致 ✅' if all_ok else '存在不一致 ❌'}")
    print("说明：该函数只在不走 C++ 且 total>10000 时被调用；C++ 侧的近似分支")
    print("      若未同步修改，两条路径会对同一局面给出不同结论（需确认）。")


if __name__ == "__main__":
    main()
