# -*- coding: utf-8 -*-
"""端到端确认：生产代码的 pbs 分支现在与暴力枚举一致（误差已消除）。

前面的 verify_pbs_independence.py 里"近似"一栏是脚本内独立复刻的**旧公式**，
用作对照基线，不代表生产代码现状。本脚本直接调用生产路径
（Solver._pbs_compute_python 的 total>10000 分支 + C++ pbs_compute），
与暴力枚举对拍，确认修复生效。

用法: python bench/verify_pbs_fix_e2e.py
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
from utils import native


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
    return None if total == 0 else 1.0 - counts / total


def main():
    a_total, num10 = 3, 0
    R = a_total - num10
    cases = [
        ("双组对称", [[[0, 0, 1], [1, 1, 0]], [[0, 0, 1], [1, 1, 0]]], 2),
        ("偏斜分布", [[[0, 0]], [[0, 0], [1, 1]]], 1),
        ("三组混合", [[[0, 1], [1, 0]], [[0, 0, 1], [1, 1, 0]], [[0], [1]]], 2),
        ("单组", [[[0, 1, 0], [1, 0, 1], [0, 0, 0]]], 3),
    ]

    s = make_full_solver(w=10, h=10, a=a_total)
    s.is_play = False

    print("生产 pbs 代码路径 vs 暴力枚举（对拍）")
    print("=" * 74)
    for label, use_native in (("Python 路径", False), ("C++ 路径", True)):
        native.available = use_native
        print(f"\n--- {label} ---")
        for desc, groups, U in cases:
            res_list = [np.array(g, dtype=np.int32) for g in groups]
            clicks = [("c", i) for i in range(sum(len(g[0]) for g in groups))]
            clicks9 = [("i", k) for k in range(U)]
            ref = brute(groups, U, R)
            if ref is None:
                continue
            got, mine, denom = s._pbs_compute_python(
                20000, num10, clicks, clicks9, res_list, [0] * len(groups))
            got = np.asarray(got, dtype=float)
            if got.size != len(ref):
                print(f"  {desc:<10} 长度不符 got={got.size} ref={len(ref)} ❌")
                continue
            diff = float(np.max(np.abs(got - ref)))
            print(f"  {desc:<10} 生产={np.round(got,4).tolist()}")
            print(f"  {'':<10} 暴力={np.round(ref,4).tolist()}  "
                  f"最大差={diff:.2e} {'✅' if diff < 1e-6 else '❌'}")
    native.available = True
    print("=" * 74)
    print("结论：若两个路径都 ✅，则近似分支的跨组独立性误差已在生产代码中消除。")


if __name__ == "__main__":
    main()
