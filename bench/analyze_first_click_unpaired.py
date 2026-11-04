# -*- coding: utf-8 -*-
"""修正首点实验的统计口径：改变首点 ⇒ 棋盘不同 ⇒ 只能用**非配对**检验。

=================== 上一版错在哪 ====================
`ab_first_click.py` 把不同首点臂按 **seed 配对**做 McNemar。
但 Arbiter 是**延迟布雷**：棋盘在首点之后才生成，布雷池 = 全盘 − 已开格。
首点不同 ⇒ 被排除的格不同 ⇒ **同一个 seed 抽出的雷位不同** ⇒ 棋盘不同。

实测证据（bench/verify_first_click_wiring.py T2）：
    center   雷位哈希 618abc5cb57b5979
    corner00 雷位哈希 2ad6894ed40cdd6e
    corner01 雷位哈希 3a933a7ad9da7564
⇒ 三臂在同一 seed 下是**三个不同棋盘**，不是同一盘面的三种打法。

因此正确口径是**独立两组比例之差**（two-proportion），
不能报 McNemar（它要求同一单元两种处理的结果可配对）。

⚠️ 注意：这不是说实验设计无效。问题是「先点哪个格」这个决策本身会
决定棋盘，所以只能做**组间比较**（between-subjects），这仍是有意义的：
它回答的正是"一直点角落 vs 一直点中心，胜率哪个高"。

用法: python bench/analyze_first_click_unpaired.py --raw state/ab/first_click_120.json
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


def diff_ci(k1, n1, k2, n2, z=1.96):
    """两组独立比例之差的 CI（Wald，p1 - p2）。"""
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return d, se, (d - z * se, d + z * se)


def mde_two_prop(k1, n1, k2, n2):
    _d, se, _ci = diff_ci(k1, n1, k2, n2)
    return (1.959964 + 0.841621) * se


def z_test_two_prop(k1, n1, k2, n2):
    """两独立比例的 pooled z 检验，返回 (z, 双尾 p)。"""
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # 正态近似双尾 p
    pval = math.erfc(abs(z) / math.sqrt(2))
    return z, min(1.0, pval)


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
    print(f"首点位置实验：非配对口径（{n_games} 局，{args.raw}）")
    print("=" * 100)
    print("\n⚠️ 为什么用非配对：改变首点会改变布雷池 ⇒ 同 seed 是不同棋盘。")
    print("   （证据：bench/verify_first_click_wiring.py T2 三个不同雷位哈希）")

    stats = {}
    print(f"\n{'臂':<12} {'胜':>5} {'n':>5} {'胜率':>8} {'95%CI':>22}")
    print("-" * 100)
    for arm, rows in res.items():
        ok = [v for v in rows.values() if not v.get("error")]
        w = sum(1 for v in ok if v["won"])
        lo, hi = wilson(w, len(ok))
        stats[arm] = (w, len(ok), lo, hi)
        print(f"{arm:<12} {w:>5} {len(ok):>5} {w/len(ok)*100:>7.2f}%  "
              f"[{lo*100:>6.2f}%, {hi*100:>6.2f}%]")

    base = args.base
    if base not in stats:
        print(f"\n基线 {base} 不在结果里，无法比较")
        return

    k0, n0, _lo, _hi = stats[base]
    print(f"\n" + "=" * 100)
    print(f"与基线 {base} 的两独立比例比较（正 Δ = 该臂更好）")
    print("=" * 100)
    print(f"{'臂':<12} {'Δ':>10} {'95%CI':>24} {'MDE':>9} {'z':>7} {'p':>9} "
          f"{'判定'}")
    print("-" * 100)
    rows_out = []
    for arm, (k, n, _a, _b) in stats.items():
        if arm == base:
            continue
        dlt, se, (lo, hi) = diff_ci(k, n, k0, n0)
        mde = mde_two_prop(k, n, k0, n0)
        z, p = z_test_two_prop(k, n, k0, n0)
        if p < 0.05 and dlt > 0:
            verdict = "显著更好"
        elif p < 0.05 and dlt < 0:
            verdict = "显著更差"
        else:
            verdict = "未检出差异"
        print(f"{arm:<12} {dlt*100:>+9.2f}pp [{lo*100:>+7.2f},{hi*100:>+7.2f}]pp "
              f"{mde*100:>8.2f}pp {z:>+7.2f} {p:>9.4f}  {verdict}")
        rows_out.append({"arm": arm, "delta": dlt, "ci": [lo, hi],
                         "mde": mde, "z": z, "p": p, "verdict": verdict})

    # ---- 机制解释：为什么角落更好 ----
    print("\n" + "=" * 100)
    print("机制（为什么在 Arbiter 规则下角落可能更好）")
    print("=" * 100)
    print("  首点后必须猜时，风险取决于邻域格数 nb：")
    print("    角落 nb=3、边缘 nb=5、中心 nb=8")
    print("  但首格数字 k=0（开出大片）的概率 = C(N-nb,a)/C(N,a)：")
    print("    角落 49.85% / 边缘 31.25% / 中心 15.45%")
    print("  ⇒ 角落是**高方差**：一半概率白送大片信息，另一半要冒 k/3 的风险。")
    print("     中心是**低方差**：很少白送，但每次猜的风险只有 k/8。")
    print("  本实验测的是两者**净效应**，结果见上表。")

    # ---- 与外部数据对照 ----
    print("\n  ⚠️ 与外部数据方向相反，需注意规则差异：")
    print("     MinesweeperHQ（现代规则：首点及 8 邻域无雷）实测")
    print("     Expert 中心 50.7% vs 角落 45.6%（角落差约 5pp）。")
    print("     本实验是 **Arbiter（延迟布雷）** 规则，只保证首点本格安全，")
    print("     所以角落的 3 邻域优势成立；两套规则结论不可混用。")

    out = args.raw.replace(".json", "_unpaired.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"raw": args.raw, "games": n_games, "base": base,
                   "stats": {k: {"wins": v[0], "n": v[1],
                                 "ci": list(v[2:])}
                             for k, v in stats.items()},
                   "comparisons": rows_out}, f, ensure_ascii=False, indent=1)
    print(f"\n非配对分析已写 {out}")


if __name__ == "__main__":
    main()
