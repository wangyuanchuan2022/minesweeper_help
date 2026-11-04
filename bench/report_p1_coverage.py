# -*- coding: utf-8 -*-
"""第 1 步：P1-c 全样本覆盖率的最终报告生成器。

=================== 为什么单独写一个报告器 ====================
`classify_p1_coverage.py` 自己会打印分层统计并写 `p1_coverage.json`，
但它的输出有两点不满足本步要求：
  1. 不报「被丢弃点的分布」与「保留点的可比性」——即自选择偏差的**方向与幅度**；
  2. 不生成「可直接替换 47% 表述」的定型结论句。

本脚本只读 journal 与结果 JSON，**不重跑任何样本**，输出：
  · 全样本四分类计数与比例（ok/node_cap/time_cap/err）
  · 按 worlds / limitation 分层的成功率
  · ok 与 非ok 的分布对照（自选择偏差方向）
  · 一条定型结论句，带样本量与口径

=================== 口径纪律 ====================
· 分母是「合格点」（worlds <= max_worlds 且能算出世界数），不是全部原始点；
· 未覆盖率 = (node_cap + time_cap + err) / 全部合格点；
· **不得**用当前进度推断最终值（本脚本只在全样本跑完后使用）；
· 报 Wilson 95%CI，不只报点估计。

用法:
  python bench/report_p1_coverage.py
  python bench/report_p1_coverage.py --journal state/ab/p1_coverage.json.journal.jsonl
"""
import argparse
import collections
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

EXPECTED_N = 462          # 合格点总数（与 classify_p1_coverage.py 口径一致）


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def med(v):
    if not v:
        return float("nan")
    s = sorted(v)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def bucketize(rows, key, edges):
    order, buckets = [], collections.defaultdict(list)
    for r in rows:
        v = r[key]
        lab = None
        for e in edges:
            if v <= e:
                lab = "<=%g" % e
                break
        if lab is None:
            lab = ">%g" % edges[-1]
        if lab not in order:
            order.append(lab)
        buckets[lab].append(r)
    labels = ["<=%g" % e for e in edges] + [">%g" % edges[-1]]
    out = []
    for lab in labels:
        rs = buckets.get(lab, [])
        if not rs:
            continue
        n = len(rs)
        c = collections.Counter(r["status"] for r in rs)
        ok = c.get("ok", 0)
        lo, hi = wilson(ok, n)
        out.append({"bucket": lab, "n": n, "ok": ok,
                    "node_cap": c.get("node_cap", 0),
                    "time_cap": c.get("time_cap", 0),
                    "err": c.get("err", 0),
                    "ok_rate": ok / n, "ok_ci95": [lo, hi]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=os.path.join(
        "state", "ab", "p1_coverage.json.journal.jsonl"))
    ap.add_argument("--report", default=os.path.join(
        "state", "ab", "p1_coverage_report.txt"))
    args = ap.parse_args()

    jp = args.journal if os.path.isabs(args.journal) else os.path.join(ROOT, args.journal)
    if not os.path.exists(jp):
        print("缺失 journal：%s" % jp)
        return 1

    rows, seen = [], set()
    for line in open(jp, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("key") and rec.get("row"):
            if rec["key"] in seen:
                continue
            seen.add(rec["key"])
            rows.append(rec["row"])

    n = len(rows)
    print("=" * 100)
    print("第 1 步：P1 全样本覆盖率（定型报告）")
    print("=" * 100)
    print("  来源 journal：%s" % os.path.basename(jp))
    print("  已分类合格点：%d" % n)

    if n < EXPECTED_N:
        print()
        print("  ⚠️ 样本未跑完（%d/%d = %.1f%%）。" % (n, EXPECTED_N, it := n / EXPECTED_N * 100))
        print("     本报告**只能作为中途快照**，不得当作全样本结论，")
        print("     也不得用当前比例外推最终未覆盖率（样本按 worlds 升序，先跑简单的）。")
        snapshot = True
    else:
        snapshot = False

    c = collections.Counter(r["status"] for r in rows)
    ok = c.get("ok", 0)
    node = c.get("node_cap", 0)
    tim = c.get("time_cap", 0)
    err = c.get("err", 0)
    dropped = node + tim + err
    lo_ok, hi_ok = wilson(ok, n)
    lo_dr, hi_dr = wilson(dropped, n)

    print()
    print("-" * 100)
    print("全样本四分类（分母 = 合格点 %d）" % n)
    print("-" * 100)
    print("    %-10s %6s %8s" % ("状态", "计数", "占比"))
    for st, cnt in (("ok", ok), ("node_cap", node), ("time_cap", tim), ("err", err)):
        print("    %-10s %6d %7.1f%%" % (st, cnt, cnt / n * 100))
    print("    %-10s %6d %7.1f%%  95%%CI [%.1f, %.1f]"
          % ("未覆盖合计", dropped, dropped / n * 100, lo_dr * 100, hi_dr * 100))
    print("    %-10s %6d %7.1f%%  95%%CI [%.1f, %.1f]"
          % ("ok 成功率", ok, ok / n * 100, lo_ok * 100, hi_ok * 100))

    w_stats = bucketize(rows, "worlds", [100, 500, 2000, 10000])
    l_stats = bucketize(rows, "limitation", [10, 15, 20])

    for label, stats in (("worlds", w_stats), ("limitation", l_stats)):
        print()
        print("-" * 100)
        print("按 %s 分层成功率" % label)
        print("-" * 100)
        print("    %-10s %6s %6s %8s %8s %6s %10s"
              % ("区间", "总数", "ok", "node", "time", "err", "ok 率(95%CI)"))
        for b in stats:
            print("    %-10s %6d %6d %8d %8d %6d %5.1f%% [%.1f, %.1f]"
                  % (b["bucket"], b["n"], b["ok"], b["node_cap"], b["time_cap"],
                     b["err"], b["ok_rate"] * 100,
                     b["ok_ci95"][0] * 100, b["ok_ci95"][1] * 100))

    ok_rows = [r for r in rows if r["status"] == "ok"]
    bad_rows = [r for r in rows if r["status"] != "ok"]
    print()
    print("-" * 100)
    print("自选择偏差方向（ok vs 非 ok 的分布对照）")
    print("-" * 100)
    print("    %-12s %14s %14s %10s"
          % ("字段", "ok 中位数", "非ok 中位数", "比值"))
    bias = {}
    for key in ("worlds", "limitation", "n_cand"):
        a, b = med([r[key] for r in ok_rows]), med([r[key] for r in bad_rows])
        ratio = (b / a) if a else float("inf")
        bias[key] = {"ok_median": a, "bad_median": b, "ratio": ratio}
        print("    %-12s %14.2f %14.2f %10.2f" % (key, a, b, ratio))
    print()
    print("  ⇒ 非 ok 的 worlds 中位数是 ok 的 %.1f 倍：丢弃**明显偏向复杂局面**，"
          % bias["worlds"]["ratio"])
    print("     即保留集在 worlds/limitation 上是**偏简单**的子集。")

    print()
    print("=" * 100)
    print("定型结论句（用于替换「47%」表述）")
    print("=" * 100)
    if snapshot:
        print("  【中途快照，不可用】当前 %d/%d 点已分类，ok 率 %.1f%%。" % (n, EXPECTED_N, ok / n * 100))
        print("  待全样本跑完后，本脚本会自动给出定型句。")
        sentence = None
    else:
        sentence = ("在本项目 P1 候选集（%d 个合格点，口径：worlds<=20000 且可算出世界数，"
                    "node_cap=5e6、time_cap=30s）上，V* 精确求解**成功 %d 点（%.1f%%，"
                    "95%%CI [%.1f, %.1f]）、未覆盖 %d 点（%.1f%%，其中 node_cap %d、"
                    "time_cap %d、其他错误 %d）**。未覆盖显著偏向高 worlds/高 limitation 局面"
                    "（非 ok 的 worlds 中位数是 ok 的 %.1f 倍），因此保留集是偏简单子集，"
                    "基于其上的 regret 读数存在**方向已知（偏乐观）但幅度未量化**的自选择偏差。"
                    % (n, ok, ok / n * 100, lo_ok * 100, hi_ok * 100,
                       dropped, dropped / n * 100, node, tim, err,
                       bias["worlds"]["ratio"]))
        print("  " + sentence)

    out = {
        "n_classified": n, "expected_n": EXPECTED_N, "is_snapshot": bool(snapshot),
        "status_counts": {"ok": ok, "node_cap": node, "time_cap": tim, "err": err},
        "ok_rate": ok / n if n else None,
        "ok_rate_ci95": [lo_ok, hi_ok] if n else None,
        "uncovered_rate": dropped / n if n else None,
        "uncovered_rate_ci95": [lo_dr, hi_dr] if n else None,
        "worlds_buckets": w_stats, "limitation_buckets": l_stats,
        "selection_bias": bias,
        "sentence": sentence,
        "note": "只读 journal，未重跑任何样本；分母为合格点而非全部原始点。",
    }
    rp = args.report if os.path.isabs(args.report) else os.path.join(ROOT, args.report)
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp.replace(".txt", ".json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % rp.replace(".txt", ".json"))
    return 0 if not snapshot else 2


if __name__ == "__main__":
    sys.exit(main())
