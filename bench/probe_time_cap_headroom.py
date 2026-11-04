# -*- coding: utf-8 -*-
"""探针：P1-c 的 259 个 time_cap 点，有多少是"cap 太紧"而非"真的算不动"？

=================== 为什么必须查这个 ====================
P1-c 全样本报告称未覆盖率 59.7%（276/462），其中 **259 点是 time_cap**、
node_cap **为 0**。而 time_cap 只设了 30 秒。

这引出一个致命的方法学问题（"未观察到 vs 不可能"）：
「30 秒内算不出」**不等于**「算不出」。
若 259 点里有相当比例只是差一点（例如 35s、60s 能算完），
那么"能算精确 q 的样本只覆盖简单局面（40.3%）"这个结论
**大幅低估了真实可达覆盖率**，
进而我上一轮据此宣布的"权重优化线结构性阻塞"就可能不成立。

本探针分层抽样，用放大后的 time_cap 重跑，量化：
  · 各 worlds 桶里，放大 cap 后的回收率
  · 回收所需的实际耗时分布
  · 据此估计"若用更大 cap，全样本可达覆盖率是多少"

用法:
  python bench/probe_time_cap_headroom.py --per-bucket 3 --tcap 180
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import ast                                                # noqa: E402
from classify_p1_coverage import classify                 # noqa: E402
from dump_metric_matrix_ext import case_key, count_worlds  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=3)
    ap.add_argument("--tcap", type=float, default=180.0)
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "time_cap_headroom.json"))
    args = ap.parse_args()

    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for c in json.load(f)["cases"]:
                    merged[case_key(c)] = c

    # 收集 P1-c journal 里所有 time_cap 点
    tc = []
    jp = os.path.join(ROOT, "state", "ab", "p1_coverage.json.journal.jsonl")
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rw = r.get("row")
            if rw and rw.get("status") == "time_cap":
                try:
                    c = merged.get(ast.literal_eval(r["key"]))
                except Exception:
                    continue
                if c is not None:
                    tc.append({"worlds": rw["worlds"],
                               "limitation": rw["limitation"],
                               "n_cand": rw["n_cand"], "total": rw["total"],
                               "case": c})
    print("=" * 100)
    print("time_cap headroom 探针")
    print("=" * 100)
    print("  P1-c time_cap 点总数：%d" % len(tc))
    print("  放大后 time_cap = %.0fs，node_cap = %d" % (args.tcap, args.node_cap))

    # 分层：按 worlds 分桶，每桶抽 per_bucket 个（取该桶内 worlds 最小的，最可能回收）
    buckets = [("<=500", 0, 500), ("501-2000", 500, 2000),
               ("2001-5000", 2000, 5000), ("5001-20000", 5000, 20001)]
    print()
    print("-" * 100)
    print("分层抽样（每桶取 worlds 最小的 %d 个）" % args.per_bucket)
    print("-" * 100)

    results = []
    for name, lo, hi in buckets:
        sel = sorted([t for t in tc if lo < t["worlds"] <= hi],
                     key=lambda t: (t["worlds"], t["n_cand"]))[:args.per_bucket]
        if not sel:
            print("  [%s] 无点" % name)
            continue
        print("  [%s] 抽 %d 点" % (name, len(sel)))
        for t in sel:
            c = t["case"]
            t0 = time.perf_counter()
            st, det = classify(c, args.node_cap, args.tcap)
            el = time.perf_counter() - t0
            results.append({"bucket": name, "worlds": t["worlds"],
                            "limitation": t["limitation"], "total": t["total"],
                            "n_cand": t["n_cand"], "status": st,
                            "detail": det, "secs": el})
            print("      worlds=%5d lim=%5.2f n_cand=%2d total=%4d -> %-9s %.1fs"
                  % (t["worlds"], t["limitation"], t["n_cand"], t["total"],
                     st, el), flush=True)

    print()
    print("-" * 100)
    print("回收率统计（按桶）")
    print("-" * 100)
    print("    %-12s %5s %5s %8s %10s" % ("桶", "样本", "回收", "回收率", "回收中位耗时"))
    total_n = 0
    total_ok = 0
    for name, lo, hi in buckets:
        rs = [r for r in results if r["bucket"] == name]
        if not rs:
            continue
        ok = [r for r in rs if r["status"] == "ok"]
        total_n += len(rs)
        total_ok += len(ok)
        med = statistics.median([r["secs"] for r in ok]) if ok else float("nan")
        print("    %-12s %5d %5d %7.0f%% %9.1fs"
              % (name, len(rs), len(ok), 100 * len(ok) / len(rs), med))

    print()
    if total_n:
        rate = total_ok / total_n
        print("  抽样总回收率：%d/%d = %.0f%%" % (total_ok, total_n, rate * 100))
        print()
        print("  ⚠️ 解释边界：")
        print("    · 本探针每桶只抽 %d 个 **worlds 最小**的点（偏乐观，最易回收）" % args.per_bucket)
        print("    · 因此本回收率是 **上界估计**，不是全 259 点的无偏估计")
        print("    · 但它足以回答一个关键问题：time_cap 里**确实存在**可回收点")
        print("      ⇒ '30 秒算不出' 不能直接当作 '算不出'")
        # 粗估全样本可达覆盖率
        p1 = os.path.join(ROOT, "state", "ab", "p1_coverage.json")
        if os.path.exists(p1):
            with open(p1, encoding="utf-8") as f:
                d = json.load(f)
            n_all = d["n"]
            ok0 = d["status_counts"].get("ok", 0)
            tc_n = d["status_counts"].get("time_cap", 0)
            print()
            print("  粗估（用抽样上界回收率外推，**仅作量级参考**）：")
            print("    当前 ok=%d/%d = %.1f%%" % (ok0, n_all, 100 * ok0 / n_all))
            est = ok0 + tc_n * rate
            print("    若 259 个 time_cap 按 %.0f%% 回收 ⇒ ok≈%.0f/%d = %.1f%%"
                  % (rate * 100, est, n_all, 100 * est / n_all))
            print("    ⇒ 可达覆盖率可能显著高于 40.3%（但这是偏乐观上界）")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"n_time_cap_total": len(tc),
                   "probe_tcap": args.tcap, "node_cap": args.node_cap,
                   "per_bucket": args.per_bucket,
                   "results": results,
                   "sampled_n": total_n, "sampled_ok": total_ok,
                   "recovery_rate_optimistic_upper": (total_ok / total_n
                                                     if total_n else None),
                   "caveat": "每桶只抽 worlds 最小的点，回收率是偏乐观上界"},
                  f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
