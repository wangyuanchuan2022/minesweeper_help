# -*- coding: utf-8 -*-
"""汇总 33 用例的算法臂对比：配对统计 + 各臂错在哪。

数据来源：bench/data/arms_result.json（由 bench/_arms_big.py 生成）。
本脚本只做统计，不重算。

关键口径：
  · regret = 该决策状态精确最优值 − 该臂选中格的真实质量（0 = 选对了）
  · 配对比较：同一用例上两臂的 regret 之差，符号检验
  · 报「该臂比最优差多少胜率（绝对百分点）」而非只报次数——用户要的是胜率

用法: python bench/summarize_arms.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ARMS = ["纯 safety", "期望展开", "2-ply 前瞻", "3-ply 前瞻", "生产 win_rate"]


def sign_test(wins, losses):
    """双尾符号检验（精确二项）。"""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def main():
    with open(os.path.join(ROOT, "bench", "data", "arms_result.json"),
              encoding="utf-8") as f:
        data = json.load(f)
    rows = [r for r in data["rows"] if "V_exact" in r and r.get("first")]

    print("=" * 96)
    print(f"算法臂汇总：{len(rows)} 个有效用例")
    print("=" * 96)

    # 每个臂的 regret 分布
    print(f"{'臂':<14} {'全对':>7} {'平均regret':>11} {'最大regret':>11} "
          f"{'总损失胜率':>11}")
    print("-" * 96)
    stats = {}
    for a in ARMS:
        regs = []
        for r in rows:
            e = r["first"].get(a)
            if e is None or e["cases"] == 0:
                continue
            regs.append(e["regret"])
        ok = sum(1 for x in regs if x <= 1e-12)
        stats[a] = regs
        print(f"{a:<14} {f'{ok}/{len(regs)}':>7} "
              f"{(sum(regs)/len(regs) if regs else 0):>11.4f} "
              f"{max(regs) if regs else 0:>11.4f} "
              f"{sum(regs):>11.4f}")

    print()
    print("=" * 96)
    print("配对比较（同一用例上两臂 regret 的差值；负=前者更好）")
    print("=" * 96)

    def paired(a, b):
        d = []
        for r in rows:
            ea = r["first"].get(a)
            eb = r["first"].get(b)
            if (ea is None or ea["cases"] == 0) or (eb is None or eb["cases"] == 0):
                continue
            d.append(ea["regret"] - eb["regret"])
        w = sum(1 for x in d if x < -1e-12)
        l = sum(1 for x in d if x > 1e-12)
        t = len(d)
        return w, l, t, sign_test(w, l), (sum(d) / len(d) if d else 0.0)

    pairs = [("3-ply 前瞻", "纯 safety"), ("2-ply 前瞻", "纯 safety"),
             ("3-ply 前瞻", "2-ply 前瞻"), ("生产 win_rate", "纯 safety"),
             ("生产 win_rate", "3-ply 前瞻"), ("期望展开", "纯 safety"),
             ("3-ply 前瞻", "生产 win_rate")]
    print(f"{'A vs B':<34} {'A更好':>7} {'B更好':>7} {'配对n':>6} "
          f"{'p值':>8} {'平均Δregret':>12}")
    print("-" * 96)
    for a, b in pairs:
        w, l, t, p, md = paired(a, b)
        sig = "显著" if p < 0.05 else "不显著"
        print(f"{a + ' vs ' + b:<34} {w:>7} {l:>7} {t:>6} {p:>8.3f} "
              f"{md:>12.4f}  {sig}")

    print()
    print("=" * 96)
    print("逐用例：生产 win_rate 与各臂的分歧细节")
    print("=" * 96)
    for r in rows:
        pw = r["first"].get("生产 win_rate") or {}
        if pw.get("cases", 0) == 0:
            print(f"[{r['idx']:2d}] V*={r['V_exact']:.4f} 生产win_rate 无有效决策点"
                  f"（该状态未触发 win_rate 或不在候选集）")
            continue
        if pw["regret"] <= 1e-12:
            continue
        best = min((r["first"][a]["regret"], a) for a in ARMS
                   if r["first"].get(a, {}).get("cases", 0) > 0)
        print(f"[{r['idx']:2d}] V*={r['V_exact']:.4f} | "
              f"生产 win_rate regret={pw['regret']:.4f} "
              f"选{tuple(pw['cell'])} safety={pw['safety']:.4f} "
              f"wr_f报={pw['reported']} | 最佳臂={best[1]}(regret={best[0]:.4f})")

    print()
    print("=" * 96)
    print("读法：regret 是'这一步选错损失的胜率'。总损失胜率 ≈ 把每次决策的")
    print("      regret 累加的直观量；一局有几十个决策，因此单步 0.02 不可忽视。")
    print("=" * 96)


if __name__ == "__main__":
    main()
