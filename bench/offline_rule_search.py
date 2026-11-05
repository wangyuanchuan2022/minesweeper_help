# -*- coding: utf-8 -*-
"""B' 第二阶段：在「安全优先」约束内，寻找能逼近理论上限的次级准则。

=================== 已确定的事实（来自 selftest_lexicographic.py）====================
· S0 基线（safety argmax）：mean regret = **0.026326**
· 约束内上限（safety 并列集内取 q 最优）：**0.011100**（比基线好 57.8%）
· safety 集内随机：0.027930（比基线略差）
⇒ **存在真实改进空间**（0.026326 → 0.011100），但简单单一指标抓不住：
   q 最优格在并列集内取到 IG 最大的比例仅 51.1%，接近随机水平。

本脚本的目标：用**可解释的非线性规则**逼近该上限，而不是又回到权重搜索
（权重搜索已被 LP 证否）。

=================== 方法：贪心规则集（decision list）====================
构造一组 if-then 规则，按顺序应用，每条规则用单个指标做阈值判断。
用**留一交叉验证（LOO）**评估，避免在样本内挑规则造成过拟合。

明确纪律：
  · 规则**只在训练折上选**，在留出折上评估（真 LOO，不是 in-sample）
  · 规则候选集预先固定（不看完结果再扩）
  · 与 S0 基线做**配对**比较（同一批点）

=================== 判据（预注册）====================
主判据：LOO mean regret 是否低于 S0 基线 0.026326。
· 低于且配对符号检验 p<0.05 ⇒ 值得进入整局 A/B
· 不低于 ⇒ 次级准则在此数据上无可用信号，回报用户

用法:
  python bench/offline_rule_search.py
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
from offline_lexicographic import load_rows, s0_regrets    # noqa: E402

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")
GAIN_METRICS = ("IG", "U", "E", "B", "R", "L")


def tie_set(row, eps=0.0):
    s0 = np.asarray(row["F"]["S0"], dtype=np.float64)
    mx = float(s0.max())
    return [i for i in range(len(s0)) if s0[i] >= mx - eps - 1e-12]


def apply_rule(row, rule):
    """应用单条规则：rule = (metric, direction, tie_break)。

    direction='max' 表示在并列集内取该指标最大者；
    tie_break 用于仍未决时的确定性选择（'first' / 'minS0'）。
    返回选中的候选下标。
    """
    kept = tie_set(row)
    if len(kept) == 1:
        return kept[0]
    m, direction = rule
    v = np.asarray(row["F"][m], dtype=np.float64)
    if direction == "max":
        target = max(v[i] for i in kept)
    else:
        target = min(v[i] for i in kept)
    cand = [i for i in kept if abs(v[i] - target) < 1e-12]
    # 仍并列：取下标最小（确定性）
    return min(cand)


def apply_rules(row, rules):
    """按顺序尝试规则：第一条能**缩小**候选集的规则胜出。

    这里采用更严格的定义：按顺序应用，若某规则把候选集缩到严格更小，
    就用该规则的结果；否则继续下一条；全部无效则取并列集第一个。
    """
    kept = tie_set(row)
    if len(kept) == 1:
        return kept[0]
    cur = kept
    for rule in rules:
        m, direction = rule
        v = np.asarray(row["F"][m], dtype=np.float64)
        target = (max(v[i] for i in cur) if direction == "max"
                  else min(v[i] for i in cur))
        nxt = [i for i in cur if abs(v[i] - target) < 1e-12]
        if len(nxt) < len(cur):
            cur = nxt
        if len(cur) == 1:
            return cur[0]
    return min(cur)


def eval_rules(rows, rules):
    """在给定点集上评估规则序列，返回逐点 regret。"""
    regs = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        idx = apply_rules(r, rules)
        regs.append(float(q.max() - q[idx]))
    return regs


def choose_best_rule(train_rows, cand_rules):
    """在训练折上选单条最优规则（按 mean regret）。"""
    best, best_r = None, None
    for rule in cand_rules:
        regs = eval_rules(train_rows, [rule])
        if not regs:
            continue
        m = sum(regs) / len(regs)
        if best_r is None or m < best_r - 1e-12:
            best, best_r = rule, m
    return best


def choose_best_pair(train_rows, cand_rules):
    """在训练折上选最优规则对（贪心：先选单条，再选第二条）。"""
    r1 = choose_best_rule(train_rows, cand_rules)
    if r1 is None:
        return ()
    rest = [r for r in cand_rules if r != r1]
    best, best_r = (r1,), None
    regs = eval_rules(train_rows, [r1])
    if regs:
        best_r = sum(regs) / len(regs)
    for r2 in rest:
        regs2 = eval_rules(train_rows, [r1, r2])
        if not regs2:
            continue
        m = sum(regs2) / len(regs2)
        if best_r is None or m < best_r - 1e-12:
            best, best_r = (r1, r2), m
    return best


def pawned_sign_test(diffs):
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
                                                 "offline_rule_search.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    usable = [r for r in rows
              if len(r["q"]) >= 2 and (max(r["q"]) - min(r["q"])) > 1e-12]
    print("=" * 100)
    print("B' 第二阶段：在 safety 约束内寻找次级准则（真 LOO）")
    print("=" * 100)
    print("  数据：%s，可用点 %d" % (src, len(usable)))

    base = s0_regrets(rows)
    base_mean = sum(base) / len(base)
    print("  S0 基线 mean regret = %.6f" % base_mean)

    # 约束内上限
    oracle = []
    for r in usable:
        q = np.asarray(r["q"], dtype=np.float64)
        kept = tie_set(r)
        oracle.append(float(q.max() - max(q[i] for i in kept)))
    oracle_mean = sum(oracle) / len(oracle)
    print("  约束内上限（完美次级准则）= %.6f" % oracle_mean)
    print()

    # 候选规则：每个增益指标 × 方向
    cand_rules = []
    for m in GAIN_METRICS:
        cand_rules.append((m, "max"))
        cand_rules.append((m, "min"))
    print("  候选规则数：%d（%d 指标 × 2 方向）"
          % (len(cand_rules), len(GAIN_METRICS)))

    # ---- 真 LOO：在训练折上选规则，留出折上评估 ----
    print()
    print("=" * 100)
    print("真 LOO（每折在训练集上选规则，只在留出点上评估）")
    print("=" * 100)
    n = len(usable)
    pred_regs = []
    chosen_rules = []
    for i in range(n):
        train = usable[:i] + usable[i + 1:]
        hold = usable[i]
        rules = choose_best_pair(train, cand_rules)
        chosen_rules.append(rules)
        q = np.asarray(hold["q"], dtype=np.float64)
        idx = apply_rules(hold, rules)
        pred_regs.append(float(q.max() - q[idx]))
        if (i + 1) % 100 == 0:
            print("    %d/%d" % (i + 1, n), flush=True)

    loo_mean = sum(pred_regs) / len(pred_regs)
    print("  **LOO mean regret = %.6f**（S0 基线 %.6f，Δ %+.6f）"
          % (loo_mean, base_mean, loo_mean - base_mean))

    diffs = [a - b for a, b in zip(pred_regs, base)]
    p, pos, neg = pawned_sign_test(diffs)
    print("  配对符号检验：优于 S0 %d 点 / 劣于 %d 点，双侧 p = %.4f"
          % (pos, neg, p))

    # 规则使用频率
    from collections import Counter
    freq = Counter()
    for rules in chosen_rules:
        for rr in rules:
            freq[rr] += 1
    print()
    print("  被选规则的频率（前 8）：")
    for rr, c in freq.most_common(8):
        print("    %s: %d 次 (%.1f%%)" % (str(rr), c, 100 * c / n))

    # 判据
    print()
    print("=" * 100)
    print("按预注册判据裁定")
    print("=" * 100)
    print("  判据：LOO mean regret < S0 基线 且 配对 p < 0.05")
    ok = (loo_mean < base_mean - 1e-12) and (p < 0.05)
    if ok:
        print("  ⇒ **通过**：LOO %.6f < 基线 %.6f，p=%.4f"
              % (loo_mean, base_mean, p))
        print("     该次级准则值得进入整局 A/B 验证。")
    else:
        print("  ⇒ **未通过**（LOO %.6f vs 基线 %.6f，p=%.4f）" % (loo_mean, base_mean, p))
        print("     次级准则在此数据上无可靠信号。")
        print()
        print("  参考：即使完美次级准则也只到 %.6f（相对基线改进 %.1f%%），"
              % (oracle_mean, 100 * (base_mean - oracle_mean) / base_mean))
        print("  LOO 选中规则的实际收益为 %.1f%%，即回收了理论空间的 %.1f%%。"
              % (100 * (base_mean - loo_mean) / base_mean,
                 100 * (base_mean - loo_mean) / max(1e-12, base_mean - oracle_mean)))

    out = {
        "n_points": n, "s0_mean": base_mean, "oracle_in_constraint": oracle_mean,
        "loo_mean": loo_mean, "delta_vs_s0": loo_mean - base_mean,
        "sign_test_p": p, "n_better": pos, "n_worse": neg,
        "passed": bool(ok),
        "caliber": "v3 初始状态 + C++ 全枚举；真 LOO 选规则",
        "caveat": "in-sample 决策点；整局 A/B 未做；规则候选集预先固定",
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
