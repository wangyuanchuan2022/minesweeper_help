# -*- coding: utf-8 -*-
"""扫描 wr_threshold：量化"前瞻胜率搜索"到底值多少胜率。

背景：number5_1 在 limitation <= wr_threshold 时走 win_rate（递归前瞻搜索，
把每种未来都玩一遍），否则走 process_bigger_situation（贪心取最大安全率）。
limitation = 0.8*内部格数 + log2(总局面数)。当前 C++ 下调到 8，意味着
内部格 ≥10 就放弃前瞻 —— 大量局面其实没做前瞻。

本脚本对同一批 seed 跑多档阈值，比较胜率。前瞻越深，越可能把"50/50 必猜"
选成"对后续更有利"的那一格。

用法:
  python bench/sweep_wr_threshold.py --games 200 --thresholds 8 16 24 999
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "sim", "eval_harness.py")
STATE = os.path.join(ROOT, "state", "sweep")


def run_one(threshold, games, workers, seed, w, h, a, limit):
    os.makedirs(STATE, exist_ok=True)
    out = os.path.join(STATE, f"t{threshold}.json")
    log = os.path.join(STATE, f"t{threshold}.log")
    cmd = [sys.executable, HARNESS, "--games", str(games),
           "--workers", str(workers), "--seed", str(seed),
           "--w", str(w), "--h", str(h), "--a", str(a), "--limit", str(limit),
           "--wr-threshold", str(threshold), "--out", out]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=f, env=env,
                             stdin=subprocess.DEVNULL)
        p.wait()
    dt = time.time() - t0
    if not os.path.exists(out):
        return None, dt, log
    with open(out, encoding="utf-8") as f:
        d = json.load(f)
    return d, dt, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--thresholds", type=float, nargs="+",
                    default=[8, 12, 16, 20, 30, 999])
    args = ap.parse_args()

    print("=" * 78)
    print(f"wr_threshold 扫描：{args.games} 局/档，seed={args.seed}，"
          f"棋盘 {args.w}x{args.h}/{args.a} 雷，limit={args.limit}")
    print("口径：同 seed 同开局，只改前瞻阈值 → 配对可比")
    print("=" * 78)
    rows = []
    base_rate = None
    for th in args.thresholds:
        d, dt, log = run_one(th, args.games, args.workers, args.seed,
                             args.w, args.h, args.a, args.limit)
        if d is None:
            print(f"  阈值 {th}: 失败（见 {log}）")
            continue
        rate = d["rate"] * 100
        lo, hi = d["ci"]
        if base_rate is None:
            base_rate = rate
        delta = rate - base_rate
        # 平均深搜次数
        res = [r for r in d["results"] if "error" not in r]
        n_deep = sum(1 for r in res for _ in range(0))  # 占位（不自带 deep 计数）
        rows.append((th, d["wins"], d["n"], rate, lo * 100, hi * 100, dt, delta))
        print(f"  阈值 {th:>6}: 胜率 {d['wins']:>3}/{d['n']} = {rate:6.2f}%  "
              f"CI [{lo*100:5.2f}%,{hi*100:5.2f}%]  差基线 {delta:+6.2f}pp  "
              f"({dt:.0f}s)")

    print("=" * 78)
    print("汇总（以第一档为基线）：")
    print(f"{'阈值':>8} {'胜局':>5} {'胜率':>8} {'CI 下':>8} {'CI 上':>8} {'Δpp':>8}")
    for th, wins, n, rate, lo, hi, dt, delta in rows:
        print(f"{th:>8} {wins:>5} {rate:>7.2f}% {lo:>7.2f}% {hi:>7.2f}% "
              f"{delta:>+8.2f}")
    print()
    print("读法：若高阈值显著高于 8，说明'更常做前瞻搜索'直接值这么多胜率；")
    print("      若持平，说明前瞻在当前局面分布下没有额外信息可用。")


if __name__ == "__main__":
    main()
