# -*- coding: utf-8 -*-
"""从两份 eval_harness 结果 JSON 做配对比较（不改配置、只做统计）。

用于已经跑完的两臂结果；比 paired_eval.py 更轻（不重跑）。

用法:
  python bench/compare_arms.py state/paired/wr8_a8.0.json state/paired/wr12_a12.0.json
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def main():
    if len(sys.argv) < 3:
        print("用法: python bench/compare_arms.py A.json B.json")
        return
    pa, pb = sys.argv[1], sys.argv[2]
    for p in (pa, pb):
        if not os.path.isabs(p):
            p = os.path.join(ROOT, p)
        if not os.path.exists(p):
            print(f"缺文件：{p}")
            return
    with open(pa, encoding="utf-8") as f:
        da = json.load(f)
    with open(pb, encoding="utf-8") as f:
        db = json.load(f)

    ra = {r["seed"]: r for r in da["results"] if "error" not in r}
    rb = {r["seed"]: r for r in db["results"] if "error" not in r}
    seeds = sorted(set(ra) & set(rb))
    n = len(seeds)
    b = c = bw = bl = 0
    diffs = []
    for s in seeds:
        wa, wb = ra[s]["won"], rb[s]["won"]
        if wa and not wb:
            b += 1; diffs.append((s, "A赢B输"))
        elif wb and not wa:
            c += 1; diffs.append((s, "A输B赢"))
        elif wa:
            bw += 1
        else:
            bl += 1

    ta = f"wr_th={da['config'].get('wr_threshold')}"
    tb = f"wr_th={db['config'].get('wr_threshold')}"
    print("=" * 74)
    print(f"A = {os.path.basename(pa)}  ({ta})")
    print(f"B = {os.path.basename(pb)}  ({tb})")
    print(f"配对 seed 数 {n}  （只比两臂共有的 seed）")
    print("=" * 74)
    print(f"  两臂都赢 {bw}   两臂都输 {bl}   A独赢 {b}   B独赢 {c}")
    if n:
        ra_ = (bw + b) / n * 100
        rb_ = (bw + c) / n * 100
        print(f"  A 胜率 {ra_:.2f}%   B 胜率 {rb_:.2f}%   差 {ra_-rb_:+.2f}pp")
    p = mcnemar_exact(b, c)
    if b + c > 0:
        d = (b - c) / n
        se = math.sqrt((b + c) / n ** 2 - ((b - c) / n) ** 2 / n)
        lo, hi = d - 1.96 * se, d + 1.96 * se
        print(f"  配对差 95%CI [{lo*100:+.2f}pp, {hi*100:+.2f}pp]  "
              f"McNemar p = {p:.4f}  {'✅ 显著' if p < 0.05 else '❌ 不显著'}")
    else:
        print(f"  ⚠️ 两臂逐局完全一致（p=1.0）→ 该参数在此局面分布下"
              f"**不改变任何决策**")
    if diffs and len(diffs) <= 25:
        print(f"  不一致明细 {len(diffs)} 局：{diffs}")
    elif diffs:
        print(f"  不一致 {len(diffs)} 局（略，仅显示前 25）：{diffs[:25]}")
    print()
    for tag, d in (("A", da), ("B", db)):
        ok = [r for r in d["results"] if "error" not in r]
        if ok:
            print(f"  [{tag}] 猜测决策 {sum(r['n_guess'] for r in ok)/len(ok):.2f}"
                  f"  步数 {sum(r['steps'] for r in ok)/len(ok):.1f}"
                  f"  点击 {sum(r['n_click'] for r in ok)/len(ok):.0f}"
                  f"  推进 {sum(r['opened'] for r in ok)/len(ok):.0f}")


if __name__ == "__main__":
    main()
