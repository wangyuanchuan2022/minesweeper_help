# -*- coding: utf-8 -*-
"""内存闸门负向自测：超限必须拒绝，正常规模必须放行（L1-15 纪律）。

用法: python bench/verify_wr_guard_gate.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from utils.probability import ProbabilityMixin


class Fake:
    """最小宿主：只提供闸门需要的属性。"""

    def __init__(self):
        self._wr_mem_warned = set()


def main():
    gate = ProbabilityMixin.__dict__["_wr_memory_ok"]
    ok = True

    print("=" * 70)
    print("win_rate 内存闸门自测")
    print("=" * 70)
    print("公式：stage2_bytes = total × (1+|clicks|+|clicks9|) × 2304B，上限 1.5GB")
    print()

    cases = [
        # (说明, clicks数, clicks9数, total, 期望放行)
        ("生产典型（n_cand=13, total=28）", 10, 2, 28, True),
        ("生产最大（n_cand=19, total=32）", 15, 3, 32, True),
        ("中盘中等（total=1000, n_cand=20）", 15, 4, 1000, True),
        ("大局面（total=40320, n_cand=20）", 15, 4, 40320, False),
        ("极端（total=40320, n_cand=480）", 8, 471, 40320, False),
    ]
    for desc, nc, n9, total, expect in cases:
        clicks = [(i, 0) for i in range(nc)]
        clicks9 = [(i, 1) for i in range(n9)]
        got = gate(Fake(), clicks, clicks9, total)
        mark = "✅" if got == expect else "❌"
        if got != expect:
            ok = False
        n_cand = 1 + nc + n9
        mem = total * n_cand * 2304.0 / 1e6
        print(f"  {mark} {desc:<38} n_cand={n_cand:>4} 预计 {mem:>9.1f} MB "
              f"→ {'放行' if got else '拒绝'}（期望 {'放行' if expect else '拒绝'}）")

    print()
    print("-" * 70)
    if ok:
        print("结论：✅ 闸门行为正确（正常规模放行、超限拒绝）")
        print("      负向用例已覆盖：若闸门被改成恒 True，上面两条'拒绝'会变 ❌")
    else:
        print("结论：⚠️ 闸门行为不符预期")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
