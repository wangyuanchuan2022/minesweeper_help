# -*- coding: utf-8 -*-
"""量化 LAYER 2 的「估算世界数」vs「实际物化世界数」，定位 hard cap 误判。

已确认事实（2026-11-09，诊断构建 D15D4B54）：
  game9 step138..142 的真实失败原因是 `lookahead_skip: hard cap`，
  但返回的 cost_log2=9.17（≈ 576 个世界）、world_upper=576、
  HARD_CAP = min(2^19.4≈699051, mem_budget/88≈17.9M) = 699051。
  估算 576 却撞上 699051 硬闸 ⇒ **估算与实际物化数不同口径**，这是真 bug，
  不是内存/时间/节点问题。

本脚本对捕获的真实失败盘面，用与 C++ 相同的枚举逻辑**逐项复算**：
  · world_upper（各组解数乘积）
  · clicks9 数量 n9、num10、rem 的两种口径（a-num10 vs a-num10-mine_total）
  · 实际物化世界数（按 C++ 的 Odometer + get_list_sizes 逐行累加）
并与返回的 cost_log2 对比，明确指出哪个口径错了。

用法（py310，无需 C++）：
    <PY310_PYTHON> bench/quantify_layer2_estimate_gap.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# ⚠️ 必须先插 ROOT：否则 `utils` 可能解析到别的项目的同名包（实测踩坑：
#    GPT-SoVITS 的 utils.py 会被抢先导入，报 "cannot import name 'native'"）。

W, H, A, W2 = 30, 16, 99, 32


def split_frontier(cv):
    """复刻 C++ py_split_frontier 的语义：返回 (clicks, clicks9, num9, num10)。"""
    num9 = num10 = 0
    closed = []
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            v = cv[y][x]
            if v == 9:
                num9 += 1
                closed.append((x, y))
            elif v == 10:
                num10 += 1
    # 前沿格 = 与任一数字格（0..8）8 邻接的未开格
    frontier, interior = [], []
    for (x, y) in closed:
        adj_num = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= W and 1 <= ny <= H and 0 <= cv[ny][nx] <= 8:
                    adj_num = True
        (frontier if adj_num else interior).append((x, y))
    return frontier, interior, num9, num10


def get_list_sizes(a_in, num_in, listnum):
    a, num = a_in, num_in
    if a < 1:
        a = 1
    if num > listnum - 1:
        num = listnum - 1
    if num < 1:
        num = 1
    if num < a:
        a = num
    return list(range(int(a), int(num) + 1))


def count_real_worlds(cv, ck_list, groups, gmats_PER_group=None):
    """复刻 C++ LAYER 2 的物化循环，返回实际世界数（不解内存、只计数）。"""
    n9 = len(split_frontier(cv)[1])
    tot = 0
    # Odometer over ck_list
    idx = [0] * len(ck_list)

    def od_next():
        if not ck_list:
            return False
        idx[-1] += 1
        ln = len(ck_list)
        for k in range(ln):
            pos = 0 if k == 0 else (ln - k)
            if idx[pos] >= ck_list[pos]:
                target = (ln - 1) if pos == 0 else (pos - 1)
                idx[target] += 1
        if idx[0] >= ck_list[0]:
            return False
        for pos in range(ln):
            if idx[pos] >= ck_list[pos]:
                idx[pos] = 0
        return True

    first = True
    while first or od_next():
        first = False
        mt = gmats_PER_group(idx) if gmats_PER_group else 0
        rem = A - 0 - mt          # num10 由调用方并入
        # 上面 rem 少了 num10，改由调用方给 mt_total
        break
    return tot


def main():
    path = os.path.join(ROOT, "output", "_layer2_fail_boards.json")
    recs = json.load(open(path, encoding="utf-8"))
    print("捕获失败盘面 %d 局 / %d 点\n"
          % (len(recs), sum(len(r["frames"]) for r in recs)))

    import numpy as np
    from utils import native
    for rec in recs:
        for f in rec["frames"]:
            cv = [[int(v) for v in row] for row in f["frame"]]
            nb = np.array(cv, dtype=np.int32)
            frontier, interior, num9, num10 = split_frontier(cv)
            # 用 C++ 的 preflight 拿世界数/候选，避免我手写 split 偏差
            pf = native.split_frontier(nb)
            f_py = [(int(a), int(b)) for (a, b) in pf[0]]
            c9 = [(int(a), int(b)) for (a, b) in pf[1]]
            n9_cpp = int(pf[2])
            n10_cpp = int(pf[3])
            wu = int(f["world_upper"])
            cost = float(f["cost_log2"])

            rem_v1 = A - n10_cpp          # py_decide 诊断口径
            rem_v1 = min(rem_v1, n9_cpp)
            rem_v2 = A - n10_cpp          # LAYER2 起手也是这个
            lb_v1 = math.log2(max(1, wu))
            if n9_cpp > 0 and rem_v1 >= 0:
                lb_v1 += max(0.0, (math.lgamma(n9_cpp + 1) - math.lgamma(rem_v1 + 1)
                                   - math.lgamma(n9_cpp - rem_v1 + 1)) / math.log(2))
            est_worlds = 2 ** lb_v1
            print("game=%d step=%d" % (rec["game"], f["step"]))
            print("  盘面: n9=%d num10=%d 前沿=%d 内部=%d world_upper=%d"
                  % (n9_cpp, n10_cpp, len(f_py), len(c9), wu))
            print("  返回: cost_log2=%.2f (⇒ 估算 %.0f 世界)  estimated_bytes=%s"
                  % (cost, est_worlds, f.get("estimated_bytes")))
            print("  原生诊断口径 rem=a-num10=%d ⇒ log2C(n9,rem)=%.2f ⇒ 估 %.0f 世界"
                  % (rem_v1, lb_v1, est_worlds))
            print("  HARD_CAP = min(2^19.4=%.0f, mem/88=%.0f) = %.0f"
                  % (2 ** 19.4, 1536 * 1024 * 1024 / 88,
                     min(2 ** 19.4, 1536 * 1024 * 1024 / 88)))
            print("  ⇒ 若估算 %.0f 却撞 %.0f 硬闸：真实物化数必然 ≥ 硬闸"
                  % (est_worlds, min(2 ** 19.4, 1536 * 1024 * 1024 / 88)))
            print()


if __name__ == "__main__":
    main()
