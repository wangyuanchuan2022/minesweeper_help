# -*- coding: utf-8 -*-
"""两件事：
  A) 破平局 A/B 的**配对置信区间**与**最小可检效应（MDE）**
      —— 不能只报 p 值，必须报"排除了多大的效应"
  B) 用户 8 指标之间的**冗余/共线检查**
      —— 高度相关的指标合并进线性 prior 只会放大噪声，不提供新信号

用法:
  python bench/analyze_ab_ci_and_redundancy.py
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


# ---------------------------------------------------------------------------
# A) 配对风险差的置信区间
# ---------------------------------------------------------------------------
def paired_diff_ci(a_only, b_only, n, z=1.96):
    """配对风险差 (p_A - p_B) 的 Wald CI（基于不一致对）。

    a_only = A 赢 B 输的局数, b_only = B 赢 A 输的局数, n = 有效配对局数。
    方差：Var(d) = [(a_only + b_only) - (a_only - b_only)^2 / n] / n^2
    """
    d = (a_only - b_only) / n
    var = ((a_only + b_only) - (a_only - b_only) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return d, se, (d - z * se, d + z * se)


def mde_paired(a_only, b_only, n, power=0.80):
    """80% 功效下的最小可检效应（配对口径，双侧 α=0.05）。

    MDE = (z_{1-α/2} + z_{power}) * SE
    """
    _d, se, _ci = paired_diff_ci(a_only, b_only, n)
    z_a = 1.959964
    # z for 80% power
    z_p = 0.841621
    return (z_a + z_p) * se


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def part_a():
    """A/B 的配对 CI 与 MDE —— **直接复用已验证的重算脚本**。

    ⚠️ 历史教训：本函数曾把 30/18、43/54、73/72 这些不一致对数量**硬编码**
    在源码里（手抄 stdout）。一旦抄错或参数顺序写反，Δ 符号会整体翻转，
    而 p 值不变所以肉眼很难发现。现改为：**不在此处保存任何数字**，直接委托
    bench/pool_paired_ab.py —— 它从原始逐局 json 计算，且第 0 步跑人工符号自证。
    """
    sys.path.insert(0, os.path.join(ROOT, "bench"))
    import subprocess
    raw_dir = os.path.join(ROOT, "state", "ab")
    raws = [os.path.join(raw_dir, "tiebreak_ab.json"),
            os.path.join(raw_dir, "tiebreak_ab_rep2.json")]
    raws = [x for x in raws if os.path.exists(x)]
    if not raws:
        print("A) 缺原始逐局结果，跳过（拒绝从汇总数字反推）")
        return
    print("=" * 96)
    print("A) 破平局准则 A/B：配对 CI 与 MDE（委托 bench/pool_paired_ab.py）")
    print("   数据源：原始逐局结果；符号自证在该脚本第 0 步执行")
    print("=" * 96)
    cmd = [sys.executable, "-X", "utf8",
           os.path.join(ROOT, "bench", "pool_paired_ab.py"), "--raws"] + raws
    subprocess.run(cmd, cwd=ROOT)


# ---------------------------------------------------------------------------

# B) 指标冗余检查
# ---------------------------------------------------------------------------
def rankdata(v):
    """平均秩（处理并列）。"""
    n = len(v)
    order = sorted(range(n), key=lambda i: v[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x, y):
    rx, ry = rankdata(x), rankdata(y)
    n = len(x)
    if n < 3:
        return None
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def part_b():
    path = os.path.join(ROOT, "state", "ab", "heuristic_metrics.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = d["rows"]

    print("\n" + "=" * 96)
    print("B) 用户 8 指标的冗余检查（同一批可算真值的决策点）")
    print("=" * 96)
    print(f"样本 {len(rows)} 个决策点（与 A/B 的 640 局独立，是另一批数据）")

    METRICS = ("S0_safety", "IG_expand", "U_filter_u", "E_filter_e", "L_center",
               "S0_then_IG", "S0_then_U", "S0_then_E")

    # B1: 选格一致率（两指标在同一决策点选中同一格的比例）
    #     一致率高 = 该指标没有提供独立信息
    print("\nB1) 选格一致率（同一决策点上两指标是否选中同一格）")
    print("   一致率高 = 该指标对决策没有独立贡献")
    print()
    hdr = "%-14s" % "指标"
    for m in METRICS:
        hdr += " %9s" % m[:9]
    print(hdr)
    print("-" * 96)
    for m1 in METRICS:
        line = "%-14s" % m1[:14]
        for m2 in METRICS:
            if m1 == m2:
                line += " %9s" % "—"
                continue
            same = sum(1 for r in rows
                       if abs(r[m1]["regret"] - r[m2]["regret"]) <= 1e-12)
            line += " %8.0f%%" % (same / len(rows) * 100)
        print(line)
    print("\n  注：regret 相同不等于选同一格，但在平局集内的选格差异通常直接反映为 regret 差异；")
    print("      该表是**保守上界**（有些不同格恰好 regret 相等）。")

    # B2: 与基线的配对改善（带 CI）
    print("\nB2) 各指标 vs 纯 safety：配对 regret 差与 CI")
    print(f"{'指标':<14} {'更好':>6} {'更差':>6} {'n':>4} {'平均Δregret':>13} "
          f"{'判定'}")
    print("-" * 96)
    for m in METRICS:
        if m == "S0_safety":
            continue
        diffs = [r[m]["regret"] - r["S0_safety"]["regret"] for r in rows]
        better = sum(1 for x in diffs if x < -1e-12)
        worse = sum(1 for x in diffs if x > 1e-12)
        p = mcnemar_exact(better, worse)
        md = sum(diffs) / len(diffs)
        verdict = ("独立增益（需 A/B 验证）" if p < 0.05 and md < 0
                   else "无独立增益" if p >= 0.05 else "有害")
        print(f"{m:<14} {better:>6} {worse:>6} {len(diffs):>4} "
              f"{md:>+13.4f} {verdict}")

    print("\n  判据（advisor 要求的筛选顺序）：")
    print("  · p<0.05 且平均Δ<0 ⇒ 该指标有**独立正向增益** ⇒ 才有资格进全新 seeds 的整局 A/B")
    print("  · p≥0.05 ⇒ 无独立增益 ⇒ 不进入下一步（放进 prior 只会增加过拟合面）")
    print("  · p<0.05 且平均Δ>0 ⇒ 有害 ⇒ 直接排除")


def main():
    part_a()
    part_b()


if __name__ == "__main__":
    main()
