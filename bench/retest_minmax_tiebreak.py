# -*- coding: utf-8 -*-
"""用确定性 tie-break 重测 min-max 准则（消除 64.9% 并列带来的顺序随机性）。

=================== 为什么必须重测 ====================
`diag_minmax_tie.py` 已证实：
  · min-max 评分函数本身**对候选顺序无依赖**（并列集坐标 100% 相同）
  · 但 **64.9% 的点存在并列**（并列集 2~12 个候选）
  · `np.argmax` 在并列时取"第一个"⇒ 选格随候选顺序变化（25% 的点会变）

⇒ 原读数（Δ=-0.024032）里混入了**由下标顺序决定的随机性**。
   这不是评分函数的问题，但使结论不可复现 —— 换一个候选顺序就换一个结果。

=================== 本脚本做法 ====================
对并列集施加**确定性、与顺序无关**的 tie-break 规则，逐条测：

  TB-1 first_reading_order : 取坐标最小的（(y,x) 字典序）—— 与数组顺序无关
  TB-2 max_safety          : 并列集内取 S0 最高者（再并列取 reading order）
  TB-3 max_ply3            : 并列集内取 PLY3 最高者（再并列取 reading order）
  TB-4 max_n_cand_ratio    : 并列集内取该格点开后新开格数期望最高者
                             （即用户列的第 1 项收益：预期开出的安全格数）

⚠️ TB-4 需要额外数据（新开格期望）。若不可得则跳过并说明。

=================== 判据 ====================
每种 tie-break 都报：mean regret、Δ vs S0、配对符号检验、bootstrap CI。
若某种 tie-break 下仍有稳健收益 ⇒ 该结论不依赖下标顺序，可信。
若收益主要由 tie-break 决定 ⇒ 必须如实报告"结论对 tie-break 敏感"。

用法:
  python bench/retest_minmax_tiebreak.py --limit 150
"""
import argparse
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from offline_multistep import (                            # noqa: E402
    load_v3_with_cases, build_multistep, s0_pick, score_minmax)


def tie_break(f, coords, tie_idx, rule):
    """在并列集内用确定性规则选一个（与数组顺序无关）。"""
    if len(tie_idx) == 1:
        return tie_idx[0]
    if rule == "reading_order":
        return min(tie_idx, key=lambda i: (coords[i][1], coords[i][0]))
    if rule == "max_safety":
        return min(tie_idx, key=lambda i: (-f["S0"][i],
                                           coords[i][1], coords[i][0]))
    if rule == "max_ply3":
        return min(tie_idx, key=lambda i: (-f["PLY3"][i],
                                           coords[i][1], coords[i][0]))
    raise ValueError(rule)


def sign_test(diffs):
    pos = sum(1 for d in diffs if d > 1e-12)   # worse
    neg = sum(1 for d in diffs if d < -1e-12)  # better
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    total = float(2 ** n)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / total
    return min(1.0, 2.0 * tail), pos, neg


def boot_ci(diffs, n_boot=5000, seed=7):
    d = np.asarray(diffs, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = len(d)
    ms = np.empty(n_boot)
    for i in range(n_boot):
        ms[i] = d[rng.integers(0, n, n)].mean()
    return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "minmax_tiebreak.json"))
    args = ap.parse_args()

    rows, cases = load_v3_with_cases(args.limit)
    rows, feats = build_multistep(rows, cases, 50_000_000)
    print("=" * 100)
    print("min-max 准则：用确定性 tie-break 重测（消除顺序随机性）")
    print("=" * 100)
    print("  样本：%d 点" % len(rows))

    base = []
    for r, f in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 3:
            continue
        base.append(float(q.max() - q[s0_pick(f)]))
    base = np.asarray(base)
    print("  S0 基线 mean regret = %.6f（n=%d）" % (base.mean(), len(base)))

    print()
    print("=" * 100)
    print("各 tie-break 规则的结果")
    print("=" * 100)
    print("  %-16s %11s %12s %9s %9s %10s %s"
          % ("tie-break", "mean_reg", "Δ vs S0", "更好", "更差", "p", "95%CI"))
    print("  " + "-" * 90)

    results = []
    for rule in ("reading_order", "max_safety", "max_ply3"):
        regs = []
        n_to = 0
        for r, f in zip(rows, feats):
            q = np.asarray(r["q"], dtype=np.float64)
            if len(q) < 3:
                continue
            coords = [tuple(int(v) for v in x) for x in r["coords"]]
            s = score_minmax(f, "PLY3")
            mx = float(s.max())
            tie_idx = [i for i in range(len(s)) if abs(s[i] - mx) < 1e-12]
            n_to += len(tie_idx)
            i = tie_break(f, coords, tie_idx, rule)
            regs.append(float(q.max() - q[i]))
        regs = np.asarray(regs)
        diffs = regs - base
        p, pos, neg = sign_test(diffs)
        lo, hi = boot_ci(diffs)
        print("  %-16s %11.6f %+12.6f %9d %9d %10.4f [%+.4f,%+.4f]"
              % (rule, regs.mean(), regs.mean() - base.mean(),
                 neg, pos, p, lo, hi), flush=True)
        results.append({"rule": rule, "mean_regret": float(regs.mean()),
                        "delta_vs_s0": float(regs.mean() - base.mean()),
                        "n_better": neg, "n_worse": pos, "p": p,
                        "ci95": [lo, hi],
                        "passed": bool((hi < 0) and (p < 0.05))})

    # 与"随机 tie-break"对照：说明原读数的随机成分
    print()
    print("-" * 100)
    print("对照：随机 tie-break（模拟原 argmax 的顺序依赖）")
    print("-" * 100)
    rng = np.random.default_rng(999)
    rand_means = []
    for _ in range(30):
        regs = []
        for r, f in zip(rows, feats):
            q = np.asarray(r["q"], dtype=np.float64)
            if len(q) < 3:
                continue
            s = score_minmax(f, "PLY3")
            mx = float(s.max())
            tie_idx = [i for i in range(len(s)) if abs(s[i] - mx) < 1e-12]
            i = int(tie_idx[rng.integers(len(tie_idx))])
            regs.append(float(q.max() - q[i]))
        rand_means.append(np.mean(regs))
    rm = np.asarray(rand_means)
    print("  30 次随机 tie-break：mean regret 均值 %.6f，范围 [%.6f, %.6f]"
          % (rm.mean(), rm.min(), rm.max()))

    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    passed = [r for r in results if r["passed"]]
    print("  通过判据（CI上界<0 且 p<0.05）的 tie-break：%d/%d"
          % (len(passed), len(results)))
    for r in results:
        print("    %-16s Δ=%+.6f  p=%.4f  CI[%+.4f,%+.4f]  %s"
              % (r["rule"], r["delta_vs_s0"], r["p"], r["ci95"][0],
                 r["ci95"][1], "✔通过" if r["passed"] else "✘未通过"))
    print()
    if passed and len(passed) == len(results):
        print("  ⇒ 所有确定性 tie-break 下都有稳健收益 ⇒ 结论**不依赖下标顺序**。")
    elif passed:
        print("  ⇒ 收益**对 tie-break 敏感**：仅部分规则通过。")
        print("     报告必须说明选用哪条规则，且该规则需有独立理由。")
    else:
        print("  ⇒ 确定 tie-break 下均未通过 ⇒ 原 Δ=-0.024032 主要由")
        print("     下标顺序的随机性造成，**不可复现**，结论不成立。")

    out = {"n_points": len(base), "s0_mean": float(base.mean()),
           "results": results,
           "random_tiebreak_30runs_mean": float(rm.mean()),
           "random_tiebreak_30runs_range": [float(rm.min()), float(rm.max())],
           "caliber": "v3 初始状态 + C++ 全枚举；确定性 tie-break",
           "caveat": "in-sample 决策点；整局 A/B 未做"}
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
