# -*- coding: utf-8 -*-
"""测量评估台的**噪声地板**：同配置重跑多次，看胜率波动多大。

为什么必须先做这个：
  200 局、胜率 ~52% 时，Wilson 95% CI 半宽约 ±6.9pp。若同一配置重跑的波动
  就有 ±3pp，那么"策略 A 比 B 高 4pp"根本不可信——那可能只是噪声。
  在做任何算法改动前后对比之前，必须先知道"多大的差才算真差"。

用法:
  python bench/measure_noise.py --games 200 --repeats 5 --threshold 8
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "sim", "eval_harness.py")
STATE = os.path.join(ROOT, "state", "noise")


def run_once(threshold, games, workers, seed, w, h, a, limit, tag):
    os.makedirs(STATE, exist_ok=True)
    out = os.path.join(STATE, f"{tag}.json")
    log = os.path.join(STATE, f"{tag}.log")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, HARNESS, "--games", str(games),
           "--workers", str(workers), "--seed", str(seed),
           "--w", str(w), "--h", str(h), "--a", str(a), "--limit", str(limit),
           "--wr-threshold", str(threshold), "--out", out]
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=f, env=env,
                         stdin=subprocess.DEVNULL).wait()
    dt = time.time() - t0
    if not os.path.exists(out):
        return None, dt
    with open(out, encoding="utf-8") as f:
        return json.load(f), dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--threshold", type=float, default=8)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args()

    print("=" * 78)
    print(f"噪声地板：同配置跑 {args.repeats} 次 × {args.games} 局，"
          f"阈值 {args.threshold}，seed 起点 {args.seed}")
    print("=" * 78)
    rates, wins, ns = [], [], []
    for i in range(args.repeats):
        # 每次换 seed 起点 = 换一批棋盘；同 seed 重跑应完全可复现
        d, dt = run_once(args.threshold, args.games, args.workers,
                         args.seed + i * 10000, args.w, args.h, args.a,
                         args.limit, f"rep{i}")
        if d is None:
            print(f"  第 {i+1} 次失败")
            continue
        rates.append(d["rate"] * 100)
        wins.append(d["wins"])
        ns.append(d["n"])
        print(f"  第 {i+1} 次：{d['wins']}/{d['n']} = {d['rate']*100:6.2f}%  "
              f"CI ±{(d['ci'][1]-d['ci'][0])/2*100:.2f}pp  ({dt:.0f}s)")

    if len(rates) >= 2:
        mean = statistics.mean(rates)
        sd = statistics.stdev(rates)
        print("-" * 78)
        print(f"均值 {mean:.2f}%   标准差 {sd:.2f}pp   范围 "
              f"[{min(rates):.2f}%, {max(rates):.2f}%]  "
              f"极差 {max(rates)-min(rates):.2f}pp")
        print()
        print("判据：")
        print(f"  · 单次 200 局的 Wilson CI 半宽约 ±6.9pp（统计不确定性）")
        print(f"  · 实测跨批标准差 {sd:.2f}pp（换棋盘带来的方差）")
        print(f"  · 结论：小于约 {2*sd:.1f}pp 的差异**不可信**；"
              f"要检测 +4pp 需要更大样本")
        # 检测所需样本量（双比例，α=0.05, power=0.8）
        p1, p2 = 0.52, 0.56
        from math import sqrt, asinh
        eff = asinh(2 * p2) - asinh(2 * p1)
        n_req = ((1.96 + 0.8416) / eff) ** 2  # Fisher 近似（每臂）
        print(f"  · 要把 +4pp（52%→56%）检成显著（α=0.05,power=0.8），"
              f"每臂约需 {n_req:.0f} 局")
        print(f"  · 本台单局 0.55s，18 并行 → {n_req:.0f} 局约 "
              f"{n_req*0.55/18/60:.1f} 分钟")

    # 复现性检查：同 seed 两次应完全一致
    print("-" * 78)
    d2a, _ = run_once(args.threshold, 40, args.workers, args.seed,
                      args.w, args.h, args.a, args.limit, "rep_same_a")
    d2b, _ = run_once(args.threshold, 40, args.workers, args.seed,
                      args.w, args.h, args.a, args.limit, "rep_same_b")
    if d2a and d2b:
        same = d2a["wins"] == d2b["wins"]
        print(f"复现性：同 seed 跑两次 → wins {d2a['wins']} vs {d2b['wins']}"
              f"  {'✅ 一致' if same else '⚠️ 不一致（存在未受控随机源）'}")
        if not same:
            ra = {r["seed"]: r["won"] for r in d2a["results"]}
            rb = {r["seed"]: r["won"] for r in d2b["results"]}
            diff = [(s, ra[s], rb[s]) for s in ra if ra.get(s) != rb.get(s)]
            print(f"  不一致局数 {len(diff)}：{diff[:8]}")


if __name__ == "__main__":
    main()
