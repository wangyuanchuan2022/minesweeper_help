# -*- coding: utf-8 -*-
"""B'：离线检验「安全优先 → 开格收益次级」的**字典序**选格准则。

=================== 用户定下的准则形态（不要改成权重搜索）====================
用户明确要求（2026-11-04）：
  · 把「开格收益」作为**独立决策准则**，不要立即混入原有权重
  · B1 应**明确开格收益与安全概率的决策顺序**，不要一开始就用大量权重搜索混合
  · 开格收益**不能**简单等同于相邻数字数量

因此本脚本采用**字典序（lexicographic）**，不是加权求和：

    1. 先用安全概率 safety(c) 筛选：只保留 safety >= max_safety - ε 的候选
       （ε = 0 表示严格只选最安全的；ε > 0 表示愿意牺牲最多 ε 的安全度去换信息）
    2. 在保留的候选里，用**开格收益**准则挑一个
    3. 若收益准则也无法区分，退回 safety 最大者（确定性，不随机）

=================== 开格收益的四个代理指标（对应用户列的 1-4）====================
用户列了 5 项，第 5 项（对整局胜率的预期影响）**无法作为候选格特征**——
它正是我们要预测的量，用它当特征就是循环论证。
因此离线检验里：
  · 代理 1「预期开出的安全格数」 → NEW（点开后新开格数的期望）
  · 代理 2「预期新增约束/信息量」 → IG（information gain，已实现）
  · 代理 3「预期减少的未确定区域」 → U/E（最坏安全度 / 必然安全格期望）
  · 代理 4「开格后产生的新安全动作机会」 → 用 NEW 与 IG 的组合近似
  · **第 5 项（整局胜率）不作为特征**，但作为**唯一的评价标准**（即 q）

⚠️ 这一点必须写清：第 5 项是评价目标，不是候选特征。用户说"前四项都是代理指标，
   不能代替整局结果"——本脚本正是用 q（真实整局续玩胜率）来评价前四项，
   而不是把它们当成结果。

=================== 评价方式 ====================
对每个决策点 i：
  · 用准则选一格 c_pick
  · regret = max(q) − q[c_pick]
报告：mean regret、与 S0 的配对比较（逐点符号检验 + 均值差）

=================== 判据（预注册，看结果前写定）====================
  · 若某 (ε, 收益准则) 组合的 mean regret **显著低于 S0**
    （配对符号检验 p < 0.05 且均值差 < 0）⇒ 该组合值得进入整局 A/B
  · 若全部组合都不优于 S0 ⇒ 「开格收益次级」在该数据上无信号，回报用户
  · 不允许事后挑选"看起来最好"的组合而不做多重比较校正

用法:
  python bench/offline_lexicographic.py
"""
import argparse
import itertools
import json
import math
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def load_rows(path):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)["rows"], "final"
    jp = p + ".journal.jsonl"
    rows = []
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == "ok":
                rows.append(r["row"])
    return rows, "journal"


def pick_lexicographic(row, eps, gain_metrics):
    """字典序选格：先 safety 过滤，再按增益指标字典序挑。

    返回 (候选下标, 说明)。
    """
    F = {m: np.asarray(row["F"][m], dtype=np.float64) for m in METRICS}
    s0 = F["S0"]
    n = len(s0)
    max_s = float(s0.max())

    # 第 1 步：安全概率筛选（愿意牺牲最多 eps）
    kept = [i for i in range(n) if s0[i] >= max_s - eps - 1e-12]
    if len(kept) == 1:
        return kept[0], "safety唯一"

    # 第 2 步：在保留集内按 gain 指标字典序比较
    #   字典序 = 依次比较各指标；用"多轮筛选"实现，而非加权
    cur = kept
    for m in gain_metrics:
        v = F[m]
        if len(cur) == 1:
            break
        mx = max(v[i] for i in cur)
        nxt = [i for i in cur if v[i] >= mx - 1e-12]
        if len(nxt) >= 1:
            cur = nxt
    # 第 3 步：仍并列则取 safety 最大、下标最小（确定性）
    best = max(cur, key=lambda i: (s0[i], -i))
    return best, "gain"


def eval_criterion(rows, eps, gain_metrics):
    """对全部点用该准则选格，返回逐点 regret 列表。"""
    regs = []
    n_safety_decided = 0
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        idx, why = pick_lexicographic(r, eps, gain_metrics)
        if why == "safety唯一":
            n_safety_decided += 1
        regs.append(float(q.max() - q[idx]))
    return regs, n_safety_decided


def s0_regrets(rows):
    """S0 基线：直接取 S0 最大者（并列取下标最小）。"""
    regs = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        s0 = np.asarray(r["F"]["S0"], dtype=np.float64)
        idx = int(np.argmax(s0))
        regs.append(float(q.max() - q[idx]))
    return regs


def pawned_sign_test(diffs):
    """配对符号检验（双侧，精确二项）。diffs = 逐点 (criterion - s0)。"""
    pos = sum(1 for d in diffs if d > 1e-12)
    neg = sum(1 for d in diffs if d < -1e-12)
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail), pos, neg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "offline_lexicographic.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    print("=" * 100)
    print("B'：离线检验「安全优先 → 开格收益次级」字典序准则")
    print("=" * 100)
    print("  数据：%s（%d 点，口径 v3 初始状态 + C++ 全枚举）" % (src, len(rows)))
    print("  评价标准：q = 该状态完美续玩的真实胜率（**不是**代理指标）")

    base = s0_regrets(rows)
    base_mean = sum(base) / len(base)
    print()
    print("  S0 基线（只用安全度）：mean regret = %.6f（n=%d）"
          % (base_mean, len(base)))

    # 待检验的增益准则（代理指标的不同组合，均为用户列的前 4 项）
    gain_sets = [
        ("NEW代理=IG", ["IG"]),
        ("NEW代理=E", ["E"]),
        ("IG,E", ["IG", "E"]),
        ("E,IG", ["E", "IG"]),
        ("IG,U", ["IG", "U"]),
        ("E,U", ["E", "U"]),
        ("IG,E,U", ["IG", "E", "U"]),
        ("U,E,IG", ["U", "E", "IG"]),
    ]
    eps_list = [0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.50]

    print()
    print("=" * 100)
    print("扫描：ε（允许牺牲的安全度）× 增益指标组合")
    print("=" * 100)
    print("  %-14s %6s %10s %10s %8s %10s %s"
          % ("增益准则", "ε", "mean_reg", "Δ vs S0", "符号p", "safety定案", "评价"))
    print("  " + "-" * 92)

    results = []
    for eps in eps_list:
        for gname, gmetrics in gain_sets:
            regs, n_sd = eval_criterion(rows, eps, gmetrics)
            if not regs:
                continue
            mean_r = sum(regs) / len(regs)
            diffs = [a - b for a, b in zip(regs, base)]
            p, pos, neg = pawned_sign_test(diffs)
            delta = mean_r - base_mean
            # 多重比较：记录但不立即宣称
            mark = ""
            if delta < 0 and p < 0.05:
                mark = "优于S0*"
            elif delta < 0:
                mark = "略优"
            elif abs(delta) < 1e-12:
                mark = "相同"
            else:
                mark = "更差"
            print("  %-14s %6.2f %10.6f %+10.6f %8.4f %10d %s"
                  % (gname, eps, mean_r, delta, p, n_sd, mark), flush=True)
            results.append({"gain": gname, "gain_metrics": gmetrics,
                            "eps": eps, "mean_regret": mean_r,
                            "delta_vs_s0": delta, "sign_p": p,
                            "n_safety_decided": n_sd,
                            "n_points": len(regs), "mark": mark})

    # 汇总
    print()
    print("=" * 100)
    print("汇总")
    print("=" * 100)
    better = [r for r in results if r["delta_vs_s0"] < 0]
    sig = [r for r in results if r["delta_vs_s0"] < 0 and r["sign_p"] < 0.05]
    print("  组合总数：%d" % len(results))
    print("  比 S0 好的：%d" % len(better))
    print("  且符号检验 p<0.05 的：%d" % len(sig))
    print()
    if sig:
        print("  以下组合显著优于 S0（**未做多重比较校正，需谨慎**）：")
        for r in sorted(sig, key=lambda x: x["delta_vs_s0"])[:6]:
            print("    %-14s ε=%.2f  mean=%.6f  Δ=%+.6f  p=%.4f"
                  % (r["gain"], r["eps"], r["mean_regret"],
                     r["delta_vs_s0"], r["sign_p"]))
        n_test = len(results)
        print()
        print("  ⚠️ 多重比较：共检验 %d 个组合，α=0.05 下期望假阳性 ≈ %.1f 个。"
              % (n_test, n_test * 0.05))
        print("     因此**不能**直接采纳单个最佳组合，需 Bonferroni 或独立验证。")
        bonf = 0.05 / n_test
        strict = [r for r in results
                  if r["delta_vs_s0"] < 0 and r["sign_p"] < bonf]
        print("     Bonferroni 校正后（α=%.5f）仍显著的：%d 个" % (bonf, len(strict)))
    else:
        print("  ⇒ 没有任何组合显著优于 S0。")
        print("     结论：在本数据上，「安全优先→开格收益次级」字典序**无正向信号**。")

    # 关键：ε 的作用（是否"牺牲安全换信息"有用）
    print()
    print("-" * 100)
    print("ε 的作用（是否值得牺牲安全度换信息）")
    print("-" * 100)
    by_eps = {}
    for r in results:
        by_eps.setdefault(r["eps"], []).append(r["delta_vs_s0"])
    for eps in sorted(by_eps):
        ds = by_eps[eps]
        print("  ε=%.2f  平均 Δ vs S0 = %+.6f（最好 %+.6f，最差 %+.6f）"
              % (eps, sum(ds) / len(ds), min(ds), max(ds)))

    out = {
        "n_points": len(base), "s0_mean_regret": base_mean,
        "caliber": "v3 初始状态 + C++ 全枚举；评价标准=q(真实胜率)",
        "method": "lexicographic (safety filter then gain metrics), NOT weighted sum",
        "results": results,
        "best": min(results, key=lambda r: r["mean_regret"]) if results else None,
        "caveat": "in-sample 决策点；多重比较未校正前不得采纳；整局 A/B 未做",
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
