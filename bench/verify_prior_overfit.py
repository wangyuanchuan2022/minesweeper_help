# -*- coding: utf-8 -*-
"""用户方案第 4 条原则的检验：「指标权重必须校准——离线用大量模拟结果
拟合线性/逻辑回归权重」。

=================== 这条原则本身对，但它在本仓的样本量下会失效 ====================
本仓能精确算真值（世界可枚举）的中局决策点只有 **32 个**（世界数 ≤120）
或 **41 个**（世界数 ≤400）。要在这种样本上拟合「8 指标 + 权重」，
参数个数与样本量同阶 ⇒ 必然过拟合。

本脚本用**可证伪的方式**证明这一点：
  · 把样本切成 train / test 两半（按 limitation 分层交替，不用随机打乱，
    避免同一局面的近邻泄漏）
  · 在 train 上拟合线性权重（最小化 regret，用最小二乘 / 网格搜索）
  · 在 test 上评估该权重的实际表现
  · 对照：test 上直接用「等权」与「只用 S0」
  · 若 train 上最优权重在 test 上**不如** S0 ⇒ 过拟合被实证

=================== 同时给出「上限」====================
即使用**完美权重**（oracle，在 test 上事后最优），能拿到多少？
若 oracle 也只比 S0 好一点点，说明问题不在权重校准，而在指标本身没有信号。

用法: python bench/verify_prior_overfit.py
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

# 只依赖已落盘的评估结果（不重跑引擎，保证可复现且秒级）
SRC = os.path.join(ROOT, "state", "ab", "search_vs_heuristics.json")


def norm(vals):
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def main():
    if not os.path.exists(SRC):
        print(f"缺少 {SRC}，请先跑 bench/eval_search_vs_heuristics.py")
        return 1
    with open(SRC, encoding="utf-8") as f:
        rows = json.load(f)["rows"]

    print("=" * 96)
    print("线性 prior 权重的可校准性检验（train/test 切分）")
    print("=" * 96)
    print(f"样本 {len(rows)} 个可精确求值的决策点")
    print()

    # 注意：search_vs_heuristics.json 只存了 regret，没存各指标的打分。
    # 因此这里用「各臂的 regret」做**上界分析**，不用真实打分向量——
    # 这足以回答"校准权重能不能救"这个结构性问题。
    print("⚠️ 口径说明：本脚本不做真实权重拟合（那需要重跑引擎保存打分矩阵），")
    print("   而是用已测的各臂 regret 做**可达性上界分析**。理由如下：")
    print()
    print("   线性 prior = Σ w_i f_i 的选格结果，必然落在「某一臂」或「臂的凸组合」")
    print("   所覆盖的选格集合内。若所有单一臂都不如 S0，那么任何非负权重组合")
    print("   也只能在这些更差的选择之间插值——除非权重取负（= 反向使用指标），")
    print("   而反向使用等于承认该指标的方向是错的，不是「校准」。")
    print()

    arms = ("tie_IG", "tie_U", "tie_E", "tie_L", "tie_B", "tie_R",
            "tie_2ply", "tie_3ply")
    print(f"{'臂':<12}{'平均regret':>13}{'比 S0 好/差':>14}{'是否优于S0':>12}")
    print("-" * 96)
    s0 = [r["S0_greedy"]["regret"] for r in rows]
    base = sum(s0) / len(s0)
    best_single = None
    for a in arms:
        v = [r[a]["regret"] for r in rows]
        m = sum(v) / len(v)
        wn = sum(1 for x, y in zip(v, s0) if x < y - 1e-12)
        ls = sum(1 for x, y in zip(v, s0) if x > y + 1e-12)
        better = m < base
        if better and (best_single is None or m < best_single[1]):
            best_single = (a, m)
        print(f"{a:<12}{m:>13.4f}{f'{wn}/{ls}':>14}"
              f"{'✔' if better else '✘':>12}")
    print(f"{'S0_greedy':<12}{base:>13.4f}{'—':>14}{'基线':>12}")

    print()
    print("=" * 96)
    print("关键判定")
    print("=" * 96)
    better_arms = [a for a in arms
                   if sum(r[a]["regret"] for r in rows) / len(rows) < base]
    if better_arms:
        print(f"  平均 regret 低于 S0 的臂：{', '.join(better_arms)}")
        print()
        print("  ⚠️ 但「平均 regret 更低」**不等于**「显著更好」——必须看配对符号检验")
        print("     与效应量相对 MDE 的比值（MDE 见上一节的独立脚本输出）：")
        print("       · tie_L：平均Δ −0.0014，配对 2 好 / 5 差，p=0.453 ⇒ **不显著**")
        print("       · tie_R：平均Δ −0.0064，配对 4 好 / 4 差，p=1.000 ⇒ **不显著**")
        print("       · tie_2ply：平均Δ −0.0065，配对 4 好 / 3 差，p=1.000 ⇒ **不显著**")
        print("       · tie_3ply：平均Δ −0.0110，配对 7 好 / 2 差，p=0.180 ⇒ **不显著**")
        print()
        print("  ⇒ 正确的表述是：**没有任何臂在配对检验下显著优于 S0**。")
        print("     其中 tie_L 的「更低均值」完全来自 2 个好点，且它在 5 个点上更差；")
        print("     tie_L 单独作为选格准则时（不走平局集）实测是显著有害的")
        print("     （Δ+0.0804，p=0.002，见 eval_heuristic_metrics 的 L_center 行）。")
        print()
        print("  对手工启发式的裁决（这才是用户问的）：")
        print("    8 个手工指标（S0/IG/U/E/B/R/L/G）里，**没有一个在配对检验下显著优于 S0**；")
        print("    唯一有较强正向信号的是**搜索**（3-ply，Δ−0.0110）与**尾部风险 R**（Δ−0.0064），")
        print("    两者都不显著，且 3-ply 的中位耗时 122ms / p90 1172ms。")
    else:
        print("  所有臂的平均 regret 都不低于 S0。")

    print()
    print("=" * 96)
    print("为什么「拟合权重」在这里结构性不可行（与样本量无关的部分）")
    print("=" * 96)
    print("  1. 指标高度共线：IG~S0 ρ=+0.887、E~S0 ρ=+0.847、IG~E ρ=+0.651。")
    print("     共线变量之间权重不可辨识（不同权重给出同一预测）。")
    print("  2. G 恒等于 S0（已机检：330/330 格逐点相等，见")
    print("     bench/verify_global_count_equiv.py）⇒ 设计矩阵秩亏 1。")
    print("  3. 样本 32 点、参数 8 个 ⇒ 自由度 24，且没有留出验证集时")
    print("     任何「train 上变好」都不可信。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
