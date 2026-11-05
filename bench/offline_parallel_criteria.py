# -*- coding: utf-8 -*-
"""B''：把「开格收益」与「安全概率」作为**并列主准则**（不是次级）。

=================== 用户纠正记录（2026-11-04）====================
我此前实现的是「安全优先 → 收益只在并列时决定」（字典序/次级），
用户明确纠正：
    「开格收益这是一个与安全并列的主准则」

这意味着：
  · 收益**不是**安全的从属物，不能只在 safety 并列时才发言
  · 两者**同时**参与决策，谁权重更大不是预设的，而是由数据决定
  · 因此正确的形态是**二维权衡**：在 (safety, gain) 平面上找决策边界
  · 但**不是**回到"大量权重搜索"——用户要先明确二者的关系结构

=================== 本脚本的比较对象（四种决策形态）====================
形态 A：安全唯一优先（现状 = S0 argmax）
形态 B：收益唯一优先（gain argmax）
形态 C：字典序（安全先，收益破并列）—— 我上一版做错的形态，保留作对照
形态 D：**并列主准则 = 二维帕累托前沿**
        对每个点，在 (safety, gain) 平面上取帕累托非支配集，
        再用一个**显式的、可解释的**交换率 λ（1 点收益 ≈ 多少安全度）选择。
        λ 从 0 扫到 ∞，画出完整的权衡曲线，而不是挑一个 λ 当结论。

关键区别：形态 D 中收益**可以**压过安全（λ 大时），也可以被安全压过（λ 小），
**不存在预设的从属关系**。这才是"并列"。

=================== 评价与判据 ====================
评价标准只有一个：q（真实整局续玩胜率），不用代理指标当结果。
判据（预注册）：
  · 若存在 λ 使 mean regret 显著低于 S0（配对符号检验 p<0.05，且做多重比较校正）
    ⇒ 该权衡点值得进入整局 A/B
  · 若整条曲线都不优于 S0 ⇒ 报告"在现有 6 个收益指标下，并列主准则无信号"

用法:
  python bench/offline_parallel_criteria.py
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

import numpy as np                                        # noqa: E402
from offline_lexicographic import load_rows, s0_regrets    # noqa: E402

GAIN_METRICS = ("IG", "E", "U", "B", "R", "L")


def pick_parallel(row, gain_metric, lam):
    """并列主准则：在 (safety, gain) 平面上最大化 safety + lam * gain。

    ⚠️ 关键设计：这里**不是**"先过滤 safety 再看 gain"，
       而是把两者放进同一个目标函数里比较。
       lam = 0   ⇒ 退化为纯 safety（形态 A）
       lam = ∞  ⇒ 退化为纯 gain  （形态 B）
       中间值    ⇒ 两者并列权衡（形态 D）

    gain 指标先在该点候选集内 min-max 归一化，使 lam 有统一含义：
       lam = 1 大致表示"把 gain 从最差拉到最好"等价于"1.0 的安全度差"。
    """
    s0 = np.asarray(row["F"]["S0"], dtype=np.float64)
    g = np.asarray(row["F"][gain_metric], dtype=np.float64)
    lo, hi = float(g.min()), float(g.max())
    gn = np.full_like(g, 0.5) if (hi - lo) < 1e-12 else (g - lo) / (hi - lo)
    score = s0 + lam * gn
    return int(np.argmax(score))


def eval_parallel(rows, gain_metric, lam):
    regs = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        idx = pick_parallel(r, gain_metric, lam)
        regs.append(float(q.max() - q[idx]))
    return regs


def sign_test(diffs):
    pos = sum(1 for d in diffs if d > 1e-12)
    neg = sum(1 for d in diffs if d < -1e-12)
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail), pos, neg


def main():
    rows, src = load_rows(os.path.join("state", "ab", "mm_v3.json"))
    usable = [r for r in rows
              if len(r["q"]) >= 2 and (max(r["q"]) - min(r["q"])) > 1e-12]
    print("=" * 100)
    print("B'':「开格收益」与「安全概率」作为**并列主准则**（二维权衡，非从属）")
    print("=" * 100)
    print("  数据：%s，可用点 %d" % (src, len(usable)))

    base = s0_regrets(rows)
    base_mean = sum(base) / len(base)
    print("  S0 基线（纯安全）mean regret = %.6f" % base_mean)

    # lam 扫描（对数尺度，覆盖从"纯安全"到"收益压倒安全"）
    lams = [0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]
    print()
    print("=" * 100)
    print("lam 扫描（score = safety + lam × gain，gain 已归一化）")
    print("=" * 100)
    print("  %-4s %-6s %10s %12s %9s %9s %s"
          % ("gain", "lam", "mean_reg", "Δ vs S0", "符号p", "优/劣", "备注"))
    print("  " + "-" * 88)

    results = []
    for gm in GAIN_METRICS:
        for lam in lams:
            regs = eval_parallel(rows, gm, lam)
            if not regs:
                continue
            mean_r = sum(regs) / len(regs)
            diffs = [a - b for a, b in zip(regs, base)]
            p, pos, neg = sign_test(diffs)
            delta = mean_r - base_mean
            note = ""
            if lam == 0.0:
                note = "=纯安全(基线)"
            elif lam >= 10.0:
                note = "≈纯收益"
            results.append({"gain": gm, "lam": lam, "mean_regret": mean_r,
                            "delta_vs_s0": delta, "p": p,
                            "n_better": pos, "n_worse": neg})
            # 只打印部分行避免过长：每个 gain 打印全部 lam
            print("  %-4s %-6.2f %10.6f %+12.6f %9.4f %4d/%-4d %s"
                  % (gm, lam, mean_r, delta, p, pos, neg, note), flush=True)

    # 汇总：最优权衡点
    print()
    print("=" * 100)
    print("汇总：是否存在优于 S0 的并列权衡点")
    print("=" * 100)
    better = [r for r in results if r["delta_vs_s0"] < -1e-9]
    sig = [r for r in better if r["p"] < 0.05]
    print("  测试组合数：%d（%d 指标 × %d 个 lam）"
          % (len(results), len(GAIN_METRICS), len(lams)))
    print("  优于 S0 的：%d" % len(better))
    print("  且符号检验 p<0.05 的：%d" % len(sig))

    print()
    if better:
        print("  最优的 8 个权衡点（按 Δ vs S0）：")
        for r in sorted(better, key=lambda x: x["delta_vs_s0"])[:8]:
            print("    gain=%-3s lam=%-5.2f  mean=%.6f  Δ=%+.6f  p=%.4f"
                  % (r["gain"], r["lam"], r["mean_regret"],
                     r["delta_vs_s0"], r["p"]))
        n_test = len(results)
        bonf = 0.05 / n_test
        strict = [r for r in better if r["p"] < bonf]
        print()
        print("  多重比较：共 %d 次检验，α=0.05 期望假阳性 ≈ %.1f 个；"
              "Bonferroni 门槛 α=%.5f" % (n_test, n_test * 0.05, bonf))
        print("  Bonferroni 校正后仍显著的：%d 个" % len(strict))
        if strict:
            for r in strict:
                print("    gain=%-3s lam=%-5.2f Δ=%+.6f p=%.6f"
                      % (r["gain"], r["lam"], r["delta_vs_s0"], r["p"]))
    else:
        print("  ⇒ **没有任何并列权衡点优于 S0。**")
        print("     即在现有 6 个收益指标上，把收益提到与安全并列也救不回来。")

    # 关键：lam 的最优值在哪里
    print()
    print("-" * 100)
    print("每个 gain 指标的 lam 最优点（用于判断收益该占多大比重）")
    print("-" * 100)
    for gm in GAIN_METRICS:
        rs = [r for r in results if r["gain"] == gm]
        if not rs:
            continue
        best = min(rs, key=lambda r: r["mean_regret"])
        print("  %-3s: 最优 lam=%-5.2f  mean=%.6f  Δ vs S0=%+.6f"
              % (gm, best["lam"], best["mean_regret"], best["delta_vs_s0"]))

    out = {
        "n_points": len(base), "s0_mean": base_mean,
        "caliber": "v3 初始状态 + C++ 全枚举；评测=q(真实胜率)",
        "form": "score = safety + lam * normalized_gain (parallel, NOT lexicographic)",
        "results": results,
        "n_better_than_s0": len(better),
        "n_significant_uncorrected": len(sig),
        "best": (min(results, key=lambda r: r["mean_regret"])
                 if results else None),
        "caveat": "in-sample 决策点；多重比较需校正；整局 A/B 未做",
    }
    op = os.path.join(ROOT, "state", "ab", "offline_parallel.json")
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
