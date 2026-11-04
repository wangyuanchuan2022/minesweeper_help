# -*- coding: utf-8 -*-
"""验证 `after_click` / `V_exact` 的实际求解口径（Advisor 要求）。

=================== 为什么要做这个验证 ====================
我在解释里把 `V*` 说成"该状态的理论最优"、"精确值"。
但核查代码后发现（bench/arms_lookahead.py:368-399）：

    V_exact(wkey, opened):
        terms = saturate(...)              # 免费推理，无截断
        for 每个终端态:
            cands = candidates(wk, op)     # 完整候选集，无剪枝
            best = max(after_click(c) for c in cands)   # 完整递归
        ...
        # 但 _tick() 会检查 node_cap / time_cap，超限抛 Budget

⇒ `V*` 的**准确**含义是：
   「在 `node_cap` 内、对**完整候选集**做**完整递归**（无深度截断）得到的最优值」

它**不是**数学上无条件的理论最优，因为：
  1. 受 node_cap 限制（超限的点被整点丢弃，不进样本）
  2. 受 time_cap 限制（同上）
  3. 依赖"世界等权"假设（Arbiter 布雷机制是否严格等价，未逐位核对）
  4. 依赖 candidates() 的定义（= union & ~opened & ~inter，即"至少在一个世界里
     安全且至少在一个世界里是雷"的格；这是完备的，但若有实现偏差则影响结果）

=================== 本脚本验证什么 ====================
用**已知答案的小型残局**（手算可验证）：
  1. 构造一个世界数极小、结构透明的局面
  2. 手算每格 after_click 的精确值
  3. 与 ExactEngine 输出比对
  4. 提高 node_cap 看结果是否变化（敏感性对照）

判据：
  · 手算与引擎一致 ⇒ 在小局面上口径正确
  · 提高 cap 后结果不变 ⇒ 该点未受 cap 截断影响
  · 提高 cap 后结果变化 ⇒ 该点**确实**受 cap 影响，P1 读数需重新审视

用法: python bench/verify_after_click_semantics.py
"""
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, Budget, WKey      # noqa: E402


def build_manual_case():
    """构造一个 5x5、3 雷的手算可验证局面。

    设计：只开 1 格（中心 (3,3)），其数字设为 1。
    ⇒ 中心周围 8 格中恰有 1 雷。
    剩余 2 雷在其余 16 格中任意分布。
    世界数 = C(8,1) * C(16,2) = 8 * 120 = 960（可枚举）。
    """
    w = h = 5
    a = 3
    num10 = 0
    # 中心 (3,3) 已开，数字 1
    cv = [[0] * (w + 2) for _ in range(h + 2)]
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            cv[y][x] = 9
    cv[3][3] = 1

    # 前沿：中心周围的 8 格
    ring = [(2, 2), (3, 2), (4, 2), (2, 3), (4, 3), (2, 4), (3, 4), (4, 4)]
    # 内部：其余 16 格（5x5=25，减中心1，减环8 = 16）
    ring_set = set(ring)
    inner = [(x, y) for y in range(1, h + 1) for x in range(1, w + 1)
             if (x, y) != (3, 3) and (x, y) not in ring_set]

    # groups：1 组，8 个解（每解 = 环上一格有雷）
    groups = []
    for i in range(8):
        sel = [0] * 8
        sel[i] = 1
        groups.append(sel)
    ck = [8]

    return {
        "w": w, "h": h, "a": a, "num10": num10,
        "clicks": [list(p) for p in ring],
        "clicks9": [list(p) for p in inner],
        "groups": [groups], "ck": ck,
        "cell_value": cv,
        "limitation": 8.0 + math.log2(960),
        "total": 960,
        "F": 1, "U": 16,
        "picked": [2, 2], "confidence": 0.5,
    }


def manual_after_click_ring(case):
    """手算环上每格的 after_click（只算一层，不递归后续）。

    ⚠️ 注意：完整 after_click 需要递归续玩，手算整棵递归树不现实。
    这里手算的是**第一层**：P(安全) 与"点开后立刻赢/立刻确定"的情形。
    用于验证 click_outcomes 与 safety 两个基础件，不验证完整递归。
    """
    w, h, a = case["w"], case["h"], case["a"]
    ring = [tuple(p) for p in case["clicks"]]
    inner = [tuple(p) for p in case["clicks9"]]
    total = len(ring) * math.comb(len(inner), a - 1)   # 8 * C(16,2) = 960

    # 每个环上格是雷的世界数 = C(16, 2)（另 2 雷在内部）
    mine_per_ring = math.comb(len(inner), a - 1)
    p_safe = 1.0 - mine_per_ring / total
    return {p: p_safe for p in ring}, total


def main():
    print("=" * 100)
    print("验证 after_click / V_exact 的求解口径")
    print("=" * 100)
    print()

    case = build_manual_case()
    print("  构造局面：5x5，3 雷，中心 (3,3) 已开且数字=1")
    print("    环上 8 格中恰有 1 雷；内部 16 格中 2 雷")
    print("    世界数 = C(8,1) x C(16,2) = %d" % case["total"])
    print()

    # ---- 1. 手算 vs 引擎：safety ----
    manual_safe, total = manual_after_click_ring(case)
    print("-" * 100)
    print("1) 基础件：环上每格的 safety（P(不是雷)）")
    print("-" * 100)
    try:
        eng = ExactEngine(case, node_cap=5_000_000, time_cap=60.0, world_cap=None)
    except Exception as e:                                 # noqa: BLE001
        print("  ✘ 引擎构造失败：%s: %s" % (type(e).__name__, e))
        return 1
    print("  引擎世界数 = %d（手算 %d）%s"
          % (eng.n_worlds, total,
             "✔" if eng.n_worlds == total else "✘"))

    wk_all = WKey.all(eng.n_worlds)
    op = eng.opened0
    terms = [t for t in eng.saturate(wk_all, op)
             if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
    if not terms:
        print("  ✘ 无终端态")
        return 1
    terms.sort(key=lambda t: -t[2])
    wk2, op2, _wt = terms[0]
    cands = eng.candidates(wk2, op2)

    max_diff = 0.0
    n_checked = 0
    for cd in cands:
        xy = eng._coord(cd)
        if xy in manual_safe:
            got = eng.safety(cd, wk2)
            want = manual_safe[xy]
            d = abs(got - want)
            max_diff = max(max_diff, d)
            n_checked += 1
    print("  环上格 checked=%d，max|引擎-手算| = %.3e %s"
          % (n_checked, max_diff, "✔" if max_diff < 1e-12 else "✘"))

    # ---- 2. node_cap 敏感性 ----
    print()
    print("-" * 100)
    print("2) node_cap 敏感性对照：提高 cap 后 V* 是否变化")
    print("-" * 100)
    results = {}
    for cap in (100_000, 1_000_000, 5_000_000, 20_000_000):
        try:
            eng2 = ExactEngine(case, node_cap=cap, time_cap=120.0, world_cap=None)
        except Exception as e:                             # noqa: BLE001
            results[cap] = ("构造失败", str(e)[:50])
            continue
        wk = WKey.all(eng2.n_worlds)
        try:
            terms2 = [t for t in eng2.saturate(wk, eng2.opened0)
                      if not eng2.won(t[1]) and eng2.candidates(t[0], t[1])]
            terms2.sort(key=lambda t: -t[2])
            wk2b, op2b, _ = terms2[0]
            cs = eng2.candidates(wk2b, op2b)
            q = {cd: eng2.after_click(cd, wk2b, op2b) for cd in cs}
            v_opt = max(q.values())
            results[cap] = ("ok", v_opt, eng2.nodes)
        except Budget as e:
            results[cap] = ("Budget", str(e), eng2.nodes)
        except Exception as e:                             # noqa: BLE001
            results[cap] = ("err", str(e)[:50])

    print("  %-14s %-12s %-18s %s" % ("node_cap", "状态", "V*", "nodes"))
    print("  " + "-" * 72)
    vopts = []
    for cap, r in results.items():
        if r[0] == "ok":
            print("  %-14d %-12s %-18.9f %d" % (cap, "ok", r[1], r[2]))
            vopts.append(r[1])
        else:
            print("  %-14d %-12s %-18s %s" % (cap, r[0], r[1], r[2] if len(r) > 2 else ""))

    print()
    if len(vopts) >= 2:
        spread = max(vopts) - min(vopts)
        print("  不同 cap 下 V* 极差 = %.3e" % spread)
        if spread < 1e-12:
            print("  ✔ 该点上 V* 与 cap 无关 ⇒ 未受截断影响")
        else:
            print("  ⚠️ V* 随 cap 变化 ⇒ 该点**确实**受 cap 影响，")
            print("     P1 的 regret 读数需按 cap 分层重新审视")

    # ---- 3. 口径结论 ----
    print()
    print("=" * 100)
    print("口径结论（必须随 P1 读数一起引用）")
    print("=" * 100)
    print("  `V*` 的准确定义：")
    print("    在给定 node_cap 内、对**完整候选集**做**完整递归**（无深度截断）")
    print("    得到的最优值。")
    print()
    print("  它**不是**无条件理论最优，因为受以下限制：")
    print("    1. node_cap / time_cap —— 超限的点被整点丢弃，不进样本")
    print("    2. 世界等权假设 —— Arbiter 布雷机制是否严格等价，未逐位核对")
    print("    3. candidates() 定义 —— 依赖实现正确性")
    print()
    print("  因此 `regret` 的准确定义：")
    print("    「该评估器 + 该候选集内，实际选格相对最优选格的**条件**质量差」")
    print("    **不是**完整对局的精确胜率损失。")
    print()
    print("  转换为整局胜率提升的关系：**本轮未量化**。")

    out = os.path.join(ROOT, "state", "ab", "after_click_semantics.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"manual_worlds": total, "engine_worlds": eng.n_worlds,
                   "safety_max_diff": max_diff, "n_checked": n_checked,
                   "cap_sensitivity": {str(k): (v[0], v[1] if len(v) > 1 else None)
                                       for k, v in results.items()},
                   }, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
