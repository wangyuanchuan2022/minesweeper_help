# -*- coding: utf-8 -*-
"""从**多份原始逐局结果**合并重算配对统计（跨轮合并，全程不手输任何数字）。

=================== 为什么单独写这个 ====================
上一轮我把"第 1 轮 +5.00pp / 第 2 轮 −2.75pp / 合并 +0.16pp"写成手输常量，
结果复算脚本里参数顺序写反、Δ 符号整体翻。本脚本的纪律：
  1. 只读原始 json（每臂每 seed 的 won），不读任何汇总数字
  2. 复用 bench/recompute_paired_ab.py 的符号自证（必须先通过）
  3. 跨轮合并按 **seed 去重**（两轮 seed 段不重叠，若有重叠则报错拒绝合并）

符号约定（与 recompute_paired_ab.py 完全一致）：
    Δ = (甲赢乙输 − 乙赢甲输) / n，正 = 甲更好

用法:
  python bench/pool_paired_ab.py --raws state/ab/tiebreak_ab.json \
      state/ab/tiebreak_ab_rep2.json --arm-a open_plus_frontier --arm-b baseline
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

from recompute_paired_ab import (paired_diff, mde80, sign_test_exact,  # noqa: E402
                                 self_test)


def load_round(path, arm_a, arm_b):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    res = raw["results"]
    for arm in (arm_a, arm_b):
        if arm not in res:
            raise SystemExit(f"{path}: 缺臂 {arm}，实有 {list(res.keys())}")
    out = {}
    for s in set(res[arm_a]) & set(res[arm_b]):
        xa, xb = res[arm_a][s], res[arm_b][s]
        if xa.get("error") or xb.get("error"):
            continue
        out[int(s)] = (bool(xa["won"]), bool(xb["won"]))
    return out, raw.get("seed"), raw.get("games")


def tally(rows):
    a_only = b_only = both_w = both_l = 0
    for wa, wb in rows.values():
        if wa and not wb:
            a_only += 1
        elif wb and not wa:
            b_only += 1
        elif wa and wb:
            both_w += 1
        else:
            both_l += 1
    return dict(a_only=a_only, b_only=b_only, both_win=both_w,
                both_lose=both_l, n=a_only + b_only + both_w + both_l,
                wins_a=a_only + both_w, wins_b=b_only + both_w)


def show(label, st):
    a_only, b_only, n = st["a_only"], st["b_only"], st["n"]
    d, se, (lo, hi) = paired_diff(a_only, b_only, n)
    mde = mde80(a_only, b_only, n)
    p = sign_test_exact(a_only, b_only)
    wa, wb = st["wins_a"], st["wins_b"]
    # 一致性校验：不一致对之差必须等于胜场之差
    ok = (a_only - b_only) == (wa - wb)
    print(f"\n【{label}】")
    print(f"  n={n}  a_only={a_only}  b_only={b_only}  "
          f"both_win={st['both_win']}  both_lose={st['both_lose']}")
    print(f"  一致性校验 a_only−b_only == wins_a−wins_b : "
          f"{a_only - b_only} vs {wa - wb}  {'✔' if ok else '✘'}")
    print(f"  甲 {wa}/{n} = {wa / n * 100:.2f}%   乙 {wb}/{n} = "
          f"{wb / n * 100:.2f}%")
    print(f"  Δ = {d * 100:+.2f}pp   95%CI [{lo * 100:+.2f}, {hi * 100:+.2f}]pp")
    print(f"  MDE(80%) = {mde * 100:.2f}pp   McNemar 精确 p = {p:.4f}")
    return dict(label=label, d=d, lo=lo, hi=hi, p=p, mde=mde, check=ok, **st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raws", nargs="+", required=True)
    ap.add_argument("--arm-a", default="open_plus_frontier")
    ap.add_argument("--arm-b", default="baseline")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not self_test():
        return 1

    per_round = []
    pooled = {}
    dup = []
    for path in args.raws:
        rows, seed0, games = load_round(path, args.arm_a, args.arm_b)
        overlap = set(rows) & set(pooled)
        if overlap:
            dup.extend(sorted(overlap)[:5])
        pooled.update(rows)
        per_round.append((os.path.basename(path), seed0, games, rows))

    if dup:
        print(f"\n✘ 两轮 seed 有重叠（例 {dup}）⇒ 合并会把同一局重复计入，"
              f"拒绝合并。")
        return 2

    print("=" * 96)
    print(f"跨轮配对合并：{args.arm_a}（甲） vs {args.arm_b}（乙）")
    print("=" * 96)
    for name, seed0, games, rows in per_round:
        print(f"  读入 {name}: seed0={seed0} games={games} "
              f"有效配对={len(rows)}")

    results = []
    for name, seed0, games, rows in per_round:
        results.append(show(f"{name}（seed0={seed0}）", tally(rows)))
    results.append(show(f"合并（{len(pooled)} 局，seed 去重）", tally(pooled)))

    print("\n" + "=" * 96)
    print("汇总表（全部由原始逐局数据算出）")
    print("=" * 96)
    print(f"{'口径':<28} {'n':>5} {'Δ':>10} {'95%CI':>24} {'MDE':>9} {'p':>8}")
    print("-" * 96)
    for r in results:
        print(f"{r['label']:<28} {r['n']:>5} {r['d'] * 100:>+9.2f}pp "
              f"[{r['lo'] * 100:>+7.2f},{r['hi'] * 100:>+7.2f}]pp "
              f"{r['mde'] * 100:>8.2f}pp {r['p']:>8.4f}")

    # ---- 可复现性判据：各轮 Δ 是否同向 ----
    dirs = [1 if r["d"] > 1e-12 else (-1 if r["d"] < -1e-12 else 0)
            for r in results[:-1]]
    same_dir = len(set(dirs)) == 1 and 0 not in dirs
    print("\n" + "=" * 96)
    print("可复现性判据（比 p 值更硬）")
    print("=" * 96)
    print(f"  各轮 Δ 方向：{['+' if d > 0 else ('-' if d < 0 else '0') for d in dirs]}")
    if same_dir:
        print("  ✔ 各轮同向 —— 通过可复现性（仍需看合并 p 值）")
    else:
        print("  ✘ 各轮方向不一致 —— 未通过可复现性。")
        print("     真实有效的准则不应在换一批独立 seed 后翻转符号。")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"arm_a": args.arm_a, "arm_b": args.arm_b,
                       "rounds": results, "pooled_n": len(pooled),
                       "same_direction": same_dir}, f, ensure_ascii=False,
                      indent=1)
        print(f"\n结果已写 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
