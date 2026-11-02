# -*- coding: utf-8 -*-
"""配对评估：同 seed 同棋盘，两套配置各跑一遍，用 McNemar 精确检验判差异。

为什么必须配对：
  独立批次实测跨批标准差 **3.66pp**（200 局），意味着 "+4pp 提升"在独立比较里
  完全不可信。配对设计能消掉"棋盘难度"这个最大的方差来源——同一个 seed 下
  两臂面对的是**逐字节相同**的棋盘，差异只可能来自策略本身。

统计口径：
  · b = A 赢 B 输的局数；c = A 输 B 赢的局数
  · McNemar 精确检验（二项分布双尾）→ p 值
  · 另报配对胜率差 = (b - c)/N，及其 95% CI（配对比例差的 Wald 区间）

用法:
  python bench/paired_eval.py --games 500 --compare "8,999"
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "sim", "eval_harness.py")
STATE = os.path.join(ROOT, "state", "paired")


def run_arm(tag, threshold, games, workers, seed, w, h, a, limit, extra=None):
    os.makedirs(STATE, exist_ok=True)
    out = os.path.join(STATE, f"{tag}.json")
    log = os.path.join(STATE, f"{tag}.log")
    # ⚠️ 每臂必须用**独立的 state_dir**：eval_harness 的并行子进程会把
    # cfg/seeds/chunk 写在 <state_dir>/chunks 下，两臂共用会互相覆盖
    # （实测：A 臂跑完时 B 臂的 chunk 文件已被写成 A 的内容，导致配对错乱）。
    arm_state = os.path.join(STATE, f"state_{tag}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, HARNESS, "--games", str(games),
           "--workers", str(workers), "--seed", str(seed),
           "--w", str(w), "--h", str(h), "--a", str(a), "--limit", str(limit),
           "--wr-threshold", str(threshold), "--state-dir", arm_state,
           "--out", out]
    if extra:
        cmd.extend(extra)
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=f, env=env,
                         stdin=subprocess.DEVNULL).wait()
    dt = time.time() - t0
    if not os.path.exists(out):
        raise RuntimeError(f"臂 {tag} 失败，见 {log}")
    with open(out, encoding="utf-8") as f:
        return json.load(f), dt


def mcnemar_exact(b, c):
    """McNemar 精确检验（二项双尾）。b/c 为两个不一致方向的对数。"""
    n = b + c
    if n == 0:
        return 1.0
    # 双尾：2 * P(X <= min(b,c))，X ~ Binom(n, 0.5)，上限截到 1
    k = min(b, c)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--arm-a", type=float, default=8.0, help="A 臂 wr_threshold")
    ap.add_argument("--arm-b", type=float, default=999.0, help="B 臂 wr_threshold")
    ap.add_argument("--tag-a", default="A")
    ap.add_argument("--tag-b", default="B")
    args = ap.parse_args()

    print("=" * 78)
    print(f"配对评估：{args.games} 局 × 2 臂，seed={args.seed}")
    print(f"A 臂 [{args.tag_a}] wr_threshold={args.arm_a}")
    print(f"B 臂 [{args.tag_b}] wr_threshold={args.arm_b}")
    print("同 seed 同棋盘 → 差异只来自策略，不含棋盘难度噪声")
    print("=" * 78)

    da, ta = run_arm(f"{args.tag_a}_a{args.arm_a}", args.arm_a, args.games,
                     args.workers, args.seed, args.w, args.h, args.a, args.limit)
    print(f"A 臂完成：{da['wins']}/{da['n']} = {da['rate']*100:.2f}%  ({ta:.0f}s)")
    db, tb = run_arm(f"{args.tag_b}_a{args.arm_b}", args.arm_b, args.games,
                     args.workers, args.seed, args.w, args.h, args.a, args.limit)
    print(f"B 臂完成：{db['wins']}/{db['n']} = {db['rate']*100:.2f}%  ({tb:.0f}s)")

    ra = {r["seed"]: r for r in da["results"] if "error" not in r}
    rb = {r["seed"]: r for r in db["results"] if "error" not in r}
    seeds = sorted(set(ra) & set(rb))
    n = len(seeds)
    b = c = both_win = both_lose = 0
    diffs = []
    for s in seeds:
        wa, wb = ra[s]["won"], rb[s]["won"]
        if wa and not wb:
            b += 1
            diffs.append((s, "A赢B输"))
        elif wb and not wa:
            c += 1
            diffs.append((s, "A输B赢"))
        elif wa and wb:
            both_win += 1
        else:
            both_lose += 1

    print("-" * 78)
    print(f"配对局数 {n}")
    print(f"  两臂都赢 {both_win}   两臂都输 {both_lose}")
    print(f"  A 独赢 {b}   B 独赢 {c}   （不一致 {b+c} 局 = {(b+c)/n*100:.1f}%）")
    rate_a = (both_win + b) / n * 100 if n else 0
    rate_b = (both_win + c) / n * 100 if n else 0
    print(f"  A 胜率 {rate_a:.2f}%   B 胜率 {rate_b:.2f}%   "
          f"差 {rate_a-rate_b:+.2f}pp")
    p = mcnemar_exact(b, c)
    # 配对比例差的 95% CI（Wald，以不一致局为条件）
    if b + c > 0:
        d = (b - c) / n
        se = math.sqrt((b + c) / n ** 2 - ((b - c) / n) ** 2 / n)
        lo, hi = d - 1.96 * se, d + 1.96 * se
        print(f"  配对差 95%CI [{lo*100:+.2f}pp, {hi*100:+.2f}pp]   "
              f"McNemar p = {p:.4f}  {'✅ 显著' if p < 0.05 else '❌ 不显著'}")
    else:
        print("  两臂完全一致，无差异可检")
        lo = hi = 0.0
        print(f"  配对差 95%CI [{lo:+.2f}pp, {hi:+.2f}pp]   McNemar p = {p:.4f}")
    if diffs and len(diffs) <= 20:
        print(f"  不一致明细：{diffs}")

    # 附：平均猜测决策数 / 步数 / 判定（机制解释）
    for tag, d in ((args.tag_a, da), (args.tag_b, db)):
        ok = [r for r in d["results"] if "error" not in r]
        if ok:
            print(f"  [{tag}] 平均猜测决策 {sum(r['n_guess'] for r in ok)/len(ok):.2f}"
                  f"  平均步数 {sum(r['steps'] for r in ok)/len(ok):.1f}"
                  f"  平均点击 {sum(r['n_click'] for r in ok)/len(ok):.0f}")

    print()
    print("判据：p<0.05 且配对差 CI 不含 0 才算真提升；否则视为噪声。")


if __name__ == "__main__":
    main()
