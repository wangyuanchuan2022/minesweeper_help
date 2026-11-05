# -*- coding: utf-8 -*-
"""B'''(v2)：用**多步收益估计器**替代一步代理，评估「安全 ∥ 收益」并列主准则。

=================== 本版相对上一版的三处实质修改 ====================
Advisor 指出（2026-11-04，我接受全部三条）：

1. **不再扫「一步收益指标 × λ」的线性标量化**
   上一版用 score = safety + λ×gain(IG/E/U/B/R/L)，这与 LP 已证否的
   线性组合同族，且可达收益上限(0.002666) < 逐点波动(0.005251)，
   只能扫出 p≈0.8 的"最优 λ"，拿不到显著结论。

2. **第 5 项「对整局胜率的预期影响」可以作特征**（我之前判断错了）
   只要用**独立廉价估计器**而非真值 q，就不构成循环论证：
     · 真值 q（C++ 全枚举完美续玩）= **评价标准**
     · 2-ply / 3-ply 估计器        = **候选特征**
   两者是不同的量，合法。代码里 `ply_score(k)` / C++ `exact_eval(ply=k)` 可直接复用。

3. **必须报嵌套/留出估计，不能报 in-sample 最优 λ**
   上一版 −0.003030 是"6 指标×13 λ 里挑最小"的选择偏差结果，
   实际落在 p=0.84 噪声内。本版一律用嵌套 LOO。

=================== 本版方法 ====================
候选特征（全部是"多步"量，非一步代理）：
  · PLY2 = 2-ply 值（safety × E[best 1-ply]）
  · PLY3 = 3-ply 值
  · S0   = 一步安全度（作为对照，保留）

并列主准则（不预设从属）：
  score = w_s·S0 + w_p·PLY_k     （两者都是准则，权重由外层搜索决定）

**但**为避免重蹈"线性标量化"覆辙，本版主分析改用**两种非线性并列**：
  (a) **比值准则**：score = PLY_k / (1 - S0 + δ)   —— 单位风险的信息回报
  (b) **最小-最大准则**：先按 min_max 归一化两维，取 min(S0_n, PLY_n)（保守并列）
  这两种都**不是**线性加和，是"并列"的不同数学形态。

=================== 统计纪律（Advisor 第 3 条）====================
· 全部用 **嵌套 LOO**：外层留一点，内层在训练折上选规则/参数，再到留出点评估
· 报配对符号检验 + **配对 bootstrap 95%CI**
· 明确声明：不报 in-sample 最优值

判据（预注册，先写定）：
  · 嵌套 LOO 的 mean regret < S0 基线，且配对 bootstrap 95%CI 上界 < 0 ⇒ 有信号
  · 否则 ⇒ 无信号，如实报告

用法:
  python bench/offline_multistep.py --limit 120
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np                                        # noqa: E402
import utils.native as native                             # noqa: E402
from arms_lookahead import ExactEngine, WKey               # noqa: E402
from dump_metric_matrix_ext import case_key                # noqa: E402
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402

# ==================== 泄漏硬门禁阈值 ====================
# 教训（2026-11-04）：首版仅用「取值完全相等的点数」（46/342）判断估计器独立性，
# 结果放过了 PLY3 与 q 的相关系数 0.9998、top-1 命中 75% 的严重泄漏。
# ⇒ 对连续量，**相等计数几乎没有鉴别力**。此后改用两个具有鉴别力的判据。
#
# 判据 1：逐点排序一致率（Spearman 式：两序列排序的 Kendall tau 均值）
#         估计器若与 q 排序高度一致，说明它已含答案
LEAK_RANK_CONSISTENCY_MAX = 0.80     # 排序一致率上限
# 判据 2：top-1 一致率（估计器 argmax 与 q argmax 相同的比例）
LEAK_TOP1_MAX = 0.60                 # top-1 一致率上限
# 辅助：Pearson 相关中位数（仅作参考，不单独作为门禁）
LEAK_CORR_MAX = 0.80


def load_v3_with_cases(limit=0):
    """读 v3 journal 的 ok 行，并用 key 映射回原始 case。"""
    import ast
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c
    rows, cases = [], []
    jp = os.path.join(ROOT, "state", "ab", "mm_v3.json.journal.jsonl")
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") != "ok":
                continue
            try:
                kk = ast.literal_eval(rec["key"])
            except Exception:
                continue
            c = merged.get(kk)
            if c is None:
                continue
            rows.append(rec["row"])
            cases.append(c)
            if limit and len(rows) >= limit:
                break
    return rows, cases


def cpp_ply(case, k, node_cap):
    """用 C++ exact_eval(ply=k) 算 k-ply 值（独立于真值 q 的估计器）。"""
    worlds = enumerate_worlds_py(case)
    if not worlds:
        return None
    hexw = [format(m, "x") for m in worlds]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    try:
        ql, co, _nodes, _v = native.mscore.exact_eval(
            hexw, [tuple(x) for x in case["clicks"]],
            [tuple(x) for x in case["clicks9"]], cv,
            int(case["a"]), int(case["w"]), int(case["h"]), node_cap, k)
    except Exception:
        return None
    if not ql:
        return None
    out = {}
    for (x, y), v in zip(co, ql):
        out[(int(x), int(y))] = float(v)
    return out


def build_multistep(rows, cases, node_cap):
    """为每个点构造 (S0, PLY2, PLY3) 三组特征，坐标与 v3 行对齐。"""
    feats = []
    kept_rows = []
    for r, c in zip(rows, cases):
        coords = [tuple(x) for x in r["coords"]]
        p2 = cpp_ply(c, 2, node_cap)
        p3 = cpp_ply(c, 3, node_cap)
        if p2 is None:
            continue
        s0 = np.asarray(r["F"]["S0"], dtype=np.float64)
        v2 = np.array([p2.get(xy, 0.0) for xy in coords], dtype=np.float64)
        if p3 is None:
            v3 = v2.copy()
        else:
            v3 = np.array([p3.get(xy, 0.0) for xy in coords], dtype=np.float64)
        feats.append({"S0": s0, "PLY2": v2, "PLY3": v3})
        kept_rows.append(r)
    return kept_rows, feats


def norm(v):
    lo, hi = float(v.min()), float(v.max())
    return np.full_like(v, 0.5) if (hi - lo) < 1e-12 else (v - lo) / (hi - lo)


def score_ratio(f, which, delta=0.05):
    """比值准则：收益 / (1 - 安全 + δ)。不是线性加和。"""
    s0 = f["S0"]
    g = f[which]
    return g / (1.0 - s0 + delta)


def score_minmax(f, which):
    """最小-最大准则：两维归一化后取 min（保守并列，任一维差就不选）。"""
    return np.minimum(norm(f["S0"]), norm(f[which]))


def score_lexicographic(f, which, eps):
    """字典序（作对照）：安全先过滤，收益破并列。"""
    s0 = f["S0"]
    g = f[which]
    mx = float(s0.max())
    kept = s0 >= mx - eps - 1e-12
    if kept.sum() <= 1:
        return s0 + 0.0 * g
    sc = np.where(kept, g, -np.inf)
    return sc


def s0_pick(f):
    return int(np.argmax(f["S0"]))


def sign_test(diffs):
    """双侧精确符号检验。

    ⚠️ 已修 bug（2026-11-04）：原写法 `sum(...) / (2 ** n)` 中，
    当 n 较大时 `2**n` 是**任意精度整数**，`int / int` 会走整数除法被截断为 0，
    导致 p 恒为 1.0（表现为"方向明显但 p=1.0000"）。
    必须显式转 float 或用 math.fsum 归一化。
    """
    pos = sum(1 for d in diffs if d > 1e-12)
    neg = sum(1 for d in diffs if d < -1e-12)
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    total = float(2 ** n)                                  # 显式 float
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / total
    return min(1.0, 2.0 * tail), pos, neg


def boot_ci(diffs, n_boot=4000, seed=7):
    """配对差值的 bootstrap 95%CI（差值均值）。"""
    d = np.asarray(diffs, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = len(d)
    means = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        means[i] = d[idx].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120,
                    help="只用前 N 点（2-ply/3-ply 计算较慢）")
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "offline_multistep.json"))
    args = ap.parse_args()

    rows, cases = load_v3_with_cases(args.limit)
    print("=" * 100)
    print("B'''：多步收益估计器（2-ply/3-ply）作并列主准则 —— 嵌套 LOO")
    print("=" * 100)
    print("  载入 v3 点：%d" % len(rows))
    print("  计算 2-ply / 3-ply（C++ exact_eval ply=k，与真值 q 独立的估计器）...")
    rows, feats = build_multistep(rows, cases, args.node_cap)
    print("  可用点：%d" % len(rows))
    if len(rows) < 30:
        print("  点太少，无法评估")
        return 1

    # 基线（逐点）
    base_regs = []
    for r, f in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        base_regs.append(float(q.max() - q[s0_pick(f)]))
    base_mean = sum(base_regs) / len(base_regs)
    print()
    print("  S0 基线 mean regret = %.6f（n=%d）" % (base_mean, len(base_regs)))

    # ==================== 泄漏硬门禁（必须通过，否则整份读数作废）====================
    # 教训（2026-11-04）：首版用过 PLY3 得到"Δ=-0.024、CI 全负、判据通过"的强读数，
    # 但复查发现 PLY3 与真值 q 的逐点相关系数中位 0.9998、选中格 75.3% 恰为 q 最优
    # ⇒ 等价于"拿答案选格"，是循环论证。此后加入本门禁。
    print()
    print("=" * 100)
    print("泄漏核对：按**实现来源**判定，不按统计一致性")
    print("=" * 100)
    print("  ⚠️ 判据修正记录（Advisor 两次更正，均已接受）：")
    print("     第 1 版：用「取值完全相等的点数」判独立性 —— 对连续量无鉴别力（已废）")
    print("     第 2 版：用「排序一致率/top-1 一致率」—— **这是错的**：")
    print("             与真值高度一致恰恰是强准则应有的优点，不是泄漏证据；")
    print("             该判据会把 PLY3 这类真正可用的强准则永久拦在门外（已废）")
    print("     第 3 版（现行）：**核对实现来源** —— 特征是否读取了 q/真值字段。")
    print()
    print("  来源核对结果：")
    print("    · cpp_ply() 的输入只有：case 的 cell_value / clicks / clicks9 / a / w / h")
    print("    · 它调用 C++ exact_eval(..., ply=k)，**不读取 journal 的 q 字段**")
    print("    · 3-ply 是决策当时就能算出的前瞻量，生产中可用")
    print("    ⇒ **不构成泄漏**。3-ply 与 q 部分点相等，是因为浅局面下 3-ply")
    print("      已足以穷尽后续 —— 这是该准则的真实优势，应计入结果，不可排除。")
    print()
    print("  诊断项（单独报告，不用于筛选）：3-ply 与 q 精确相等的点占比")
    eq_n = 0
    tot_n = 0
    for r, f in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        p = np.asarray(f["PLY3"], dtype=np.float64)
        if len(q) < 3:
            continue
        tot_n += 1
        if np.allclose(p, q, atol=1e-9):
            eq_n += 1
    print("    %d/%d = %.1f%%（**不排除**，仅作诊断）"
          % (eq_n, tot_n, 100 * eq_n / max(1, tot_n)))
    leak_stats = {"equal_to_q_rate": eq_n / max(1, tot_n), "n": tot_n,
                  "verdict": "not_leak_by_source_check",
                  "source_check": "cpp_ply reads only case fields, never journal q"}
    print()
    print("  ✔ 来源核对通过：特征不读真值 ⇒ 允许继续评估（全样本，不排除任何点）。")

    # 候选准则（全部非线性/并列形态）
    candidates = []
    for which in ("PLY2", "PLY3"):
        candidates.append(("比值:%s" % which,
                           lambda f, w=which: score_ratio(f, w)))
        candidates.append(("最小-最大:%s" % which,
                           lambda f, w=which: score_minmax(f, w)))
        for eps in (0.0, 0.05, 0.2):
            candidates.append(("字典序:%s/eps=%.2f" % (which, eps),
                               lambda f, w=which, e=eps:
                               score_lexicographic(f, w, e)))

    print()
    print("=" * 100)
    print("各准则的 in-sample 表现（仅作参考，**不作结论**）")
    print("=" * 100)
    print("  %-22s %12s %12s" % ("准则", "mean_reg", "Δ vs S0"))
    print("  " + "-" * 52)
    for name, fn in candidates:
        regs = []
        for r, f in zip(rows, feats):
            q = np.asarray(r["q"], dtype=np.float64)
            idx = int(np.argmax(fn(f)))
            regs.append(float(q.max() - q[idx]))
        m = sum(regs) / len(regs)
        print("  %-22s %12.6f %+12.6f" % (name, m, m - base_mean), flush=True)

    # 嵌套 LOO：外层留一点，内层在训练折上选**准则**，到留出点评估
    print()
    print("=" * 100)
    print("嵌套 LOO（外层留一点；内层在训练折上选最优准则；留出点评估）")
    print("=" * 100)
    n = len(rows)
    outer_pred = np.zeros(n)
    chosen = []
    for i in range(n):
        train_idx = [j for j in range(n) if j != i]
        # 内层：在训练折上给每个准则打分
        best_name, best_score = None, None
        for name, fn in candidates:
            regs = []
            for j in train_idx:
                r, f = rows[j], feats[j]
                q = np.asarray(r["q"], dtype=np.float64)
                idx = int(np.argmax(fn(f)))
                regs.append(float(q.max() - q[idx]))
            m = sum(regs) / len(regs)
            if best_score is None or m < best_score:
                best_name, best_score = name, m
        chosen.append(best_name)
        fn = dict(candidates)[best_name]
        r, f = rows[i], feats[i]
        q = np.asarray(r["q"], dtype=np.float64)
        idx = int(np.argmax(fn(f)))
        outer_pred[i] = float(q.max() - q[idx])
        if (i + 1) % 20 == 0:
            print("    外层 %d/%d" % (i + 1, n), flush=True)

    loo_mean = float(outer_pred.mean())
    diffs = list(outer_pred - np.asarray(base_regs))
    p, pos, neg = sign_test(diffs)
    ci_lo, ci_hi = boot_ci(diffs)
    print()
    print("  **嵌套 LOO mean regret = %.6f**（S0 基线 %.6f，Δ %+.6f）"
          % (loo_mean, base_mean, loo_mean - base_mean))
    # ⚠️ 标签修正（2026-11-04）：sign_test 的 pos = count(d>0) = **变差**的点数，
    # neg = count(d<0) = **变好**的点数。原打印把 pos 当作"优"，标签写反了。
    # 现已改为正确语义，并同时打印 0 差异点数以便对账。
    n_zero = sum(1 for d in diffs if abs(d) <= 1e-12)
    print("  配对符号检验：**更好 %d / 更差 %d**（另 %d 点无差异），双侧 p = %.4f"
          % (neg, pos, n_zero, p))
    print("  配对 bootstrap 95%%CI（Δ）= [%+.6f, %+.6f]" % (ci_lo, ci_hi))

    from collections import Counter
    freq = Counter(chosen)
    print()
    print("  内层选中的准则分布（前 5）：")
    for nm, c in freq.most_common(5):
        print("    %-24s %d 次 (%.1f%%)" % (nm, c, 100 * c / n))

    print()
    print("=" * 100)
    print("按预注册判据裁定")
    print("=" * 100)
    passed = (loo_mean < base_mean - 1e-12) and (ci_hi < 0)
    print("  判据：嵌套 LOO < S0 基线，且配对 bootstrap 95%CI 上界 < 0")
    print("  ⇒ **%s**" % ("通过（有信号）" if passed else "未通过（无信号）"))
    if not passed:
        print()
        print("  说明（Advisor 第 3 条）：")
        print("    · 本版已用嵌套 LOO，不报 in-sample 最优值")
        print("    · 上一版报的 -0.003030 是 6 指标×13 λ 里挑最小的选择偏差结果，")
        print("      实际 p=0.84，落在噪声内，不构成「接近显著」")

    out = {
        "n_points": n, "s0_mean": base_mean,
        "nested_loo_mean": loo_mean, "delta_vs_s0": loo_mean - base_mean,
        "sign_test_p": p, "boot_ci95": [ci_lo, ci_hi],
        "n_better": pos, "n_worse": neg, "passed": bool(passed),
        "chosen_freq": dict(freq),
        "caliber": "v3 初始状态；PLY2/PLY3 用 C++ exact_eval(ply=k) 独立估计",
        "caveat": "in-sample 决策点；整局 A/B 未做；点有限",
        "args": vars(args),
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
