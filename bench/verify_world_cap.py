# -*- coding: utf-8 -*-
"""世界数闸门验收：必须**在分配内存之前**拦住爆炸局面。

=================== 本闸要证明什么 ====================
历史事故：2000 局跑到 20GB+ 被顶爆。根因是"先构造引擎（枚举全部世界）
再检查世界数"，检查形同虚设。典型爆炸规模（纯算术）：
    U=20 rem=10 → C = 184,756
    U=25 rem=12 → C = 5,200,300
    U=30 rem=15 → C = 155,117,520   ← 每个 set[tuple] 约 4KB ⇒ 数百 GB
    U=35 rem=17 → C = 4,537,567,650
    U=40 rem=20 → C = 137,846,528,820

验收项：
  A) count_worlds_exact 纯算术结果与暴力枚举一致（小规模可枚举时）
  B) 超限时 enumerate_worlds **抛异常且不分配内存**（用 tracemalloc 测峰值）
  C) ExactEngine 构造前拦截（不进入 enumerate）
  D) 未超限时行为不变（结果与旧版一致）

用法: python bench/verify_world_cap.py
"""
import json
import os
import sys
import tracemalloc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

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


def make_case(a, num10, U, ck, groups, w=30, h=16, clicks_n=10):
    """构造最小可用 case（只用于测世界数，不要求真盘面）。"""
    return {
        "a": a, "num10": num10, "w": w, "h": h,
        "F": clicks_n, "U": U, "total": 1,
        "clicks": [[1, 1]] * clicks_n,
        "clicks9": [[2 + i, 1] for i in range(U)],
        "ck": ck, "groups": groups,
        "cell_value": [[0] * (w + 2) for _ in range(h + 2)],
        "is_play": True,
    }


def main():
    from compare_2ply_vs_winrate import (count_worlds_exact, enumerate_worlds,
                                         WorldCapExceeded, WORLD_CAP_DEFAULT)

    print("=" * 92)
    print(f"世界数闸门验收（默认 WORLD_CAP_DEFAULT = {WORLD_CAP_DEFAULT:,}）")
    print("=" * 92)

    # ---------- A) 纯算术 vs 暴力枚举一致 ----------
    print("\nA) count_worlds_exact 与真实枚举数量一致（小规模）")
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    agree = 0
    tot = 0
    for ci, c in enumerate(cases):
        n = count_worlds_exact(c)
        worlds, _, _, _ = enumerate_worlds(c, world_cap=None)
        tot += 1
        if n == len(worlds):
            agree += 1
        else:
            print(f"  [{ci:2d}] 算术 {n} != 枚举 {len(worlds)}")
    check(f"33 个真实用例算术/枚举一致（{agree}/{tot}）", agree == tot)

    # ---------- B) 超限：抛异常且几乎不分配内存 ----------
    print("\nB) 超限时抛异常**且不分配内存**（tracemalloc 峰值）")
    # 构造一个必然超限的 case：U=30, rem=15, 单组 1 解
    big = make_case(a=40, num10=24, U=30, ck=[1], groups=[[[0] * 10]], clicks_n=10)
    n_big = count_worlds_exact(big, cap=WORLD_CAP_DEFAULT)
    print(f"  构造局面：U=30, rem≈16 → 理论世界数 {n_big:,}"
          f"（超上限 {WORLD_CAP_DEFAULT:,}）")
    check("该局面确实超限", n_big > WORLD_CAP_DEFAULT,
          f"{n_big} vs {WORLD_CAP_DEFAULT}")

    tracemalloc.start()
    raised = False
    try:
        enumerate_worlds(big, world_cap=WORLD_CAP_DEFAULT)
    except WorldCapExceeded:
        raised = True
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check("超限时抛出 WorldCapExceeded", raised)
    check(f"超限时内存峰值极小（{peak/1024:.0f} KB < 10 MB）",
          peak < 10 * 1024 * 1024, f"peak={peak/1024/1024:.1f}MB")

    # ---------- C) ExactEngine 构造前拦截 ----------
    print("\nC) ExactEngine 在构造时即拦截（不进入枚举）")
    from arms_lookahead import ExactEngine
    raised_e = False
    tracemalloc.start()
    try:
        ExactEngine(big, node_cap=1000, time_cap=5, world_cap=WORLD_CAP_DEFAULT)
    except WorldCapExceeded:
        raised_e = True
    cur2, peak2 = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check("ExactEngine 抛 WorldCapExceeded", raised_e)
    check(f"ExactEngine 拦截时内存峰值极小（{peak2/1024:.0f} KB）",
          peak2 < 10 * 1024 * 1024)

    # ---------- D) 未超限不受影响 ----------
    print("\nD) 未超限时行为不变（与世界数上限无关）")
    ok = 0
    n = 0
    for ci, c in enumerate(cases):
        nw = count_worlds_exact(c)
        if nw > WORLD_CAP_DEFAULT:
            continue
        n += 1
        w1, _, _, _ = enumerate_worlds(c, world_cap=None)
        w2, _, _, _ = enumerate_worlds(c, world_cap=WORLD_CAP_DEFAULT)
        if len(w1) == len(w2):
            ok += 1
    check(f"未超限用例结果一致（{ok}/{n}）", ok == n and n > 0)

    print()
    print("=" * 92)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print("结论：世界数闸门在**分配内存之前**生效，爆炸局面会被立即拦截；")
        print("      未超限局面行为不变 —— 这是 2000 局内存爆炸的根本修复。")
    else:
        print("结论：存在失败项，闸门不可用。")


if __name__ == "__main__":
    main()
