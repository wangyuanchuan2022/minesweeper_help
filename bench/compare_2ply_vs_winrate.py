# -*- coding: utf-8 -*-
"""离线对比：纯 safety 选择 vs 一步前瞻(2-ply) vs 完整 win_rate 金标准。

=================== 这个实验要回答什么 ====================
用户给出的关系式（已验证 win_rate ≤ safety）：
    win_rate(c) = safety(c) × P(点击 c 后继续获胜)

当前 `process_bigger_situation` 选格只用 safety 排序 ⇒ 等于把第二项当作 1。
完整 win_rate 想精确算这一项，但代价是指数递归（生产单次最坏 502ms）。

问：**一步前瞻（2-ply）能否近似完整 win_rate 的选择？**
    2-ply(c) = safety(c) × max_{c'} safety_{c}(c')
    其中 safety_{c}(c') 是"假设 c 点开后（新世界集合下）"所有剩余格的
    **完全枚举**（受控规模，无递归），只是"选最大 safety"这一步不递归。

成立就有意义：成本从指数级降到 O(n_cand × nworlds)。

=================== 安全设计（本脚本绝不跑 win_rate 递归）===================
  · 只读已采集的固定用例（n_cand ≤ 19、total ≤ 32、nworlds ≤ 72）
  · 2-ply 计算是纯枚举：对每个候选，按该格邻域雷数把世界分桶，
    再在每桶内数"每格是雷的世界数" → 无递归、无记忆化、无整盘拷贝
  · 金标准值取自 bench/data/wr_before.json（**预先**跑好存盘的），不现场跑

用法: python bench/compare_2ply_vs_winrate.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

UNOPENED, MINE = 9, 10


# ---------------------------------------------------------------------------
# 世界枚举（与 C++ py_win_rate 阶段1 同一语义）
# ---------------------------------------------------------------------------
def enumerate_worlds(case, validate=True):
    """枚举所有一致世界，返回 (worlds, clicks, clicks9, base_mines)。

    每个 world = set[(x,y)]，含该布局下**全部**雷格（已标记雷 + 前沿雷 + 内部雷）。
    与 cpp/mscore.cpp 阶段1 的判定对应：
        rem = a - num10 - mine_total
        rem == 0            → 接受
        a > num10 + mt + |clicks9| → 拒绝
        否则 对 C(|clicks9|, rem) 的每个组合接受

    ⚠️ 守恒断言（validate=True 时启用）：任何世界都必须满足
        ① |world 中落在 w*h 盘面内的格| == a
        ② base_mines ⊆ world，且 world 内无已开格
        ③ world 内格数 == num10 + frontier_mines + inner_mines
    这是本轮最重要的一道闸：曾经因列号错位导致世界少算雷（95/96/97 而非 99），
    而所有下游读数（V*、safety、各臂排名）都"自洽地错"，极难发现。
    """
    a = case["a"]
    num10 = case["num10"]
    w = int(case["w"])
    h = int(case["h"])
    clicks = [tuple(p) for p in case["clicks"]]
    clicks9 = [tuple(p) for p in case["clicks9"]]
    ck = case["ck"]
    groups = case["groups"]

    # 基底盘面里已有的雷（cell_value == 10）与已开格（0..8）
    base_mines = set()
    opened_cells = set()
    cv = case["cell_value"]
    for y in range(len(cv)):
        for x in range(len(cv[0])):
            v = cv[y][x]
            if v == MINE:
                base_mines.add((x, y))
            elif 0 <= v <= 8:
                opened_cells.add((x, y))

    # 盘面内（1..w, 1..h）的格，用于守恒计数（排除外框 0 值行/列）
    board_cells = {(x, y) for y in range(1, h + 1) for x in range(1, w + 1)}

    worlds = []

    def emit(frontier_mines, inner_mines):
        m = set(base_mines)
        m |= set(frontier_mines)
        m |= set(inner_mines)
        if validate:
            inside = m & board_cells
            if len(inside) != a:
                raise AssertionError(
                    f"世界雷数 {len(inside)} != a={a}（前沿雷 {len(frontier_mines)} "
                    f"内部雷 {len(inner_mines)} 基底 {len(base_mines)}）")
            if not (base_mines <= m):
                raise AssertionError("基底已标记雷未全部包含在世界内")
            if m & opened_cells:
                raise AssertionError(f"世界把已开格当成了雷：{sorted(m & opened_cells)[:5]}")
            if len(m & board_cells) != num10 + len(frontier_mines) + len(inner_mines):
                raise AssertionError("雷数分项与总数不守恒")
        worlds.append(m)

    # ⚠️ 关键修正（本轮发现）：groups[gi] 的列 **只覆盖本组自己的前沿格**，
    # 不是全部 clicks。列号 ci 必须映射到该组在 clicks 里的偏移位置。
    # 若直接写 clicks[ci]，组 1 的 8 个雷会被错写到 clicks[0..7]，
    # 与组 0/2/3 的雷互相覆盖 ⇒ 世界雷数少算（实测 95/96/97 而非 99）
    # ⇒ 所有 V*、safety、各臂读数系统性偏移（最隐蔽的一类错误）。
    # 组顺序与 clicks 顺序一致：clicks 由 click_list（分组后同序化）展平而来
    # （见 utils/probability.py:506-511）。
    group_offset = []
    _off = 0
    for g in groups:
        group_offset.append(_off)
        _off += len(g[0])
    if validate and _off != len(clicks):
        raise AssertionError(
            f"组列总数 {_off} != len(clicks) {len(clicks)}（分组与 clicks 未同序）")

    # 前沿格贡献（按 ck 的乘积遍历 —— 用 itertools.product 等价 Odometer）
    import itertools
    for idx in itertools.product(*[range(c) for c in ck]):
        mine_total = 0
        fm = []
        for gi, sel in enumerate(idx):
            row = groups[gi][sel]
            base = group_offset[gi]
            for ci, v in enumerate(row):
                if v == 1:
                    mine_total += 1
                    fm.append(clicks[base + ci])
        rem = a - num10 - mine_total
        if rem == 0:
            emit(fm, [])
            continue
        if a > num10 + mine_total + len(clicks9):
            continue
        if rem < 0 or rem > len(clicks9):
            # 与 C++ 一致：get_list_sizes 会把 rem 钳到 [1, |clicks9|-1]/[1, rem]，
            # 但 rem 为负或超过内部格数时该组合不产生任何合法世界 → 跳过。
            continue
        for combo in itertools.combinations(range(len(clicks9)), rem):
            im = [clicks9[j] for j in combo]
            emit(fm, im)
    return worlds, clicks, clicks9, base_mines


# ---------------------------------------------------------------------------
# safety：某格在给定世界集合里"是雷的世界占比"→ safety = 1 - 雷占比
# ---------------------------------------------------------------------------
def safety_of(cell, worlds):
    if not worlds:
        return 0.0
    cnt = sum(1 for w in worlds if cell in w)
    return 1.0 - cnt / len(worlds)


def all_safety(cands, worlds):
    return {c: safety_of(c, worlds) for c in cands}


# ---------------------------------------------------------------------------
# 一步前瞻：对候选 c，假设它安全（= 去掉 c 是雷的世界），
# 按"c 的邻域雷数"分桶（这与真实点开后的世界划分一致），
# 在每个桶里求"下一个格的最大 safety"，再按桶大小加权。
# ---------------------------------------------------------------------------
def neighbors(cell):
    x, y = cell
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if not (dx == 0 and dy == 0)]


def two_ply(cand, others, worlds):
    """返回 safety(c) × E[max_{c'} safety_{c}(c')]。"""
    safe_worlds = [w for w in worlds if cand not in w]
    if not safe_worlds:
        return 0.0
    s_c = len(safe_worlds) / len(worlds)

    # 按 c 邻域雷数分桶（点开后观测到的数字）
    buckets = {}
    for w in safe_worlds:
        v = sum(1 for p in neighbors(cand) if p in w)
        buckets.setdefault(v, []).append(w)
    if not buckets:
        return 0.0

    exp_next_max = 0.0
    for v, ws in buckets.items():
        # 点开 c 后它变成已知数字，其余 world 不变（这里不做数字约束级联，
        # 只是保守地沿用同桶世界集合 → 这正是"不递归"的近似）
        s_next = all_safety([q for q in others if q != cand], ws)
        best = max(s_next.values()) if s_next else 1.0
        exp_next_max += (len(ws) / len(safe_worlds)) * best
    return s_c * exp_next_max


def one_ply(cand, worlds):
    return safety_of(cand, worlds)


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases.json"), encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(ROOT, "bench", "data", "wr_before.json"), encoding="utf-8") as f:
        gold = json.load(f)["results"]

    print("=" * 86)
    print("一步前瞻(2-ply) vs 纯 safety vs 完整 win_rate（离线，无递归）")
    print("=" * 86)
    for ci, case in enumerate(cases):
        worlds, clicks, clicks9, _ = enumerate_worlds(case)
        cands = clicks + clicks9
        if not worlds or not cands:
            continue
        if len(worlds) > 200:
            print(f"[{ci:2d}] 跳过：worlds={len(worlds)} 过大")
            continue

        s1 = all_safety(cands, worlds)
        s2 = {c: two_ply(c, cands, worlds) for c in cands}

        # 金标准（预先存盘的 win_rate 结果）
        g = gold[ci]
        g_res, g_clicks = g["res"], [tuple(p) for p in g["clicks"]]
        g_map = {xy: g_res[k] for k, xy in enumerate(g_clicks)
                 if k < len(g_res)}
        gold_best = max(g_map.items(), key=lambda kv: kv[1]) if g_map else (None, 0)
        # 注意：金标准 res 只覆盖 all_clicks；内部格也可能在内

        pick_s1 = max(cands, key=lambda c: s1[c])
        pick_s2 = max(cands, key=lambda c: s2[c])
        print(f"\n[{ci:2d}] F={case['F']} U={case['U']} worlds={len(worlds)} "
              f"n_cand={len(cands)}")
        print(f"     纯 safety 选 {pick_s1}  safety={s1[pick_s1]:.4f}")
        print(f"     2-ply   选 {pick_s2}  2ply={s2[pick_s2]:.4f} "
              f"(safety={s1[pick_s2]:.4f})")
        if pick_s1 != pick_s2:
            print(f"     ⚠️ 两者选择不同")
        else:
            print(f"     ✓ 两者选择相同")
        # 金标准里最大胜率的格
        gb_cell, gb_val = gold_best
        print(f"     金标准(win_rate) 最大 {gb_cell} win_rate={gb_val:.4f}"
              f"{'  ← 与 2-ply 相同' if gb_cell == pick_s2 else ''}"
              f"{'  ← 与 1-ply 相同' if gb_cell == pick_s1 else ''}")

    print()
    print("=" * 86)
    print("结论判据：")
    print("  · 若 2-ply 与金标准选择一致率明显高于 1-ply ⇒ 一步前瞻值得接入")
    print("  · 若两者都经常不中 ⇒ 前瞻深度是必需的，只能优化成本不能降深度")


if __name__ == "__main__":
    main()
