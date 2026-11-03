# -*- coding: utf-8 -*-
"""P0.5 验证：小规模同类开局上，「safety 相同」是否意味着「续玩价值相同」。

=================== 要回答的命题 ====================
在开局大 U 这类"内部格对称"局面上：
  所有内部格的 safety 相同（= 1 - R/U）
问：它们的**续玩价值**（点开后继续获胜的期望）是否也相同？

若相同 ⇒ 前瞻在开局大 U 上选不出更好的格 ⇒ 不做前瞻是正确的（P0.5 成立）
若不同 ⇒ 格点位置影响后续信息量 ⇒ 前瞻有价值（P0.5 不成立）

=================== 关键洞察 ====================
safety 只反映"这一步会不会踩雷"，而前瞻还关心"点开后能拿到多少信息"。
一个内部格即使 safety 相同，它**离前沿的远近**会影响：
  · 点开后若数字 > 0，只开 1 格
  · 点开后若数字 == 0，flood 展开一片（信息量大）
而"数字是否为 0"取决于该格邻域雷数 —— 这依赖**该格周围有多少格是已知的**，
即它离前沿的**距离**。所以对称性在"点开后"就被打破了。

=================== 验证方法（按评审要求：小规模、可完整求解）====================
构造小的"单数字格 + 大量内部格"局面（如 6x6 盘面、U≈20），
使 ExactEngine 能完整枚举并算 V_exact。
比较：
  · 所有内部格的 safety 是否相同（应相同，验证对称性前提）
  · 所有内部格的 after_click 价值是否相同（若不相同的比例高 ⇒ 前瞻有价值）
  · 价值最高的格 vs 随机内部格，差距多少

用法: python bench/verify_p05_symmetric_lookahead.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                       # noqa: E402

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


def build_opening_case(w, h, a, num10, frontier, unknown_u):
    """构造一个"单数字格 + 大量内部格"的对称开局 case（供 ExactEngine 用）。

    做法：
      · cell_value 全 9（未开）
      · 选一个数字格位置，写入其数字（由 frontier 数量决定）
      · clicks = 该数字格的 8 邻域中的未开格（前沿）
      · clicks9 = 其余未开格（内部）
      · groups/ck 给出一个"可行"的前沿解集（这里用最简单的：无约束单解）
    """
    cv = [[9] * (w + 2) for _ in range(h + 2)]
    for y in range(h + 2):
        cv[y][0] = 0
        cv[y][-1] = 0
    for x in range(w + 2):
        cv[0][x] = 0
        cv[-1][x] = 0

    # 选中心偏上一点作为数字格
    cx, cy = w // 2 + 1, 2
    cv[cy][cx] = 0          # 先占位，稍后按实际前沿雷数修正

    # 前沿 = 数字格的 8 邻域（在盘面内的未开格）
    frontier_cells = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = cx + dx, cy + dy
            if 1 <= nx <= w and 1 <= ny <= h:
                frontier_cells.append((nx, ny))

    frontier_set = set(frontier_cells)
    interior_cells = [(x, y) for y in range(1, h + 1) for x in range(1, w + 1)
                      if (x, y) not in frontier_set and (x, y) != (cx, cy)]
    return cv, (cx, cy), frontier_cells, interior_cells


def main():
    print("=" * 96)
    print("P0.5：小规模对称开局上，safety 相同是否 ⇒ 续玩价值相同")
    print("=" * 96)

    from arms_lookahead import ExactEngine, WKey, Budget

    # ---------- 用小盘面构造可完整求解的"对称开局" ----------
    # 规模选择依据（bench 实测）：
    #   6x6=36 格、4 雷、开 1 个数字格 ⇒ 前沿 8、内部 27、
    #   世界数 C(27,4)=17,550 < world_cap=200000 ⇒ 可完整枚举。
    # 更大的 6x6/10 雷会到 843 万世界，超出 cap，无法用于本验证。
    w, h, a = 6, 6, 4
    cv, numcell, frontier, interior = build_opening_case(w, h, a, 0, 8, 0)
    print(f"\n构造局面：{w}x{h}，{a} 雷")
    print(f"  数字格 {numcell}，前沿 {len(frontier)} 格，内部 {len(interior)} 格")

    # 直接构造 case：前沿格无约束（单解），内部格受总雷数约束
    # 注意：这里刻意让前沿"无约束"（groups 只有全 0 解），
    # 以复现"内部格完全对称"的最纯形式
    F = len(frontier)
    case = {
        "a": a, "num10": 0, "w": w, "h": h,
        "F": F, "U": len(interior), "total": 1,
        "clicks": [list(p) for p in frontier],
        "clicks9": [list(p) for p in interior],
        "ck": [1], "groups": [[[0] * F]],       # 前沿无雷（单解）
        "cell_value": cv, "is_play": True,
    }

    try:
        # ⚠️ node_cap 必须足够大：after_click 对每个候选格做一次完整续玩递归，
        # 27 个候选 × 每个数千节点 ⇒ 400000 不够（实测只有 4 个格算出价值，
        # 其余 23 个抛 Budget 被过滤，会得出"样本只有 4 个"的假象）。
        eng = ExactEngine(case, node_cap=50_000_000, time_cap=300,
                          world_cap=200000)
    except Exception as e:                             # noqa: BLE001
        print(f"  构造引擎失败：{type(e).__name__}: {e}")
        return

    print(f"  世界数 = {eng.n_worlds}")
    if eng.n_worlds == 0:
        print("  无一致世界，无法验证")
        return

    wk = WKey.all(eng.n_worlds)
    op = eng.opened0
    cands = eng.candidates(wk, op)
    print(f"  候选格 = {len(cands)}")

    # ---------- 计算每个候选的 safety 与 after_click ----------
    # ⚠️ 预算语义（按评审要求修正）：
    # `Budget` 表示**评估未完成**，不是"败局"也不是"零续玩价值"。
    # 若把它当 None 过滤掉，等于按"是否容易计算"筛选候选 ⇒ 系统性偏倚
    # （实测会得出"只有 4 个内部格可算"的假象，而真实有 27 个）。
    # 正确做法：同一决策点的**全部候选**必须用一致且足够的预算；
    # 任一候选无法完整评估 ⇒ 整个决策点标为"未覆盖"，不纳入任何统计。
    rows = []
    incomplete = []
    for c in cands:
        xy = eng._coord(c)
        s = eng.safety(c, wk)
        try:
            q = eng.after_click(c, wk, op)
        except Budget as e:
            incomplete.append((xy, str(e)))
            q = None
        rows.append({"cell": xy, "safety": s, "value": q,
                     "is_interior": xy in set(interior)})

    if incomplete:
        print(f"\n!! 决策点未覆盖：{len(incomplete)}/{len(cands)} 个候选"
              f"评估未完成（Budget），整个决策点不纳入统计")
        for xy, why in incomplete[:5]:
            print(f"     {xy}: {why}")
        print("   ⇒ 提高 --node-cap / --time-cap 后重跑；"
              "不得用「部分候选」下结论")
        return

    interior_rows = [r for r in rows if r["is_interior"]]
    print(f"\n内部格样本：{len(interior_rows)}（全部候选均已完整评估）")

    if len(interior_rows) < 3:
        print("  内部格太少，无法判断")
        return

    # ---------- 判据 1：内部格 safety 是否全相同 ----------
    saf = [r["safety"] for r in interior_rows]
    saf_spread = max(saf) - min(saf)
    print(f"\n判据 1：内部格 safety 一致性")
    print(f"  safety 范围 [{min(saf):.6f}, {max(saf):.6f}]，极差 {saf_spread:.2e}")
    check("内部格 safety 完全相同（对称性前提成立）", saf_spread < 1e-12,
          f"spread={saf_spread:.3e}")

    # ---------- 判据 2：内部格续玩价值是否全相同 ----------
    vals = [r["value"] for r in interior_rows]
    val_spread = max(vals) - min(vals)
    print(f"\n判据 2：内部格续玩价值（after_click）一致性")
    print(f"  价值范围 [{min(vals):.6f}, {max(vals):.6f}]，极差 {val_spread:.6f}")
    n_distinct = len(set(round(v, 9) for v in vals))
    print(f"  不同取值个数：{n_distinct}/{len(vals)}")
    check("内部格续玩价值**不完全相同**（⇒ 前瞻有信息量）",
          val_spread > 1e-9, f"spread={val_spread:.3e}")

    # ---------- 判据 3：最优格 vs 最差格的差距 ----------
    best = max(interior_rows, key=lambda r: r["value"])
    worst = min(interior_rows, key=lambda r: r["value"])
    gap = best["value"] - worst["value"]
    print(f"\n判据 3：最优内部格 vs 最差内部格")
    print(f"  最优 {best['cell']} value={best['value']:.6f} "
          f"safety={best['safety']:.6f}")
    print(f"  最差 {worst['cell']} value={worst['value']:.6f} "
          f"safety={worst['safety']:.6f}")
    print(f"  差距 {gap:.6f}（相对 {(gap / max(best['value'], 1e-9) * 100):.1f}%）")

    # ---------- 结论 ----------
    print()
    print("=" * 96)
    print("结论")
    print("=" * 96)
    if saf_spread < 1e-12 and val_spread > 1e-9:
        print("P0.5 **不成立**：内部格 safety 确实相同，")
        print("  但**续玩价值不同** ⇒ 格点位置影响后续信息量，")
        print("  前瞻在对称开局上**有**信息量，不是白算。")
        print()
        print("⚠️ 但注意：本局面是「前沿无约束」的极端构造。")
        print("   真实开局前沿受数字约束，结论需在真实局面上复核。")
    elif saf_spread < 1e-12 and val_spread <= 1e-9:
        print("P0.5 **成立**：内部格 safety 与续玩价值都相同，")
        print("  前瞻在对称开局上选不出更好的格 ⇒ 不做前瞻是正确的。")
    else:
        print("内部格 safety 本身就不相同（对称性前提不成立），")
        print("  本局面不适合验证 P0.5，需换构造。")

    print()
    print("=" * 96)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    print("注：PASS/FAIL 只是「判据是否被观测到」，")
    print("    不代表 P0.5 成立与否（那是数据结论，见上方）。")


if __name__ == "__main__":
    main()
