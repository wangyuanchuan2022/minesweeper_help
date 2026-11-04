# -*- coding: utf-8 -*-
"""检验：分层权重能否突破「全局单一权重不可分离」的 LP 结论？

=================== 为什么这是关键下一步 ====================
`verify_lp_separability.py` 已用 LP 严格证明：
**全局单一权重不可线性分离**（7 维与 12 维均不可行）。

但那个证明有一个明确的**逻辑边界**：
    它只否定"**一套**权重适用于所有决策点"。

**分层权重**（按 limitation 分桶，每桶一套独立权重）是另一个假设空间：
    每桶只需求解自己那部分约束 ⇒ 约束子集更小 ⇒ 可行性**可能不同**。

因此"全局不可分离"**不能推出**"分层也不可分离"。这是必须实测的问题，
也是 HANDOFF 第四节列的未尝试项之一。

=================== 方法 ====================
设桶键为 floor(limitation / 步长)。对每个桶 b：
  · 收集属于 b 的所有约束（每点 × 非最优候选）
  · 对桶内单独跑 LP1 判定（w_b ≥ 0，Σw_b = 1）
  · 记录：桶内可分离 / 不可分离 / 约束数 / 点数

判据（预注册，看结果前写定）：
  · 若**所有**非空桶都可分离 ⇒ 分层权重能达 regret ≡ 0（强结论，值得投入）
  · 若部分桶可分离 ⇒ 分层有局部收益，需报告各桶情况
  · 若几乎所有桶都不可分离 ⇒ 分层也无效，权重路线彻底封死

⚠️ 纪律：
  · 桶内样本太小时 LP 容易"平凡可行"（约束少），必须报每桶点数与约束数
  · 少于 MIN_PTS 的桶标记为"样本不足，不下判定"

用法:
  python bench/verify_lp_layered.py --bucket 2.0 --min-pts 15
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from scipy.optimize import linprog                        # noqa: E402
from verify_lp_separability import (                       # noqa: E402
    METRICS, load_rows, build_constraints, check_solution, lp1_feasibility)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--bucket", type=float, default=2.0,
                    help="limitation 分桶宽度")
    ap.add_argument("--min-pts", type=int, default=15,
                    help="桶内少于该点数则标'样本不足'")
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "lp_layered.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    print("=" * 100)
    print("检验：分层权重 vs 全局不可分离")
    print("=" * 100)
    print("  数据：%s（%d 点）" % (src, len(rows)))
    print("  分桶宽度：limitation / %.1f" % args.bucket)
    print("  样本不足门限：< %d 点" % args.min_pts)

    # 全局对照
    A_all, _ = build_constraints(rows)
    res_all, _ = lp1_feasibility(A_all)
    glob_feas = (res_all is not None and res_all.status == 0)
    print()
    print("  全局对照：约束 %d，LP %s"
          % (A_all.shape[0], "可行" if glob_feas else "不可行"))

    # 分桶
    buckets = {}
    for r in rows:
        b = int(np.floor(float(r["limitation"]) / args.bucket))
        buckets.setdefault(b, []).append(r)

    print()
    print("=" * 100)
    print("逐桶 LP1 判定")
    print("=" * 100)
    print("  %-14s %6s %10s %10s %s"
          % ("limitation 区间", "点数", "约束数", "LP 状态", "判定"))
    print("  " + "-" * 82)

    results = []
    for b in sorted(buckets):
        rs = buckets[b]
        lo = b * args.bucket
        hi = lo + args.bucket
        label = "[%.1f, %.1f)" % (lo, hi)
        if len(rs) < args.min_pts:
            print("  %-14s %6d %10s %10s %s"
                  % (label, len(rs), "-", "-", "样本不足，不下判定"))
            results.append({"bucket": label, "n_points": len(rs),
                            "verdict": "insufficient"})
            continue
        A, _ = build_constraints(rs)
        if A.shape[0] == 0:
            print("  %-14s %6d %10d %10s %s"
                  % (label, len(rs), 0, "-", "无约束（跳过）"))
            results.append({"bucket": label, "n_points": len(rs),
                            "verdict": "no_constraints"})
            continue
        res, msg = lp1_feasibility(A)
        if res is None:
            print("  %-14s %6d %10d %10s %s"
                  % (label, len(rs), A.shape[0], "-", msg))
            continue
        feas = (res.status == 0)
        verdict = "可分离" if feas else "不可分离"
        print("  %-14s %6d %10d %10s %s"
              % (label, len(rs), A.shape[0],
                 "可行" if feas else "不可行", verdict), flush=True)
        rec = {"bucket": label, "n_points": len(rs),
               "n_constraints": int(A.shape[0]),
               "feasible": bool(feas), "verdict": verdict}
        if feas:
            chk = check_solution(rs, res.x)
            rec["check_ok"] = chk["n_ok"]
            rec["check_total"] = chk["n_tot"]
            rec["mean_regret"] = chk["mean_regret"]
            print("      桶内实测：选对 %d/%d，mean_regret=%.6f"
                  % (chk["n_ok"], chk["n_tot"], chk["mean_regret"]))
        results.append(rec)

    # 汇总
    judged = [r for r in results if "feasible" in r]
    feas_n = sum(1 for r in judged if r["feasible"])
    insuff = sum(1 for r in results if r["verdict"] == "insufficient")
    print()
    print("=" * 100)
    print("汇总")
    print("=" * 100)
    print("  可判定桶数：%d，其中可分离 %d、不可分离 %d"
          % (len(judged), feas_n, len(judged) - feas_n))
    print("  样本不足的桶：%d" % insuff)
    print()
    if judged and feas_n == len(judged):
        print("  ⇒ **所有可判定桶都可分离** ⇒ 分层权重可达桶内 regret ≡ 0")
        print("     这是正向发现，值得投入（但需注意：in-sample，且桶内样本较少）")
    elif feas_n > 0:
        print("  ⇒ **部分桶可分离（%d/%d）** ⇒ 分层有局部收益，但不是全胜"
              % (feas_n, len(judged)))
        print("     合理做法：可分离的桶用线性权重，不可分离的桶用 S0。")
    else:
        print("  ⇒ **所有可判定桶都不可分离** ⇒ 分层权重同样无效")
        print("     权重路线在本指标集上彻底封死，应转向非线性和整局验证。")

    print()
    print("  ⚠️ 证据边界：")
    print("     · 全部 in-sample；桶内样本少 ⇒ LP 更容易'平凡可行'，需谨慎")
    print("     · 桶内可分离不等于桶内 out-of-sample 有效")
    print("     · 桶宽 %.1f 是人为选择，换桶宽结论可能不同（未做敏感性分析）"
          % args.bucket)

    out = {
        "n_points": len(rows), "bucket_width": args.bucket,
        "min_pts": args.min_pts,
        "global_feasible": bool(glob_feas),
        "global_constraints": int(A_all.shape[0]),
        "results": results,
        "caliber": "v3 initial state",
        "caveat": "in-sample；桶宽未做敏感性分析",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
