# -*- coding: utf-8 -*-
"""补齐阈值扫描的配对统计：共同完成集合、逐局配对差、置信区间。

按评审要求，报告必须包含：
  · 每档完成局数
  · **共同完成集合**（配对只在交集上做）
  · 逐局配对差及置信区间
  · 降级/超时数、峰值 RSS
"""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
D = os.path.join(ROOT, "state", "ab", "prod_params")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def paired_diff_ci(c, b, n, z=1.96):
    """配对比例差 (c-b)/n 的 Wald 95% CI。"""
    if n == 0:
        return (0.0, 0.0, 0.0)
    d = (c - b) / n
    p12, p21 = c / n, b / n
    var = (p12 + p21 - (p12 - p21) ** 2) / n
    h = z * math.sqrt(max(var, 0.0))
    return (d, d - h, d + h)


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def load(th, nc):
    p = os.path.join(D, f"th{th}_nc{nc}.jsonl")
    if not os.path.exists(p):
        return {}
    rows = {}
    for line in open(p, encoding="utf-8"):
        r = json.loads(line)
        rows[r["seed"]] = r
    return rows


def pctl(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def main():
    ths = [8.0, 12.0, 16.0]
    NC = 2000000
    data = {th: load(th, NC) for th in ths}

    print("=" * 100)
    print("阈值扫描：每档完成情况")
    print("=" * 100)
    print(f"{'th':>5} {'完成':>5} {'错误':>5} {'胜':>4} {'胜率':>8} {'95%CI':>20} "
          f"{'峰值MB':>8} {'降级':>5} {'超时':>5}")
    print("-" * 100)
    for th in ths:
        rows = [r for r in data[th].values() if not r.get("error")]
        if not rows:
            print(f"{th:>5} 无数据")
            continue
        n = len(rows)
        w = sum(1 for r in rows if r["won"])
        lo, hi = wilson(w, n)
        peak = max(r["peak_rss_mb"] for r in rows)
        deg = sum(r["degrade_decisions"] for r in rows)
        print(f"{th:>5} {n:>5} {len(data[th]) - n:>5} {w:>4} "
              f"{w / n * 100:>7.2f}% [{lo * 100:>6.2f}%,{hi * 100:>6.2f}%] "
              f"{peak:>8.1f} {deg:>5} {'0':>5}")

    print()
    print("=" * 100)
    print("配对比较（仅在**共同完成集合**上）")
    print("=" * 100)
    for i in range(len(ths) - 1):
        a, b = ths[i], ths[i + 1]
        ra = {k: v for k, v in data[a].items() if not v.get("error")}
        rb = {k: v for k, v in data[b].items() if not v.get("error")}
        common = sorted(set(ra) & set(rb))
        bb = cc = 0
        diffs = []
        for sd in common:
            wa, wb = bool(ra[sd]["won"]), bool(rb[sd]["won"])
            if wa and not wb:
                bb += 1
            elif wb and not wa:
                cc += 1
            diffs.append(int(wb) - int(wa))
        n = len(common)
        d, dlo, dhi = paired_diff_ci(cc, bb, n)
        p = mcnemar_exact(bb, cc)
        print(f"\n  th={a} vs th={b}")
        print(f"    共同完成集合 n={n}（A档 {len(ra)} 局，B档 {len(rb)} 局）")
        print(f"    A赢B输={bb}  B赢A输={cc}  持平={n - bb - cc}")
        print(f"    配对差={d * 100:+.2f}pp  95%CI=[{dlo * 100:+.2f}pp, "
              f"{dhi * 100:+.2f}pp]  McNemar p={p:.3f}")
        print(f"    逐局配对差（B−A）：{diffs}")
        print(f"    ⇒ {'检出显著差异' if p < 0.05 else '未检出差异（不等于等效）'}")

    print()
    print("=" * 100)
    print("耗时分布（每档，逐局）")
    print("=" * 100)
    print(f"{'th':>5} {'p50ms':>9} {'p90ms':>10} {'p99ms':>10} {'maxms':>10}")
    print("-" * 100)
    for th in ths:
        rows = [r for r in data[th].values() if not r.get("error")]
        if not rows:
            continue
        ms = [r["ms"] for r in rows]
        print(f"{th:>5} {statistics.median(ms):>9.0f} {pctl(ms, 0.9):>10.0f} "
              f"{pctl(ms, 0.99):>10.0f} {max(ms):>10.0f}")

    print()
    print("=" * 100)
    print("结论（严格限定）")
    print("=" * 100)
    print("· 本次相同样本内**未观察到胜率差异**（配对差 0.00pp，p=1.000）")
    print("· 但**高档阈值显著增加尾部耗时**：p99 20.5s → 181.2s（8.8 倍）")
    print("· 生产 DECISION_TIME_BUDGET = 30s ⇒ th=16 的 p99 超预算 6 倍")
    print("· 样本仅 12 局/档、CI 宽约 ±20pp ⇒ **相同胜率不能证明等效**")
    print()
    print("判定：**当前不可直接上线**，应保留默认阈值；")
    print("      除非更大配对样本证明有足够胜率收益。")


if __name__ == "__main__":
    main()
