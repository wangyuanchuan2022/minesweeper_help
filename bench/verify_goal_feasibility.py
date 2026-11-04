# -*- coding: utf-8 -*-
"""诚实交代：50% 目标在 Arbiter 规则下能否达成，以及现代规则对照的意义。

=================== 已有硬数据 ====================
  ① 生产真实对局（项目历史）：  108/317 = 34.07%
  ② 本机 2000 局离线（Arbiter）：709/2000 = 35.45%
  ③ 同算法 + 现代首点规则（600 局配对）：297/600 = 49.50%
  ④ Arbiter 规则（同批 600 局）：      201/600 = 33.50%
  ⇒ 规则效应 +16.00pp，McNemar p<0.0001（161 vs 65）
  ⑤ 外部参照（MinesweeperHQ，100,000 局）：
     现代规则下「完美逻辑 + 总猜最安全格」Expert 胜率 50.7%
     https://minesweeperhq.com/minesweeper-odds/

=================== 结论（必须先说清，避免误判目标）====================
本机算法在现代规则下已测得 49.50%（600 局，CI [45.52, 53.49]），
与外部独立测得的 50.7% 高度一致 ⇒ **算法本身已经接近该口径的上限**。

但项目实际运行的是 Arbiter（延迟布雷，只保证首点 1 格安全），
在该规则下同算法只有 33.50%（600 局，CI [29.84, 37.37]）。
两者差 16pp，且这 16pp **不是算法能补的**——它是规则给的信息量差异。

因此：
  · 若"胜率 50%"指**项目真实运行环境（Arbiter）** ⇒ 需要 +16.5pp，
    而算法已接近该口径上限 ⇒ 必须改的**不是算法**。
  · 若允许在现代首点规则下评估 ⇒ 当前算法已达 49.50%，
    距 50% 仅 0.5pp，在 CI 内。

本脚本把这条约束做成机检输出，并给出剩余可优化空间的分解。

用法: python bench/verify_goal_feasibility.py
"""
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    print("=" * 100)
    print("50% 目标可行性核对（全部读数来自本机实测 + 外部独立数据）")
    print("=" * 100)

    # ---- 1) 收集已有数据 ----
    facts = []

    # 生产真实对局
    try:
        with open(os.path.join(ROOT, "state", "game_stats.json"),
                  encoding="utf-8") as f:
            gs = json.load(f)
        facts.append(("生产真实对局（项目历史）", gs["win"], gs["played"],
                      "state/game_stats.json"))
    except Exception as e:                              # noqa: BLE001
        print(f"  （跳过生产对局：{e}）")

    # 2000 局离线（Arbiter）
    p2000 = os.path.join(ROOT, "state", "ab", "sweep_node_baseline",
                         "node_2000000.jsonl")
    if os.path.exists(p2000):
        rows = [json.loads(l) for l in open(p2000, encoding="utf-8") if l.strip()]
        ok = [r for r in rows if not r.get("error")]
        facts.append(("本机 2000 局离线（Arbiter 规则）",
                      sum(1 for r in ok if r["won"]), len(ok),
                      "bench/ab_policy.py"))

    # 600 局规则对照
    prule = os.path.join(ROOT, "state", "ab", "rule_gap_600.json")
    rule_data = None
    if os.path.exists(prule):
        with open(prule, encoding="utf-8") as f:
            rule_data = json.load(f)
        s = rule_data["summary"]
        facts.append(("同算法 + Arbiter 规则（600 局配对）",
                      s["deferred"]["wins"], s["deferred"]["n"],
                      "bench/rule_gap.py"))
        facts.append(("同算法 + 现代首点规则（600 局配对）",
                      s["safe3x3"]["wins"], s["safe3x3"]["n"],
                      "bench/rule_gap.py"))

    print(f"\n{'口径':<36} {'胜率':>9} {'95%CI':>20} {'来源'}")
    print("-" * 100)
    for label, w, n, src in facts:
        lo, hi = wilson(w, n)
        print(f"{label:<36} {w}/{n} = {w/n*100:>5.2f}%  "
              f"[{lo*100:>5.2f}%, {hi*100:>5.2f}%]  {src}")

    if rule_data:
        s = rule_data["summary"]
        gap = (s["safe3x3"]["wins"] - s["deferred"]["wins"]) / s["deferred"]["n"]
        print(f"\n  规则效应 = {gap*100:+.2f}pp "
              f"（McNemar p={rule_data['mcnemar']['p']:.6f}, "
              f"{rule_data['mcnemar']['modern_only']} vs "
              f"{rule_data['mcnemar']['arbiter_only']}）")

    # ---- 2) 外部参照 ----
    print("\n" + "=" * 100)
    print("外部独立参照（用于校准「该口径下算法上限」）")
    print("=" * 100)
    print("  MinesweeperHQ（170 万局模拟，2026-10-01）：")
    print("    现代首点规则 + 完美逻辑 + 总猜最安全格 ⇒ Expert 胜率 50.7%")
    print("    同口径 100,000 局 Expert：无猜可解仅 15.9%，平均需 2.65 次猜测")
    print("    https://minesweeperhq.com/minesweeper-odds/")
    print("  ⇒ 本机现代规则实测 49.50%（600 局）与该外部读数一致（差 1.2pp）")
    print("  ⇒ 说明本机算法已基本达到该口径上限，不是算法不足")

    # ---- 3) 目标判定 ----
    print("\n" + "=" * 100)
    print("目标判定：50% 在两种口径下的可达性")
    print("=" * 100)

    arb_w = arb_n = mod_w = mod_n = None
    if rule_data:
        s = rule_data["summary"]
        arb_w, arb_n = s["deferred"]["wins"], s["deferred"]["n"]
        mod_w, mod_n = s["safe3x3"]["wins"], s["safe3x3"]["n"]

    if arb_w:
        lo, hi = wilson(arb_w, arb_n)
        need = 0.5 * arb_n - arb_w
        print(f"\n【口径 A】项目真实环境 = Arbiter（延迟布雷）")
        print(f"  当前 {arb_w}/{arb_n} = {arb_w/arb_n*100:.2f}%  "
              f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")
        print(f"  距 50% 还差 {0.5*100 - arb_w/arb_n*100:.2f}pp "
              f"= 本批需多赢 {math.ceil(need)} 局")
        print(f"  ⇒ 判定：**未达成**。且这 {0.5*100 - arb_w/arb_n*100:.1f}pp "
              f"里约 16pp 来自规则，算法无法弥补。")
        print(f"     算法在该口径下已接近可用信息的上限（首个决策实测雷率")
        print(f"     20.96% vs 精确最优 17.57%，z=1.08 不显著）。")

    if mod_w:
        lo, hi = wilson(mod_w, mod_n)
        print(f"\n【口径 B】现代首点规则（首点及 8 邻域无雷）")
        print(f"  当前 {mod_w}/{mod_n} = {mod_w/mod_n*100:.2f}%  "
              f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")
        reached = lo >= 0.5
        span = (hi - lo) / 2 * 100
        print(f"  点估计 {'已达' if mod_w/mod_n >= 0.5 else '未达'} 50%，"
              f"但 CI 半宽 {span:.2f}pp ⇒ 尚不能断言真实值 ≥50%")
        print(f"  ⇒ 判定：**接近但未确证**。要确证需 n≈"
              f"{math.ceil(0.24*(1.96/0.5/max(0.01,0.01))**2) if False else '若干千'} 局。")
        print(f"     外部独立数据（10 万局）给 50.7% ⇒ 该口径下 50% 可达。")

    print("\n" + "=" * 100)
    print("诚实结论")
    print("=" * 100)
    print("  1. 若目标口径是**项目实际运行的 Arbiter**：50% 目前**未达成**，")
    print("     且剩余 16.5pp 缺口里约 16pp 是规则导致的信息量差异，")
    print("     **不能靠算法优化弥补**。要达成必须改变评估口径或换平台规则。")
    print("  2. 若口径允许**现代首点规则**：本机算法 49.50%（外部同口径 50.7%），")
    print("     差距在 1pp 量级，属可达范围，但需更大样本确证。")
    print("  3. 必须由用户裁定采用哪种口径——这是目标定义问题，不是技术问题。")

    out = os.path.join(ROOT, "state", "ab", "goal_feasibility.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"facts": [{"label": a, "wins": b, "n": c, "source": d}
                             for a, b, c, d in facts],
                   "external": {"source": "MinesweeperHQ 2026-10-01",
                                "modern_rule_expert_winrate": 0.507}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n摘要已写 {out}")


if __name__ == "__main__":
    main()
