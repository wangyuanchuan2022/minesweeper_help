# -*- coding: utf-8 -*-
"""证明"增量哈希 == 全盘重算哈希"不变式（评审要求的硬证据）。

=================== 为什么必须单独证明 ====================
之前观察到 hash_calls 42.4万→145万、entries 6.2万→22.7万，
这只能说明 memo 行为变了，**不能单独把根因锁定为 cell_mix 不可加**。
必须直接证明：对任意棋盘、任意格子的任意旧值→新值，
    增量结果 == 全盘重算结果
覆盖：单步、连续多步、反向更新（改回去）、边界值（0/9/10/超9）、
      多个位置、随机棋盘。

用 C++ 导出的 `_hash_probe` 钩子逐步核对。

用法: python bench/verify_incremental_hash.py
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                       # noqa: E402

import utils.native as nat               # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def probe(board, cells):
    """返回 [(增量, 全盘, 相等)]。"""
    return nat.mscore._hash_probe(np.asarray(board, np.int32), cells)


def main():
    print("=" * 92)
    print("不变式证明：增量哈希 == 全盘重算哈希")
    print("=" * 92)

    # ---------- 1) 单步：任意位置、任意旧值→新值 ----------
    print("\n1) 单步更新（覆盖所有位置 × 所有合法值组合）")
    W2, H2 = 8, 6                    # 小棋盘：(h+2)x(w+2) = 6x8 = 48 格
    n = W2 * H2
    base = [9] * n
    values = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 15]
    bad = []
    for pos in range(n):
        for old in (9, 0, 10):
            b = list(base)
            b[pos] = old
            for new in values:
                res = probe(b, [(pos, new)])
                if not res[0][2]:
                    bad.append((pos, old, new, res[0][0], res[0][1]))
    check(f"单步 {n} 位置 × 3 旧值 × {len(values)} 新值 全部一致",
          not bad, f"首个失败 {bad[0] if bad else ''}")

    # ---------- 2) 连续多步（累积增量） ----------
    print("\n2) 连续多步累积（随机 200 步，每步都核对）")
    random.seed(20261103)
    b = [9] * n
    cells = []
    expect = []
    for _ in range(200):
        pos = random.randrange(n)
        new = random.choice(values)
        b[pos] = new
        cells.append((pos, new))
        expect.append(list(b))
    res = probe([9] * n, cells)
    bad_step = [i for i, r in enumerate(res) if not r[2]]
    check("连续 200 步每步增量 == 全盘重算", not bad_step,
          f"失败步 {bad_step[:5]}")

    # ---------- 3) 反向更新（改回去） ----------
    print("\n3) 反向更新（改回原值，增量应可逆）")
    pos = 17
    b0 = [9] * n
    r = probe(b0, [(pos, 5), (pos, 9)])
    check("改 9→5→9 两步每步增量 == 全盘重算", all(x[2] for x in r),
          f"{[x[2] for x in r]}")

    # ⚠️ 上一版这里写错了：拿 r0[0][1]（**第 0 步之后**的全盘值）当"初始哈希"，
    # 但那一步已经把该格改成 5，所以当然不等于初始棋盘。
    # 正确做法：直接对"初始棋盘"做一次 probe（cells 为空则返回空列表），
    # 改用一次 no-op 更新（把某格从 9 改成 9）来取初始全盘值。
    init_full = probe(b0, [(0, 9)])[0][1]      # 9→9，棋盘未变，全盘值即初始值
    back_full = r[-1][1]                        # 改回 9 之后的全盘值
    check("改回后全盘哈希与初始相同", init_full == back_full,
          f"{init_full} vs {back_full}")
    check("改回后增量哈希也与初始相同", r[-1][0] == init_full,
          f"{r[-1][0]} vs {init_full}")

    # ---------- 4) 随机棋盘 × 随机多步 ----------
    print("\n4) 随机初始棋盘 × 随机多步（20 轮 × 50 步）")
    all_ok = True
    for trial in range(20):
        init = [random.choice(values) for _ in range(n)]
        b = list(init)
        cells = []
        for _ in range(50):
            pos = random.randrange(n)
            new = random.choice(values)
            b[pos] = new
            cells.append((pos, new))
        res = probe(init, cells)
        if not all(r[2] for r in res):
            all_ok = False
            print(f"     trial {trial} 失败")
            break
    check("20 轮随机棋盘各 50 步全部一致", all_ok)

    # ---------- 5) 真实棋盘尺寸 ----------
    print("\n5) 真实棋盘尺寸（30x16 → (h+2)x(w+2)=18x32=576 格）")
    W2r, H2r = 32, 18
    nr = W2r * H2r
    init = [9] * nr
    cells = [(random.randrange(nr), random.choice(values)) for _ in range(100)]
    res = probe(init, cells)
    check(f"576 格棋盘 100 步全部一致", all(r[2] for r in res),
          f"失败步 {[i for i, r in enumerate(res) if not r[2]][:5]}")

    print()
    print("=" * 92)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print("结论：增量哈希与全盘重算**逐位一致**（覆盖单步/多步/反向/")
        print("      边界值/随机棋盘/真实尺寸）。不变式成立。")
    else:
        print("结论：不变式不成立，必须回退到已验证版本。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
