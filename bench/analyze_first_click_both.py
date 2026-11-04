# -*- coding: utf-8 -*-
"""首点实验：**两种口径并列**，明确各自能支持什么结论。

=================== 口径之争的正确表述 ====================
两个口径算出的**点估计完全相同**，只是方差不同、支持的结论不同：

  口径 A：按 seed 配对（McNemar / 配对风险差）
    Δ = (b − c) / n，其中 b = 甲赢乙输、c = 乙赢甲输
    支持的结论：「**首点策略整体效应**」（含布雷分布变化的全部影响）
    理由：seed 是合法区组因子。两臂共用同一条 RNG 流，
          棋盘虽然不同但**由同一个 seed 决定**，因此是相关的配对单元。
          这在「策略 A vs 策略 B 哪个整体更好」这个问题上完全有效。
    不支持的结论：**不是**「同一棋盘上的纯算法提升」
          （棋盘不同，不能归因为算法决策差异）。

  口径 B：两独立比例
    Δ = p_甲 − p_乙，方差更大（不利用相关性）
    支持的结论：同样只是策略整体效应，但**更保守、损失功效**。

⇒ 正确做法：**报配对口径为主**（利用相关性、功效更高），
   同时说明"这是策略整体效应，含布雷分布变化"。
   同时报独立口径作为保守参考。

⚠️ 两种口径的点估计必须相同（同一份原始数据），可作**一致性自证**：
   若 (b−c)/n ≠ p_甲 − p_乙，说明数据/代码有问题。

用法: python bench/analyze_first_click_both.py --raw state/ab/first_click_120.json
"""
import argparse
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


def paired(b, c, n, z=1.96):
    """配对风险差：Δ=(b−c)/n，方差用不一致对。"""
    d = (b - c) / n
    var = ((b + c) - (b - c) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return d, se, (d - z * se, d + z * se)


def mcnemar_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def unpaired(k1, n1, k2, n2, z=1.96):
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return d, se, (d - z * se, d + z * se)


def mde(se, power_z=0.841621, alpha_z=1.959964):
    return (alpha_z + power_z) * se


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="state/ab/first_click_120.json")
    ap.add_argument("--base", default="center")
    args = ap.parse_args()

    with open(args.raw, encoding="utf-8") as f:
        d = json.load(f)
    res = d["results"]
    n_games = d["games"]

    print("=" * 100)
    print(f"首点实验：两种口径并列（{n_games} 局）")
    print("=" * 100)
    print("\n口径说明：")
    print("  A 配对(seed) ：检验『首点策略整体效应』——含布雷分布变化。")
    print("                 seed 是合法区组因子（两臂共用同一条 RNG 流）。")
    print("                 不能支持『同一棋盘上的纯算法提升』。")
    print("  B 独立比例   ：同样只支持策略整体效应，但更保守（损失功效）。")
    print("  两者点估计必须相同 —— 用作一致性自证。")

    # ---- 各臂胜率 ----
    stats = {}
    print(f"\n{'臂':<12} {'胜':>5} {'n':>5} {'胜率':>8} {'95%CI':>22}")
    print("-" * 100)
    for arm, rows in res.items():
        ok = {k: v for k, v in rows.items() if not v.get("error")}
        w = sum(1 for v in ok.values() if v["won"])
        lo, hi = wilson(w, len(ok))
        stats[arm] = {"ok": ok, "w": w, "n": len(ok)}
        print(f"{arm:<12} {w:>5} {len(ok):>5} {w/len(ok)*100:>7.2f}%  "
              f"[{lo*100:>6.2f}%, {hi*100:>6.2f}%]")

    base = args.base
    bok = stats[base]["ok"]

    print("\n" + "=" * 100)
    print(f"与基线 {base} 的比较（正 Δ = 该臂更好）")
    print("=" * 100)
    print(f"{'臂':<12} {'口径':<8} {'Δ':>10} {'95%CI':>24} {'MDE':>9} "
          f"{'p':>9}")
    print("-" * 100)

    out_rows = []
    for arm, st in stats.items():
        if arm == base:
            continue
        aok = st["ok"]
        seeds = sorted(set(bok) & set(aok), key=int)
        b = c = 0
        for s in seeds:
            wa, wb = aok[s]["won"], bok[s]["won"]
            if wa and not wb:
                b += 1
            elif wb and not wa:
                c += 1
        n = len(seeds)

        # A 配对
        dA, seA, (loA, hiA) = paired(b, c, n)
        pA = mcnemar_p(b, c)
        print(f"{arm:<12} {'A配对':<8} {dA*100:>+9.2f}pp "
              f"[{loA*100:>+7.2f},{hiA*100:>+7.2f}]pp "
              f"{mde(seA)*100:>8.2f}pp {pA:>9.4f}")
        print(f"{'':<12} {'(b/c)':<8} b={b:>3} c={c:>3} n={n}")

        # B 独立
        dB, seB, (loB, hiB) = unpaired(st["w"], st["n"],
                                       stats[base]["w"], stats[base]["n"])
        print(f"{'':<12} {'B独立':<8} {dB*100:>+9.2f}pp "
              f"[{loB*100:>+7.2f},{hiB*100:>+7.2f}]pp "
              f"{mde(seB)*100:>8.2f}pp")

        # 一致性自证
        same = abs(dA - dB) < 1e-12
        print(f"{'':<12} 一致性   点估计差 {abs(dA-dB)*1e-15:>8.3f}e-15  "
              f"{'✔ 相同' if same else '✘ 不同（有问题）'}")
        print()

        out_rows.append({
            "arm": arm, "n": n, "b_only": b, "c_only": c,
            "paired": {"delta": dA, "ci": [loA, hiA],
                       "mde": mde(seA), "p": pA},
            "unpaired": {"delta": dB, "ci": [loB, hiB], "mde": mde(seB)},
            "point_estimate_consistent": same,
        })

    # ---- 判定 ----
    print("=" * 100)
    print("判定")
    print("=" * 100)
    print(f"{'臂':<12} {'配对Δ':>10} {'配对p':>8} {'独立Δ':>10} {'独立CI含0?':>11} "
          f"{'综合'}")
    print("-" * 100)
    for r in out_rows:
        p = r["paired"]
        u = r["unpaired"]
        ci_excludes_0 = not (u["ci"][0] <= 0 <= u["ci"][1])
        if p["p"] < 0.05 and p["delta"] > 0 and ci_excludes_0:
            verdict = "稳健正向"
        elif p["delta"] > 0 and ci_excludes_0:
            verdict = "正向（配对未达显著）"
        elif p["p"] < 0.05 and p["delta"] > 0:
            verdict = "正向（独立CI含0，弱）"
        else:
            verdict = "未检出"
        print(f"{r['arm']:<12} {p['delta']*100:>+9.2f}pp {p['p']:>8.4f} "
              f"{u['delta']*100:>+9.2f}pp {str(ci_excludes_0):>11} {verdict}")

    n_cmp = len(out_rows)
    print(f"\n  ⚠️ 多重比较：共 {n_cmp} 个比较，Bonferroni 阈值 = "
          f"{0.05/max(1,n_cmp):.4f}")
    for r in out_rows:
        p = r["paired"]["p"]
        print(f"     {r['arm']:<12} p={p:.4f}  "
              f"{'仍显著' if p < 0.05/max(1,n_cmp) else '校正后不显著'}")

    out = args.raw.replace(".json", "_both.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"raw": args.raw, "games": n_games, "base": base,
                   "note": "paired = strategy-level effect incl. mine "
                           "distribution change; not a same-board "
                           "algorithm gain",
                   "stats": {k: {"wins": v["w"], "n": v["n"]}
                             for k, v in stats.items()},
                   "comparisons": out_rows}, f, ensure_ascii=False, indent=1)
    print(f"\n结果已写 {out}")


if __name__ == "__main__":
    main()
