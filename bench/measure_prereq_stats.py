# -*- coding: utf-8 -*-
"""三项前置统计（Advisor 要求，全部在动手实现前完成）。

=================== 必须回答的三个问题 ====================
Q1（Advisor 第1条）：真实决策点的 limitation 分层分布是什么？
   每个候选深度档会触发多少比例？
   历史教训：`B线_自适应深度未生效.md` 记录 ad1 的 `cost>1e4` 在真实分布上
   **从不触发**（0/248），导致整局 A/B 构造上必然无差异。
   ⇒ 新触发条件必须先验证命中率。

Q2（Advisor 第2条）：深局面的 limitation / 平局集 / ply 耗时分布是什么？
   不能用 worlds 中位 61 的 150 个简单点评估"自适应深度"——
   在 easy 点上前瞻本就不会加深，测出来必然"无差异"（同一种结构性失败）。

Q3（Advisor 第3条）：此前 51 个改善点里，有多少属于安全度的并列/近似并列？
   若多数改善点**不是**并列（PLY3 与 S0 在非平局点就分开），
   那么"只对并列做前瞻"会丢掉这些收益。

=================== 说明 ====================
本脚本只做统计，不改任何代码、不写生产逻辑。
数据来源：
  · 全部 462 个合格点的 limitation（从 case 重算，不依赖 journal）
  · v3 已完成 342 点的 worlds 与 F 矩阵（用于 Q3 的平局判定）
  · 51 个改善点来自 minmax 对比（现场重算，不引用旧文件）

用法:
  python bench/measure_prereq_stats.py
"""
import argparse
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
from dump_metric_matrix_ext import case_key, count_worlds  # noqa: E402

TIE_EPS = 0.0005        # 与生产 probability.py:1206 一致
LIM_EDGES = [8, 10, 12, 14, 16, 20]


def limitation_of(case):
    total = int(case.get("total", 0))
    len9 = len(case.get("clicks9", []))
    return len9 * 0.8 + math.log2(max(1, total))


def bucket_of(lim):
    prev = 0
    for e in LIM_EDGES:
        if lim <= e:
            return "[%g,%g]" % (prev, e)
        prev = e
    return ">%g" % LIM_EDGES[-1]


def load_v3_rows():
    rows = []
    jp = os.path.join(ROOT, "state", "ab", "mm_v3.json.journal.jsonl")
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == "ok":
                rows.append(r["row"])
    return rows


def q3_tie_analysis(rows):
    """Q3：改善点是否集中在平局/近似平局上。

    ⚠️ v3 矩阵的 F 里**没有 PLY3 字段**（那要在评估时单独用 C++ 算）。
    因此这里现场加载 PLY3（复用 offline_multistep 的实现），
    并把坐标对齐。
    """
    print()
    print("=" * 100)
    print("Q3：此前改善点与「平局」的关系")
    print("=" * 100)
    print("  （v3 不含 PLY3，现场用 C++ exact_eval(ply=3) 加载；取前 150 点控制成本）")

    from offline_multistep import load_v3_with_cases, build_multistep
    mrows, mcases = load_v3_with_cases(150)
    mrows, mfeats = build_multistep(mrows, mcases, 50_000_000)
    print("  载入 %d 点（worlds %d~%d，中位 %d）"
          % (len(mrows), min(r["worlds"] for r in mrows),
             max(r["worlds"] for r in mrows),
             int(statistics.median([r["worlds"] for r in mrows]))))

    n = 0
    better_on_tie = 0
    better_not_tie = 0
    tie_total = 0
    nontie_total = 0
    detail = []
    for r, f in zip(mrows, mfeats):
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 3 or (q.max() - q.min()) < 1e-12:
            continue
        n += 1
        s0 = np.asarray(f["S0"], dtype=np.float64)
        mx = float(s0.max())
        tie_idx = [i for i in range(len(s0)) if s0[i] >= mx - TIE_EPS]
        is_tie = len(tie_idx) > 1
        if is_tie:
            tie_total += 1
        else:
            nontie_total += 1
        i_s0 = int(np.argmax(s0))
        reg_s0 = float(q.max() - q[i_s0])
        p3 = np.asarray(f["PLY3"], dtype=np.float64)
        i_p3 = int(np.argmax(p3))
        reg_p3 = float(q.max() - q[i_p3])
        if reg_p3 < reg_s0 - 1e-12:      # PLY3 更好 = 改善点
            if is_tie:
                better_on_tie += 1
            else:
                better_not_tie += 1
            if len(detail) < 5 and not is_tie:
                detail.append({"worlds": r["worlds"], "tie_size": len(tie_idx),
                               "reg_s0": reg_s0, "reg_p3": reg_p3})
    print()
    print("  可用点 %d：平局 %d（%.1f%%）/ 非平局 %d（%.1f%%）"
          % (n, tie_total, 100 * tie_total / max(1, n),
             nontie_total, 100 * nontie_total / max(1, n)))
    print()
    print("  PLY3 优于 S0 的改善点：")
    print("    落在**平局**上的：%d" % better_on_tie)
    print("    落在**非平局**上的：%d" % better_not_tie)
    tot = better_on_tie + better_not_tie
    if tot:
        print()
        print("  ⇒ 改善点中 %.0f%% 在平局上，%.0f%% 在非平局上"
              % (100 * better_on_tie / tot, 100 * better_not_tie / tot))
        if better_not_tie > better_on_tie:
            print()
            print("  ⚠️ **多数改善点在非平局上** ⇒ 「只对并列做前瞻」会丢掉大部分收益！")
            print("     ⇒ 用户方向正确，但限制为「只破平局」过窄。")
        else:
            print()
            print("  ⇒ 多数改善点在平局上 ⇒ 「只对并列做前瞻」可覆盖主要收益。")
        if detail:
            print()
            print("  非平局改善点样例（证明前瞻在非平局也有用）：")
            for d in detail:
                print("    worlds=%5d tie_size=%d reg_S0=%.4f → reg_PLY3=%.4f"
                      % (d["worlds"], d["tie_size"], d["reg_s0"], d["reg_p3"]))
    return {"n": n, "tie_total": tie_total, "nontie_total": nontie_total,
            "better_on_tie": better_on_tie,
            "better_not_tie": better_not_tie,
            "sample_note": "150 pts, worlds %d~%d"
                           % (min(r["worlds"] for r in mrows),
                              max(r["worlds"] for r in mrows))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "prereq_stats.json"))
    args = ap.parse_args()

    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c
    elig = []
    for c in merged.values():
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= 20000:
            elig.append((nw, c, limitation_of(c)))

    # ================= Q1 =================
    print("=" * 100)
    print("Q1：真实决策点的 limitation 分层分布（Advisor 第 1 条）")
    print("=" * 100)
    print("  合格点 %d，limitation 定义 = len(clicks9)*0.8 + log2(total)" % len(elig))
    lims = [l for _, _, l in elig]
    print("  limitation：min %.2f，中位 %.2f，mean %.2f，max %.2f"
          % (min(lims), statistics.median(lims), sum(lims) / len(lims), max(lims)))

    buckets = {}
    for nw, c, l in elig:
        buckets.setdefault(bucket_of(l), []).append((nw, c, l))
    order = []
    prev = 0
    for e in LIM_EDGES:
        order.append("[%g,%g]" % (prev, e))
        prev = e
    order.append(">%g" % LIM_EDGES[-1])

    print()
    print("  %-12s %6s %8s %10s %10s"
          % ("limitation", "点数", "占比", "能算q率", "worlds中位"))
    print("  " + "-" * 56)
    # 能算 q 的点集合（v3 已完成）
    v3 = load_v3_rows()
    v3_worlds = {r["worlds"] for r in v3}
    for b in order:
        items = buckets.get(b, [])
        if not items:
            continue
        n = len(items)
        # 能算 q 的精确率需要逐点比对，这里用 worlds 近似标注
        med_w = statistics.median([w for w, _, _ in items])
        print("  %-12s %6d %7.1f%% %10s %10.0f"
              % (b, n, 100 * n / len(elig), "见备注", med_w))

    print()
    print("  各深度档若按 limitation 分界，命中率（用于避免 ad1 式「从不触发」）：")
    for thr in (8, 10, 12, 14, 16, 20):
        hit = sum(1 for l in lims if l <= thr)
        print("    limitation<=%2d 覆盖 %3d/%d = %5.1f%%  %s"
              % (thr, hit, len(lims), 100 * hit / len(lims),
                 "（生产 wr_threshold 默认 8）" if thr == 8 else ""))

    # ================= Q2 =================
    print()
    print("=" * 100)
    print("Q2：v3 已完成点的 worlds 分布（Advisor 第 2 条：不能只用简单点）")
    print("=" * 100)
    ws = [r["worlds"] for r in v3]
    print("  已完成 %d 点，worlds：min %d，中位 %d，max %d"
          % (len(ws), min(ws), statistics.median(ws), max(ws)))
    print()
    print("  ⚠️ 已知事实：此前 min-max 评估用的 150 点 worlds 仅 2~180（中位 61），")
    print("     属于最简单的一批。用它们评估「自适应深度」会得到平凡的「无差异」。")
    print("  ⇒ **必须补深局面**（worlds 数千那层）才能评估自适应深度的价值。")
    # 统计深局面可用性
    deep = [(w, c, l) for w, c, l in elig if w > 2000]
    print()
    print("  深局面（worlds>2000）可用点数：%d" % len(deep))
    if deep:
        dw = [w for w, _, _ in deep]
        print("    worlds：min %d，中位 %d，max %d"
              % (min(dw), statistics.median(dw), max(dw)))

    # ================= Q3 =================
    q3 = q3_tie_analysis(v3)

    out = {
        "Q1_limitation": {
            "n": len(elig), "min": min(lims),
            "median": statistics.median(lims),
            "mean": sum(lims) / len(lims), "max": max(lims),
            "bucket_sizes": {b: len(buckets.get(b, [])) for b in order},
            "coverage_by_threshold": {str(t): sum(1 for l in lims if l <= t) / len(lims)
                                      for t in (8, 10, 12, 14, 16, 20)},
        },
        "Q2_v3_worlds": {"n": len(v3), "min": min(ws),
                         "median": statistics.median(ws), "max": max(ws),
                         "n_deep_available": len(deep)},
        "Q3_tie_vs_improvement": q3,
        "tie_eps": TIE_EPS,
        "note": "本脚本只做统计，不改代码",
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
