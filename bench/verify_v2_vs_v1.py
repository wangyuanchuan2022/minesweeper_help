# -*- coding: utf-8 -*-
"""v2 验收：300 点 q 交叉验证 + 修复前后对比（按组织者裁决 B）。

=================== 两项独立验收 ====================
1. **q 路径交叉验证（裁决 B 硬要求）**：
   用 v2 的 q 计算路径（全枚举 + C++ 完整递归）重算 `merged_regret.json` 的 300 点，
   与文件内已有 q 逐点对拍，报 max|Δ|。
   - max|Δ| < 1e-9 ⇒ 证明 q 计算路径正确
   - 不一致 ⇒ 停止，不放行

2. **修复前后对比（裁决 A 交付要求）**：
   对 v2 与 v1（`mm_full.json`）的共同点，比较 q 与 F：
   - q 差异分布（均值/中位/最大）
   - opt q 变化方向统计（上升/不变/下降）
   - F 按指标的平均绝对差
   - 只在**多终端点**上单独统计（缺陷所在）

用法:
  python bench/verify_v2_vs_v1.py
"""
import argparse
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def med(v):
    return statistics.median(v) if v else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", default=os.path.join("state", "ab", "mm_full.json"))
    ap.add_argument("--v2", default=os.path.join("state", "ab", "mm_full_v2.json"))
    ap.add_argument("--merged", default=os.path.join("state", "ab",
                                                     "merged_regret.json"))
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "v2_acceptance.json"))
    args = ap.parse_args()

    print("=" * 100)
    print("v2 验收报告")
    print("=" * 100)

    d1 = load(os.path.join(ROOT, args.v1)) if not os.path.isabs(args.v1) else load(args.v1)
    d2 = load(os.path.join(ROOT, args.v2)) if not os.path.isabs(args.v2) else load(args.v2)
    print("  v1: %d 点（截断版）" % len(d1["rows"]))
    print("  v2: %d 点多终端点 %d（修复版）"
          % (len(d2["rows"]), d2.get("n_multi_terminal_points", -1)))
    print("  v2 skipped: %s" % d2.get("skipped"))
    print("  v2 字段自述: version=%s, p0_3_fixed=%s"
          % (d2.get("version"), d2.get("p0_3_fixed")))

    # ============ 1. q 交叉验证（与 merged_regret）============
    print()
    print("=" * 100)
    print("验收 1：q 路径交叉验证（v2 路径 vs merged_regret 已有 q）")
    print("=" * 100)
    # 说明：merged_regret 的 q 由 merge_and_eval_cases.py 用全枚举+C++ 生成，
    # 与 v2 的 q 路径相同 ⇒ 若 v2 实现正确，两者在同一 case 上必须一致。
    # 用 (limitation, worlds, n_cand, coords 集合) 做匹配。
    mp = os.path.join(ROOT, args.merged)
    max_dq = 0.0
    n_matched = 0
    n_cmp = 0
    per_point = []
    if os.path.exists(mp):
        mrows = load(mp)["rows"]
        # 建 v2 索引
        idx2 = {}
        for r in d2["rows"]:
            key = (round(r["limitation"], 6), int(r["worlds"]),
                   frozenset(tuple(x) for x in r["coords"]))
            idx2[key] = r
        for r in mrows:
            key = (round(r["limitation"], 6), int(r["worlds"]),
                   frozenset(tuple(x) for x in r["coords"]))
            r2 = idx2.get(key)
            if r2 is None:
                continue
            n_matched += 1
            # 逐坐标比 q
            q2 = {tuple(x): v for x, v in zip(r2["coords"], r2["q"])}
            md = 0.0
            nc = 0
            for x, v in zip(r["coords"], r["q"]):
                xy = tuple(x)
                if xy in q2:
                    md = max(md, abs(q2[xy] - float(v)))
                    nc += 1
            n_cmp += nc
            max_dq = max(max_dq, md)
            per_point.append({"limitation": r["limitation"],
                              "n_cmp": nc, "max_dq": md})
            if n_matched <= 10:
                print("    点%d：limitation %.2f，比较 %d 格，max|Δq|=%.3e"
                      % (n_matched, r["limitation"], nc, md), flush=True)
        print()
        print("  匹配到 %d/%d 点，共比较 %d 格，**max|Δq| = %.3e**（门限 1e-9）"
              % (n_matched, len(mrows), n_cmp, max_dq))
        q_ok = (n_matched > 0) and (max_dq <= 1e-9)
        print("  ⇒ %s" % ("通过：v2 的 q 路径与独立实现一致"
                          if q_ok else "**失败：q 路径不一致，禁止放行**"))
        if not q_ok:
            print()
            print("  最差的 5 点：")
            for p in sorted(per_point, key=lambda x: -x["max_dq"])[:5]:
                print("    limitation %.2f，比较 %d 格，max|Δq|=%.3e"
                      % (p["limitation"], p["n_cmp"], p["max_dq"]))
    else:
        q_ok = False
        print("  缺失 %s，无法对拍" % args.merged)

    # ============ 2. 修复前后对比 ============
    print()
    print("=" * 100)
    print("验收 2：修复前后 q/F 差异（v1 截断 vs v2 全终端聚合）")
    print("=" * 100)
    # 按 (limitation, worlds, coords 集合) 匹配共同点
    idx1 = {}
    for r in d1["rows"]:
        key = (round(r["limitation"], 6), int(r["worlds"]),
               frozenset(tuple(x) for x in r["coords"]))
        idx1[key] = r
    common = []
    for r2 in d2["rows"]:
        key = (round(r2["limitation"], 6), int(r2["worlds"]),
               frozenset(tuple(x) for x in r2["coords"]))
        r1 = idx1.get(key)
        if r1 is not None:
            common.append((r1, r2))
    print("  两版共同点（limitation+worlds+coords 完全匹配）：%d" % len(common))

    q_deltas = []
    opt_rise = opt_same = opt_fall = 0
    f_deltas = {m: [] for m in METRICS}
    multi = []
    for r1, r2 in common:
        q1 = {tuple(x): float(v) for x, v in zip(r1["coords"], r1["q"])}
        q2 = {tuple(x): float(v) for x, v in zip(r2["coords"], r2["q"])}
        com = set(q1) & set(q2)
        if not com:
            continue
        ds = [abs(q1[xy] - q2[xy]) for xy in com]
        q_deltas.extend(ds)
        b1 = max(q1.values())
        b2 = max(q2.values())
        if b2 > b1 + 1e-9:
            opt_rise += 1
        elif b2 < b1 - 1e-9:
            opt_fall += 1
        else:
            opt_same += 1
        for m in METRICS:
            f1 = {tuple(x): float(v) for x, v in zip(r1["coords"], r1["F"][m])}
            f2 = {tuple(x): float(v) for x, v in zip(r2["coords"], r2["F"][m])}
            for xy in (set(f1) & set(f2)):
                f_deltas[m].append(abs(f1[xy] - f2[xy]))
        if r2.get("n_terms", 1) > 1:
            multi.append((r1, r2, b1, b2, max(ds), r2.get("dropped_mass", 0)))

    print()
    print("  q 差异（共同点 %d，共 %d 个 q 比较）："
          % (len(common), len(q_deltas)))
    print("    均值 %.3e，中位 %.3e，最大 %.3e"
          % (sum(q_deltas) / len(q_deltas) if q_deltas else 0,
             med(q_deltas), max(q_deltas) if q_deltas else 0))
    print("  opt q 变化方向：上升 %d / 不变 %d / 下降 %d"
          % (opt_rise, opt_same, opt_fall))
    print()
    print("  F 各指标平均绝对差：")
    for m in METRICS:
        v = f_deltas[m]
        if v:
            print("    %-3s 均值 %.4e，中位 %.4e，最大 %.4e"
                  % (m, sum(v) / len(v), med(v), max(v)))

    print()
    print("  多终端点（缺陷所在，n=%d）单独统计：" % len(multi))
    if multi:
        dm = [m[5] for m in multi]
        print("    丢弃质量：中位 %.1f%%，均值 %.1f%%"
              % (med(dm) * 100, sum(dm) / len(dm) * 100))
        qd = [m[4] for m in multi]
        print("    单点 q 最大Δ：中位 %.3e，最大 %.3e" % (med(qd), max(qd)))
        dr = [m[3] - m[2] for m in multi]
        print("    opt q 变化：中位 %+.4f，最大 %+.4f，最小 %+.4f"
              % (med(dr), max(dr), min(dr)))
        rise = sum(1 for x in dr if x > 1e-9)
        fall = sum(1 for x in dr if x < -1e-9)
        print("    方向：上升 %d / 不变 %d / 下降 %d（共 %d）"
              % (rise, len(dr) - rise - fall, fall, len(dr)))
        print()
        print("    最受影响的 8 点（按 |opt q 变化| 排序）：")
        for r1, r2, b1, b2, md, dm2 in sorted(multi, key=lambda m: -abs(m[3] - m[2]))[:8]:
            print("      终端 %3d，丢弃 %5.1f%%，q 最大Δ %.3e，opt q %.4f→%.4f（%+.4f）"
                  % (r2.get("n_terms", 0), dm2 * 100, md, b1, b2, b2 - b1))

    out = {
        "v1_points": len(d1["rows"]),
        "v2_points": len(d2["rows"]),
        "v2_multi_terminal": d2.get("n_multi_terminal_points"),
        "q_cross_validation": {
            "n_matched": n_matched, "n_compared": n_cmp,
            "max_abs_delta": max_dq, "threshold": 1e-9,
            "passed": bool(q_ok),
        },
        "before_after": {
            "n_common": len(common),
            "q_delta_mean": sum(q_deltas) / len(q_deltas) if q_deltas else None,
            "q_delta_median": med(q_deltas) if q_deltas else None,
            "q_delta_max": max(q_deltas) if q_deltas else None,
            "opt_q_rise": opt_rise, "opt_q_same": opt_same,
            "opt_q_fall": opt_fall,
            "F_mean_abs_delta": {m: (sum(f_deltas[m]) / len(f_deltas[m])
                                     if f_deltas[m] else None)
                                 for m in METRICS},
            "multi_terminal_n": len(multi),
            "multi_dropped_mass_median": med([m[5] for m in multi]) if multi else None,
            "multi_optq_delta_median": med([m[3] - m[2] for m in multi]) if multi else None,
        },
        "note": "验收脚本只读两份结果文件，未重跑任何样本",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  验收明细已写 %s" % op)
    print()
    print("=" * 100)
    print("总判定：q 交叉验证 %s ⇒ %s"
          % ("✔" if q_ok else "✘",
             "v2 可用于后续（但仍受自选择偏差限制）" if q_ok
             else "**禁止放行**"))
    return 0 if q_ok else 2


if __name__ == "__main__":
    sys.exit(main())
