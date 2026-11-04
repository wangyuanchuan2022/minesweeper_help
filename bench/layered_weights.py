# -*- coding: utf-8 -*-
"""A 线：分层权重实验（Bergstra 推论）+ 严格冻结发现/验证集。

=================== 为什么做分层权重 ====================
Bergstra & Bengio (2012) 的核心实证结论（JMLR 13:281-305，第 13-14 页）：
  "a small fraction of hyper-parameters matter for any one data set, but
   **different hyper-parameters matter on different data sets**"
  "Even in this simple 7-d problem, Ψ has a much lower effective dimension of
   between 1 and 4, depending on the data set."

映射到本项目：**不同 limitation 分层（简单局/中局/残局）的最优破平局准则
可能不是同一套**。这解释了此前实测的一个反常现象：
全局单一权重（128 子集穷举）永远赢不了 S0 —— 因为单一权重被迫在所有
分层上折中，而各分层的最优准则方向不同。

⇒ 本脚本做**分层权重**：按 limitation 分桶，每桶独立选准则，
对比全局单一权重。

=================== 为什么必须冻结发现集/验证集 ====================
这是本项目已踩过的坑（记忆条目）：
候选是在发现集里从多臂中挑出的，"挑最高"本身就含运气；
把它与独立验证集合并会把幸运值带进 CI，得到看似显著的假象。

本脚本强制：
  · 按 **seed 奇偶** 切分（不是按 limitation，避免分层泄漏）
  · 发现集上做全部选择（子集穷举 / 分层准则选择）
  · 验证集上只做**一次**最终评估，报告配对差 + CI + MDE
  · 若发现集最优与验证集最优不一致，如实报告

=================== 判据 ====================
主判据：验证集上的平均 regret 与 S0 的配对差 + 符号检验 + MDE。
不作 in-sample 结论。

用法:
  python bench/layered_weights.py
  python bench/layered_weights.py --buckets 3 --min-per-bucket 40
"""
import argparse
import itertools
import json
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

ALL_METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def pick_best(fvec, w):
    """按 w·f 选候选索引；平局取最小索引（确定性）。"""
    best_i, best_v = 0, None
    for i in range(len(fvec[0])):
        v = 0.0
        for k in range(len(w)):
            v += w[k] * fvec[k][i]
        if best_v is None or v > best_v + 1e-15:
            best_i, best_v = i, v
    return best_i


def regret_of(row, subset, w):
    fvec = [row["F"][m] for m in subset]
    i = pick_best(fvec, w)
    q = row["q"]
    return max(q) - q[i]


def per_point(rows, subset, w):
    return [regret_of(r, subset, w) for r in rows]


def sign_p(d):
    wn = sum(1 for x in d if x < -1e-12)
    ls = sum(1 for x in d if x > 1e-12)
    nz = wn + ls
    if nz == 0:
        return 1.0, wn, ls
    k = min(wn, ls)
    return min(1.0, 2.0 * sum(math.comb(nz, i) * (0.5 ** nz)
                              for i in range(k + 1))), wn, ls


def mde80(diffs):
    n = len(diffs)
    if n < 2:
        return None
    mu = sum(diffs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in diffs) / (n - 1))
    return (1.959964 + 0.841621) * sd / math.sqrt(n)


def _report_missing_matrix():
    """如实报告：当前 merge 输出缺指标矩阵，无法做分层权重。"""
    print()
    print("=" * 100)
    print("阻塞：分层权重需要 7 指标打分矩阵（F），而 merge 输出只有 q/coords")
    print("=" * 100)
    print("  解决路径（二选一）：")
    print("    A) 扩展 dump_metric_matrix.py 支持多输入文件 + C++ exact_eval")
    print("       （可复用 merge 的 journal 断点续跑）")
    print("    B) 先用现有 41 点做分层，明确标注「样本不足，仅探索」")
    print()
    print("  当前状态：41 点全部 limitation<=20，只落在 1 个桶 ⇒ 分层==全局，")
    print("            该实验在扩样前**没有意义**。")
    print()
    print("  ⇒ 建议先完成 A，再做分层权重。")
    return 2


def bucket_of(lim, edges):
    for i, e in enumerate(edges):
        if lim <= e:
            return i
    return len(edges)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=None,
                    help="旧接口：单文件 metric_matrix.json（会按位置奇偶切分）")
    ap.add_argument("--discovery", default=None,
                    help="发现集 regret 结果（merge_and_eval_cases.py 输出）")
    ap.add_argument("--validation", default=None,
                    help="验证集 regret 结果（独立采集，只评估一次）")
    ap.add_argument("--edges", default="20,60",
                    help="limitation 分桶边界，逗号分隔")
    ap.add_argument("--min-per-bucket", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261104)
    args = ap.parse_args()

    edges = [float(x) for x in args.edges.split(",")]

    # ============================================================
    # 数据来源分两种模式（P1 预注册要求：发现/验证必须真正独立）
    # ============================================================
    if args.discovery and args.validation:
        # 模式 A（推荐）：两份独立采集的结果文件
        dp = args.discovery if os.path.isabs(args.discovery) else os.path.join(
            ROOT, args.discovery)
        vp = args.validation if os.path.isabs(args.validation) else os.path.join(
            ROOT, args.validation)
        for pth, nm in ((dp, "发现集"), (vp, "验证集")):
            if not os.path.exists(pth):
                print("缺少%s %s" % (nm, pth))
                return 1
        with open(dp, encoding="utf-8") as f:
            disc_rows = json.load(f)["rows"]
        with open(vp, encoding="utf-8") as f:
            valid_rows = json.load(f)["rows"]
        print("  ⚠️ 注意：merge_and_eval_cases.py 输出的是 q/coords，")
        print("     不含 7 指标打分。分层权重需要 F 矩阵 ⇒ 需先补算。")
        return _report_missing_matrix()
    else:
        # 模式 B（旧）：单文件 metric_matrix.json，按位置奇偶切分
        path = args.matrix if args.matrix else os.path.join(
            ROOT, "state", "ab", "metric_matrix.json")
        path = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if not os.path.exists(path):
            print("缺少 %s，请先跑 bench/dump_metric_matrix.py" % path)
            return 1
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload["rows"]
        print("  ⚠️ 模式 B：单文件按位置奇偶切分（弱于真实独立采集）")
        print("     若要严格独立，请用 --discovery/--validation")

    print("=" * 100)
    print("A 线：分层权重实验（Bergstra 推论）+ 冻结发现/验证集")
    print("=" * 100)
    print("  样本 %d 个决策点" % len(rows))
    print("  分桶边界 limitation <= %s" % edges)
    print()

    # ---- 冻结切分：按索引奇偶（不按 limitation，避免分层泄漏）----
    # 注意：这里按位置奇偶切，而不是按 seed（快照里没有 seed 字段）。
    # 位置顺序来自采集顺序，与 limitation 无系统关联。
    disc = [r for i, r in enumerate(rows) if i % 2 == 0]
    valid = [r for i, r in enumerate(rows) if i % 2 == 1]
    print("  发现集 n=%d，验证集 n=%d（按位置奇偶冻结）"
          % (len(disc), len(valid)))

    # ---- 基线：S0 在两集上的 regret ----
    s0_w = [1.0]
    s0_d = per_point(disc, ("S0",), s0_w)
    s0_v = per_point(valid, ("S0",), s0_w)
    print("  S0 基线 regret：发现集 %.4f，验证集 %.4f"
          % (sum(s0_d) / len(s0_d), sum(s0_v) / len(s0_v)))
    print()

    # ============================================================
    # 第一步：全局单一权重（在发现集上从 128 子集里挑最优）
    # ============================================================
    print("=" * 100)
    print("第一步：全局单一权重（发现集上从 128 子集挑最优，等权口径）")
    print("=" * 100)
    subsets = []
    for r in range(1, len(ALL_METRICS) + 1):
        for combo in itertools.combinations(ALL_METRICS, r):
            subsets.append(combo)
    best_global = None
    for subset in subsets:
        d = len(subset)
        m = sum(per_point(disc, subset, [1.0 / d] * d)) / len(disc)
        if best_global is None or m < best_global[1]:
            best_global = (subset, m)
    gsub, gd = best_global
    print("  发现集最优子集：%s（regret %.4f，S0 为 %.4f，Δ%+.4f）"
          % ("+".join(gsub), gd, sum(s0_d) / len(s0_d),
             gd - sum(s0_d) / len(s0_d)))

    # 在验证集上评估这一个子集（只评估一次）
    gv = per_point(valid, gsub, [1.0 / len(gsub)] * len(gsub))
    gv_mean = sum(gv) / len(gv)
    d_g = [a - b for a, b in zip(gv, s0_v)]
    p_g, wn_g, ls_g = sign_p(d_g)
    mde_g = mde80(d_g)
    print()
    print("  ★ 验证集结果（唯一一次评估）：")
    print("    regret %.4f vs S0 %.4f，Δ%+.4f"
          % (gv_mean, sum(s0_v) / len(s0_v), gv_mean - sum(s0_v) / len(s0_v)))
    print("    配对：更好 %d / 更差 %d，p=%.3f" % (wn_g, ls_g, p_g))
    if mde_g:
        print("    MDE(80%%) = %.4f ⇒ %s"
              % (mde_g, "可检出" if abs(gv_mean - sum(s0_v) / len(s0_v)) >= mde_g
                 else "**低于 MDE，不可判定**"))

    # ============================================================
    # 第二步：分层权重（每桶独立挑最优子集，全部在发现集上做）
    # ============================================================
    print()
    print("=" * 100)
    print("第二步：分层权重（每桶在发现集上独立挑子集）")
    print("=" * 100)

    disc_b = {}
    valid_b = {}
    for r in disc:
        disc_b.setdefault(bucket_of(r["limitation"], edges), []).append(r)
    for r in valid:
        valid_b.setdefault(bucket_of(r["limitation"], edges), []).append(r)

    print("  分桶样本数：发现集 %s / 验证集 %s"
          % ({k: len(v) for k, v in sorted(disc_b.items())},
             {k: len(v) for k, v in sorted(valid_b.items())}))
    print()
    print("  %-8s %-6s %-28s %10s %10s" %
          ("桶", "n", "发现集选中子集", "发现regret", "验证regret"))
    print("  " + "-" * 88)

    layered_regrets = {}     # 验证集上逐点 regret（分层）
    skipped = []
    for b in sorted(disc_b.keys()):
        d_rows = disc_b[b]
        v_rows = valid_b.get(b, [])
        if len(d_rows) < args.min_per_bucket or not v_rows:
            skipped.append(b)
            continue
        best = None
        for subset in subsets:
            k = len(subset)
            m = sum(per_point(d_rows, subset, [1.0 / k] * k)) / len(d_rows)
            if best is None or m < best[1]:
                best = (subset, m)
        bsub, bd = best
        bv = per_point(v_rows, bsub, [1.0 / len(bsub)] * len(bsub))
        bv_mean = sum(bv) / len(bv)
        for r, reg in zip(v_rows, bv):
            layered_regrets[id(r)] = reg
        print("  %-8d %-6d %-28s %10.4f %10.4f"
              % (b, len(v_rows), "+".join(bsub), bd, bv_mean))
    if skipped:
        print("  （跳过桶 %s：样本不足 %d 或无验证样本）"
              % (skipped, args.min_per_bucket))

    # 分层在验证集上的总体表现
    if layered_regrets:
        lv = [layered_regrets[id(r)] for r in valid if id(r) in layered_regrets]
        covered = len(lv)
        lv_mean = sum(lv) / covered
        print()
        print("  ★ 分层验证集结果（覆盖 %d/%d 点）：" % (covered, len(valid)))
        print("    regret %.4f vs S0(同子集) %.4f"
              % (lv_mean,
                 sum(reg for r, reg in zip(valid, s0_v)
                     if id(r) in layered_regrets) / covered))
        # 配对比较（只用覆盖到的点）
        s0_cov = [reg for r, reg in zip(valid, s0_v)
                  if id(r) in layered_regrets]
        d_l = [a - b for a, b in zip(lv, s0_cov)]
        p_l, wn_l, ls_l = sign_p(d_l)
        mde_l = mde80(d_l)
        print("    配对：更好 %d / 更差 %d，p=%.3f" % (wn_l, ls_l, p_l))
        if mde_l:
            print("    MDE(80%%) = %.4f" % mde_l)

    # ============================================================
    # 第三步：分层 vs 全局（在验证集上配对比较）
    # ============================================================
    print()
    print("=" * 100)
    print("第三步：分层 vs 全局（验证集配对比较）")
    print("=" * 100)
    if layered_regrets:
        common = [r for r in valid if id(r) in layered_regrets]
        g_vals = []
        l_vals = []
        s_vals = []
        for r, reg_s0 in zip(valid, s0_v):
            if id(r) not in layered_regrets:
                continue
            g_vals.append(regret_of(r, gsub, [1.0 / len(gsub)] * len(gsub)))
            l_vals.append(layered_regrets[id(r)])
            s_vals.append(reg_s0)
        d = [a - b for a, b in zip(l_vals, g_vals)]
        p, wn, ls = sign_p(d)
        mde = mde80(d)
        print("  分层 %.4f vs 全局 %.4f，Δ%+.4f"
              % (sum(l_vals) / len(l_vals), sum(g_vals) / len(g_vals),
                 sum(d) / len(d)))
        print("  配对：分层更好 %d / 更差 %d，p=%.3f" % (wn, ls, p))
        if mde:
            print("  MDE(80%%) = %.4f ⇒ %s"
                  % (mde, "可检出" if abs(sum(d) / len(d)) >= mde
                     else "**低于 MDE，不可判定**"))
        print()
        print("  ⚠️ 结论边界：")
        print("    · 分层是在发现集上逐桶独立选子集 ⇒ 选择自由度 = 桶数 × 128，")
        print("      过拟合风险高于全局（全局只选 1 次）。")
        print("    · 若分层未显著优于全局，不能声称 Bergstra 推论在本项目成立。")
        print("    · 若显著，也需在**另一批独立 seeds** 上复现后才可采纳。")

    out = os.path.join(ROOT, "state", "ab", "layered_weights.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "n_disc": len(disc), "n_valid": len(valid),
            "global_best": list(gsub), "global_disc_regret": gd,
            "global_valid_regret": gv_mean,
            "s0_valid_regret": sum(s0_v) / len(s0_v),
            "layered_valid_regret": (sum(layered_regrets.values())
                                     / len(layered_regrets)
                                     if layered_regrets else None),
            "edges": edges, "args": vars(args),
        }, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
