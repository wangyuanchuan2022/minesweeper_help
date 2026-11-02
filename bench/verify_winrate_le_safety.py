# -*- coding: utf-8 -*-
"""验证关键数学关系：胜率 ≤ 安全率（因此"按安全率降序 + 安全率上界剪枝"有效）。

命题（用户指出，本脚本用证据复核）：
    win_rate(c) = safety(c) × E[点击 c 后继续获胜的概率 | c 不是雷]
    第二个因子 ≤ 1  ⇒  **win_rate(c) ≤ safety(c)**

推论（原剪枝的正确性）：
    候选已按 safety 降序排列。设前面已算出最大胜率 running_max。
    若第 i 个候选的 safety[i] < running_max，则对任意 j ≥ i：
        win_rate[j] ≤ safety[j] ≤ safety[i] < running_max
    ⇒ j 及之后都不可能成为最优 ⇒ 可以安全剪掉（**这是有效分支定界**，
    不是启发式，也不是 bug）。

本脚本验证：
  ① win_rate ≤ safety 是否对每个候选成立（逐用例检查）
  ② 是否真的存在"被剪掉但胜率更高"的反例（若有 ⇒ 剪枝失效）
  ③ 顺序敏感性的真正来源（既然剪枝是对的，那前面观察到的排列差异
     必有别的原因——本脚本顺带定位）

用法: python bench/verify_winrate_le_safety.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np

import utils.native as nat

CASES = os.path.join(ROOT, "bench", "data", "wr_cases.json")


def run(case):
    out = nat.mscore.win_rate(
        case["clicks"], case["clicks9"],
        [np.asarray(g, np.int32) for g in case["groups"]],
        np.asarray(case["cell_value"], np.int32), case["ck"], case["num10"],
        case["a"], case["w"], case["h"], case["is_play"], lambda v: None)
    res = [float(x) for x in out[0]]
    clicks = [tuple(int(t) for t in c) for c in out[1]]
    c2p = {(int(k[0]), int(k[1])): float(v) for k, v in out[3].items()}
    return res, clicks, c2p


def main():
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 84)
    print("命题验证：win_rate(c) ≤ safety(c)")
    print("=" * 84)
    print(f"{'idx':>3} {'F':>3} {'U':>3} {'候选数':>6} {'违反数':>6} "
          f"{'最大超出':>10} {'最大win_rate':>12} {'对应safety':>12}")
    total_viol = 0
    for i, c in enumerate(cases):
        res, clicks, c2p = run(c)
        viol = 0
        max_excess = 0.0
        max_wr = 0.0
        safety_at_max = 0.0
        for k, xy in enumerate(clicks):
            s = c2p.get(xy)
            if s is None:
                continue
            wr = res[k]
            if wr > s + 1e-9:
                viol += 1
                max_excess = max(max_excess, wr - s)
            if wr > max_wr:
                max_wr = wr
                safety_at_max = s
        total_viol += viol
        print(f"{i:>3} {c['F']:>3} {c['U']:>3} {len(res):>6} {viol:>6} "
              f"{max_excess:>10.2e} {max_wr:>12.6f} {safety_at_max:>12.6f}")

    print("-" * 84)
    if total_viol == 0:
        print("✅ 全部用例满足 win_rate ≤ safety ——")
        print("   ⇒ 按安全率降序 + `safety < running_max` 剪枝是**有效分支定界**")
    else:
        print(f"⚠️ 发现 {total_viol} 处违反 —— 剪枝前提不成立，需重新推导")
    print()

    # 定位顺序敏感性的另一可能来源：clicks2p 的分母
    print("=" * 84)
    print("safety（clicks2p）是否依赖排列")
    print("=" * 84)
    print("⚠️ 诊断口径纠错：win_rate 返回的 clicks 是**排序后**顺序，")
    print("   而 clicks2p 是按**排序前**插入序构建的 dict —— 必须按坐标查，")
    print("   不能按下标对齐（早先按下标读，误以为 safety 随排列变化）。")
    print()
    c7 = cases[7]
    variants = {
        "原始": c7["clicks"],
        "反转": list(reversed(c7["clicks"])),
        "排序": sorted([list(p) for p in c7["clicks"]]),
    }
    safety_by_coord = {}
    for tag, clk in variants.items():
        cc = dict(c7)
        cc["clicks"] = clk
        res, clicks, c2p = run(cc)
        mp = {xy: res[k] for k, xy in enumerate(clicks)}
        safety_by_coord[tag] = c2p
        print(f"\n{tag}: max={max(res):.6f}  排序后首格={clicks[0] if clicks else None}")
        for xy in sorted(mp, key=lambda t: (t[1], t[0]))[:8]:
            s = c2p.get(xy)
            print(f"    {xy}: wr={mp[xy]:.6f}  safety={s if s is None else round(s,6)}")

    # 关键判定：同一坐标的 safety 在三档下是否相同
    print()
    print("同一坐标的 safety 跨排列一致性检查：")
    all_coords = set()
    for tag in safety_by_coord:
        all_coords |= set(safety_by_coord[tag])
    inconsistent = []
    for xy in sorted(all_coords):
        vals = [safety_by_coord[t].get(xy) for t in variants]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2 and max(vals) - min(vals) > 1e-9:
            inconsistent.append((xy, vals))
    if inconsistent:
        print(f"  ⚠️ {len(inconsistent)} 个坐标的 safety 依赖排列：{inconsistent[:5]}")
    else:
        print("  ✅ 所有坐标的 safety 跨排列完全一致 ⇒ safety 是局面固有属性（正确）")


if __name__ == "__main__":
    main()
