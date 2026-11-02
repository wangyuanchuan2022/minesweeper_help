# -*- coding: utf-8 -*-
"""统计 limitation 分布，回答"win_rate 前瞻搜索到底触发过几次"。

limitation = 0.8 * len(clicks9) + log2(total)
              内部格数            总局面数

只有 limitation <= wr_threshold 时才调用 win_rate（递归前瞻）。
历史结论"阈值 8 优于 999"是独立批次的噪声；配对实验显示两臂逐局完全一致
⇒ 说明 win_rate 根本没被触发过（或触发但从不改变决策）。

本脚本直接扫描真实对局里的 limitation 分布。

用法: python bench/probe_limitation.py --games 60
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args()

    # 用 --diag 收集每次决策的 total / n_prob；limitation = 0.8*U + log2(total)，
    # 其中 U（内部格数）= n_unopened - n_prob（未开格减前沿格）
    import math
    import tempfile
    out = os.path.join(ROOT, "state", "eval_run", "diag_limitation.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, os.path.join(ROOT, "sim", "eval_harness.py"),
           "--games", str(args.games), "--workers", str(args.workers),
           "--wr-threshold", "9999", "--diag", "--limit", str(args.limit),
           "--out", out]
    sub = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env,
                           stdin=subprocess.DEVNULL)
    sub.wait()
    if not os.path.exists(out):
        print(f"评估失败：{out} 不存在")
        return
    with open(out, encoding="utf-8") as f:
        d = json.load(f)

    lims = []
    n_dec = 0
    for r in d["results"]:
        if "error" in r:
            continue
        for rec in (r.get("records") or []):
            n_dec += 1
            total = rec.get("total") or 0
            n_prob = rec.get("n_prob") or 0
            n_unop = rec.get("n_unopened") or 0
            U = max(0, n_unop - n_prob)      # 无约束内部格
            if total <= 0:
                total = 1
            lim = 0.8 * U + math.log2(total)
            lims.append((lim, total, U, n_prob))

    print("=" * 76)
    print(f"limitation 分布（{args.games} 局，{n_dec} 次概率决策）")
    print("=" * 76)
    if not lims:
        print("没有采集到决策记录")
        return
    lims.sort()
    vals = [x[0] for x in lims]

    def pct(p):
        return vals[min(len(vals) - 1, int(p * len(vals)))]

    print(f"  min {vals[0]:.2f}  p10 {pct(0.10):.2f}  p25 {pct(0.25):.2f}  "
          f"中位数 {pct(0.50):.2f}  p75 {pct(0.75):.2f}  p90 {pct(0.90):.2f}  "
          f"max {vals[-1]:.2f}")
    print()
    for th in (0, 2, 4, 6, 8, 10, 12, 16, 20, 30, 50, 100):
        cnt = sum(1 for v in vals if v <= th)
        print(f"  limitation <= {th:>4} 的决策：{cnt:5d}/{len(vals)} "
              f"= {cnt/len(vals)*100:5.1f}%"
              f"{'   ← 若阈值设此值，这些走前瞻搜索' if cnt else ''}")
    print()
    # 关键：有多少决策的 limitation 在 8 以下（当前阈值）
    n8 = sum(1 for v in vals if v <= 8)
    print(f"**当前阈值 8：{n8}/{len(vals)} = {n8/len(vals)*100:.2f}% 的决策走 win_rate**")
    if n8 == 0:
        print("   ⇒ win_rate 从未触发 ⇒ 调 wr_threshold 不可能改变任何决策")
        print("   （这就是配对实验里 8 与 999 逐局完全一致的原因）")
    print()
    print("distribution 分桶：")
    buckets = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 12), (12, 20),
               (20, 30), (30, 50), (50, 1e9)]
    for lo, hi in buckets:
        cnt = sum(1 for v in vals if lo <= v < hi)
        bar = "#" * int(cnt / len(vals) * 50)
        print(f"  [{lo:>4},{hi if hi < 1e9 else '∞'}) : {cnt:5d} "
              f"({cnt/len(vals)*100:5.1f}%) {bar}")
    # 总局面数分布（看是不是 total 太大把 limitation 顶上去）
    print()
    tots = sorted(x[1] for x in lims)
    print(f"total（总局面数）中位数 {tots[len(tots)//2]:.0f}  "
          f"p90 {tots[int(0.9*len(tots))]:.0f}  max {tots[-1]:.0f}")
    Us = sorted(x[2] for x in lims)
    print(f"内部格数 U 中位数 {Us[len(Us)//2]:.0f}  "
          f"p90 {Us[int(0.9*len(Us))]:.0f}  max {Us[-1]:.0f}")


if __name__ == "__main__":
    main()
