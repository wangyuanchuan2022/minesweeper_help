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
class WorldCapExceeded(Exception):
    """世界数超过上限：必须在**枚举前/枚举中**中止，不能先枚举再判断。"""


# 世界数的硬上限（默认）。标定：一个世界是 set[tuple]（约 99 个 tuple），
# 实测约 4KB；100 万世界 ≈ 4GB —— 已接近整机风险线。
# 生产实际用例世界数最大 90（见 bench/data/wr_cases_big.json），
# 取 200000 留 2000x 余量，同时把最坏内存限制在约 800MB。
WORLD_CAP_DEFAULT = 200000


def count_worlds_exact(case, cap=None):
    """**只算数量、不枚举**：用组合数直接求和，零内存。

    这是防爆的核心：世界数 = Σ_组合 C(U, rem)，纯算术可算，
    因此可以在分配任何内存**之前**判断是否超限。

    ⚠️ 用 log 位长预估做 early-out：U 很大时 math.comb(U, rem) 本身
    会算出上千位的整数（虽不占多少内存，但属于无意义计算）。
    当累计值的 bit_length 已远超 cap 的 bit_length 时立即返回。
    """
    import itertools
    import math
    a = case["a"]
    num10 = case["num10"]
    U = len(case["clicks9"])
    ck = case["ck"]
    groups = case["groups"]
    total = 0
    cap_bits = (cap.bit_length() + 8) if cap is not None else None
    for idx in itertools.product(*[range(c) for c in ck]):
        mt = 0
        for gi, sel in enumerate(idx):
            row = groups[gi][sel]
            mt += sum(int(v) for v in row)
        rem = a - num10 - mt
        if rem == 0:
            total += 1
        elif 0 < rem <= U:
            # 位长早退：C(U,rem) 的位长约 rem*log2(U/rem) 量级；
            # 若已明显超出 cap 位数，直接返回哨兵值，不做精确 comb
            if cap_bits is not None:
                est_bits = rem * max(1.0, math.log2(max(U, 2) / max(rem, 1)))
                if est_bits > cap_bits + 64:
                    return 1 << (cap_bits + 64)      # 明确超限的哨兵
            total += math.comb(U, rem)
        if cap is not None and total > cap:
            return total          # 已超限，early return（不必继续精确）
    return total


def validate_case_or_raise(case, u_hard_max=60):
    """局面合法性校验：把"显然不是真实局面"的情况尽早挡在概率计算之外。

    ==================== 为什么需要（实测事故）====================
    2000 局内存实测撞到这样的决策点：
        F=8,  U=471, num10=0, a=99    ← 整盘 471 格被当成"内部未开格"
        F=32, U=104, num10=69         ← 看似正常，但世界数 5.1e21
    前者是识别/状态异常；后者是"内部格异常多"导致的组合爆炸。
    正常对局（30x16/99 雷）实测 **U ≤ 7**（33 个采集用例 U 最大值 = 7）。

    若不挡住，`Σ C(U, rem)` 会到 10^21 ~ 10^148 量级：
      · 任何枚举都必然 OOM
      · 更根本的是：这种局面不该被当作有效局面进入概率分支

    ==================== 阈值选取依据 ====================
    u_hard_max 默认 **60**：
      · 真实实测最大 7 ⇒ 给 8.5 倍余量，正常对局绝不误伤
      · 异常局面 104 / 471 ⇒ 稳稳挡住
      · 60 时最坏世界数 C(60,30)≈1.2e17 仍远超 world_cap，
        但会被下一道"世界数闸"接手拦住（本闸只管"局面荒谬"）
    校验项：
      ① 0 ≤ num10 ≤ a
      ② U ≤ w*h
      ③ U ≤ u_hard_max
    非法即抛 ValueError，调用方按"识别异常"处理（重扫/回退），
    而不是硬算一个毫无意义的概率。
    """
    a = int(case["a"])
    num10 = int(case["num10"])
    w = int(case["w"])
    h = int(case["h"])
    U = len(case["clicks9"])
    F = len(case["clicks"])
    if num10 < 0 or num10 > a:
        raise ValueError(f"局面非法：num10={num10} 不在 [0, a={a}]")
    if U > w * h:
        raise ValueError(f"局面非法：内部未开格 {U} 超过盘面 {w * h}")
    if U > u_hard_max:
        raise ValueError(
            f"局面非法：内部未开格 U={U} > {u_hard_max}"
            f"（正常对局实测 U≤7，疑似识别异常；F={F} a={a} num10={num10}）")
    return True


def enumerate_worlds(case, validate=True, world_cap=WORLD_CAP_DEFAULT):
    """枚举所有一致世界，返回 (worlds, clicks, clicks9, base_mines)。

    每个 world = set[(x,y)]，含该布局下**全部**雷格（已标记雷 + 前沿雷 + 内部雷）。
    与 cpp/mscore.cpp 阶段1 的判定对应：
        rem = a - num10 - mine_total
        rem == 0            → 接受
        a > num10 + mt + |clicks9| → 拒绝
        否则 对 C(|clicks9|, rem) 的每个组合接受

    ⚠️ **世界数上限（world_cap）**：这是防内存爆炸的第一道闸。
    历史事故：调用方（如 ExactEngine）习惯"先构造再检查世界数"，
    但构造过程本身就把全部世界枚举进内存了 ⇒ 检查形同虚设。
    典型爆炸：U=30、rem=15 时 C(30,15)=1.55 亿个世界，每个 set[tuple] 约 4KB
    ⇒ 理论 600GB。实测跑到 20GB+ 把整机顶爆。
    因此本函数在**分配任何内存之前**先用 count_worlds_exact 算总量，
    超限直接抛 WorldCapExceeded（调用方必须捕获并降级，不得吞掉继续跑）。

    ⚠️ 守恒断言（validate=True 时启用）：任何世界都必须满足
        ① |world 中落在 w*h 盘面内的格| == a
        ② base_mines ⊆ world，且 world 内无已开格
        ③ world 内格数 == num10 + frontier_mines + inner_mines
    这是本轮最重要的一道闸：曾经因列号错位导致世界少算雷（95/96/97 而非 99），
    而所有下游读数（V*、safety、各臂排名）都"自洽地错"，极难发现。
    """
    # ---- 闸 0：局面合法性（比世界数闸更早，防"天文数字"）----
    # 事故实录：2000 局实测撞到 U=471、num10=0、a=99 的局面
    # （识别/状态异常导致整盘被当成"内部未开格"），
    # 世界数 = Σ C(471, rem) 达到 10^148 量级 —— 任何上限检查都只能是
    # "早退"，但**这种局面根本不该进入概率计算**（把它误当有效局面的
    # 下游代码会试图枚举它）。因此在最前面做合法性校验。
    validate_case_or_raise(case)
    a = case["a"]
    num10 = case["num10"]
    w = int(case["w"])
    h = int(case["h"])
    U = len(case["clicks9"])

    # ---- 闸 1：枚举前先算总量（零内存）----
    if world_cap is not None:
        n_est = count_worlds_exact(case, cap=world_cap)
        if n_est > world_cap:
            raise WorldCapExceeded(
                f"世界数 {n_est:,} > 上限 {world_cap:,}"
                f"（U={U}，纯算术预算，未分配内存）")

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

    # ⚠️ 内存热点（2000 局爆炸的直接元凶，实测）：
    # emit() 每个世界都做 3 次 set 拷贝（set(base_mines) / |= set(frontier) /
    # |= set(inner)），每个世界约 99 个 tuple。n_worlds=114,870 时
    # 单个 set 约 4 KB ⇒ 光 worlds 列表就 ~460 MB（实测构造 ExctEngine 时
    # ΔRSS 497.6 MB，且峰值出现在**枚举阶段**而非搜索阶段）。
    #
    # 修复：改用**位集整数**作为世界的内部表示（emit 产出一个 Python int，
    # 每个世界约 480 bit ≈ 100 B），同时**只在需要时才还原成坐标集合**。
    # 守恒断言也改成位运算（`&` / `bit_count`），不再造临时 set。
    #
    # 坐标 → 位索引：bit = (y-1)*w + (x-1)，与 arms_lookahead 的 W2 布局不同，
    # 但本函数只做"雷数守恒"判断，不需要与引擎的盘面布局一致。
    # ⚠️ 注意：cell_value 含 1 格外框（第 0/ w+1 行、第 0/ h+1 列恒为 0），
    # 必须**排除外框**再算位索引，否则 (y-1)*w+(x-1) 会出现负位移。
    def _in(x, y):
        return 1 <= x <= w and 1 <= y <= h

    base_bits = 0
    for (x, y) in base_mines:
        if _in(x, y):
            base_bits |= 1 << ((y - 1) * w + (x - 1))
    opened_bits = 0
    for (x, y) in opened_cells:
        if _in(x, y):
            opened_bits |= 1 << ((y - 1) * w + (x - 1))
    n_cells = w * h
    full_bits = (1 << n_cells) - 1

    def _coords_to_bits(coords):
        b = 0
        for (x, y) in coords:
            if _in(x, y):
                b |= 1 << ((y - 1) * w + (x - 1))
        return b

    def emit(frontier_mines, inner_mines):
        m = base_bits | _coords_to_bits(frontier_mines) | _coords_to_bits(inner_mines)
        if validate:
            inside = m & full_bits
            if inside.bit_count() != a:
                raise AssertionError(
                    f"世界雷数 {inside.bit_count()} != a={a}（前沿雷 "
                    f"{len(frontier_mines)} 内部雷 {len(inner_mines)} "
                    f"基底 {len(base_mines)}）")
            if (base_bits & ~m) != 0:
                raise AssertionError("基底已标记雷未全部包含在世界内")
            if (m & opened_bits) != 0:
                raise AssertionError("世界把已开格当成了雷")
            if inside.bit_count() != num10 + len(frontier_mines) + len(inner_mines):
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
