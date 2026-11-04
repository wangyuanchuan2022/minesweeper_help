# -*- coding: utf-8 -*-
"""从**逐局原始胜负结果**独立重算配对统计，并用人工样例锁定符号口径。

=================== 为什么必须这么写（本轮实际踩的坑）============
上一版报告里的 +5.00pp / −2.75pp / +0.16pp 全部来自我**手输的不一致对数量**
（凭 stdout 抄数字），而且我在 `analyze_ab_ci_and_redundancy.py` 里一度把
"甲赢乙输" 与 "乙赢甲输" 两个参数的顺序写反 ⇒ Δ 符号整体翻。**符号错一次
就会把整份结论的方向写反**，而肉眼很难发现（因为 p 值不变）。

因此本脚本的纪律：
  1. **只读逐局原始结果**（每臂每 seed 的 won 布尔），不读任何汇总数字
  2. **先跑人工符号自证样例**（已知答案，必须通过才继续）
  3. 所有 Δ / CI / p / MDE 都由同一份原始数据算出，一次成型

符号约定（写进代码，不靠记忆）：
    Δ = (甲臂胜率 − 乙臂胜率)
    a_only = 「甲赢、乙输」的局数          → 有利于甲
    b_only = 「乙赢、甲输」的局数          → 有利于乙
    Δ = (a_only − b_only) / n            ← a_only 大 ⇒ Δ 为正 ⇒ 甲更好

用法:
  python bench/recompute_paired_ab.py --raw state/ab/tiebreak_ab.json \
      --arm-a open_plus_frontier --arm-b baseline
"""
import argparse
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


# ---------------------------------------------------------------------------
def sign_test_exact(b, c):
    """精确二项双尾（McNemar 精确版）。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def paired_diff(b, c, n, z=1.96):
    """配对风险差与 CI。

    b = 甲赢乙输, c = 乙赢甲输。
    Δ = (b − c)/n（正 = 甲更好）。
    方差（McNemar 形式）：Var = [(b + c) − (b − c)^2/n] / n^2
    """
    d = (b - c) / n
    var = ((b + c) - (b - c) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return d, se, (d - z * se, d + z * se)


def mde80(b, c, n):
    """80% 功效、双侧 α=0.05 下的最小可检效应。"""
    _d, se, _ci = paired_diff(b, c, n)
    return (1.959964 + 0.841621) * se


# ---------------------------------------------------------------------------
# 人工符号自证样例（答案已知，必须全部通过）
# ---------------------------------------------------------------------------
def self_test():
    print("=" * 96)
    print("第 0 步：符号口径自证（人工构造已知答案，必须全部通过）")
    print("=" * 96)

    cases = [
        # (甲赢乙输, 乙赢甲输, n, 期望Δ符号, 说明)
        (10, 2, 20, +1, "甲明显更好 ⇒ Δ 必须为正"),
        (2, 10, 20, -1, "乙明显更好 ⇒ Δ 必须为负"),
        (5, 5, 20, 0, "完全对半 ⇒ Δ 必须为 0"),
    ]
    ok = True
    for b, c, n, want, desc in cases:
        d, se, (lo, hi) = paired_diff(b, c, n)
        got = 1 if d > 1e-12 else (-1 if d < -1e-12 else 0)
        passed = got == want
        ok = ok and passed
        print(f"  b={b:>2} c={c:>2} n={n:>2} → Δ={d*100:>+7.2f}pp "
              f"CI[{lo*100:>+6.2f},{hi*100:>+6.2f}]  {desc}  "
              f"{'✔' if passed else '✘ 符号错'}")

    # 附加断言：对称性 —— 交换 b/c 必须让 Δ 精确变号
    d1, _, _ = paired_diff(10, 2, 20)
    d2, _, _ = paired_diff(2, 10, 20)
    sym = abs(d1 + d2) < 1e-15
    ok = ok and sym
    print(f"  交换 b/c 后 Δ 变号精确：{d1*100:+.2f} vs {d2*100:+.2f} "
          f"{'✔' if sym else '✘'}")

    # 附加断言：CI 必须包含点估计
    d, se, (lo, hi) = paired_diff(10, 2, 20)
    inside = lo <= d <= hi
    ok = ok and inside
    print(f"  CI 包含点估计：{'✔' if inside else '✘'}")

    if not ok:
        print("\n✘ 符号自证未通过 —— 拒绝继续计算（避免把方向写反）")
        return False
    print("\n✔ 符号口径已锁定：Δ = (甲赢乙输 − 乙赢甲输)/n，正 = 甲更好\n")
    return True


# ---------------------------------------------------------------------------
def extract_paired(raw, arm_a, arm_b):
    """从原始逐局结果里提取配对信息。返回 dict。"""
    res = raw["results"]
    if arm_a not in res or arm_b not in res:
        raise SystemExit(f"原始文件缺臂：有 {list(res.keys())}，"
                         f"需要 {arm_a} 与 {arm_b}")
    A, B = res[arm_a], res[arm_b]
    seeds = sorted(set(A) & set(B), key=int)
    a_only = b_only = both_win = both_lose = 0
    excluded = 0
    for s in seeds:
        xa, xb = A[s], B[s]
        if xa.get("error") or xb.get("error"):
            excluded += 1
            continue
        wa, wb = bool(xa["won"]), bool(xb["won"])
        if wa and not wb:
            a_only += 1
        elif wb and not wa:
            b_only += 1
        elif wa and wb:
            both_win += 1
        else:
            both_lose += 1
    n = a_only + b_only + both_win + both_lose
    return {
        "arm_a": arm_a, "arm_b": arm_b,
        "n": n, "excluded": excluded,
        "a_only": a_only, "b_only": b_only,
        "both_win": both_win, "both_lose": both_lose,
        "wins_a": a_only + both_win,
        "wins_b": b_only + both_win,
    }


def report(stat):
    a_only, b_only, n = stat["a_only"], stat["b_only"], stat["n"]
    d, se, (lo, hi) = paired_diff(a_only, b_only, n)
    mde = mde80(a_only, b_only, n)
    p = sign_test_exact(a_only, b_only)
    wa, wb = stat["wins_a"], stat["wins_b"]

    print("=" * 96)
    print(f"配对统计：{stat['arm_a']}（甲） vs {stat['arm_b']}（乙）")
    print("=" * 96)
    print(f"  有效配对局数 n = {n}"
          f"（剔除 {stat['excluded']} 局有 error）")
    print()
    print(f"  甲赢乙输 (a_only) = {a_only:>4}   ← 有利于甲")
    print(f"  乙赢甲输 (b_only) = {b_only:>4}   ← 有利于乙")
    print(f"  两臂都赢          = {stat['both_win']:>4}")
    print(f"  两臂都输          = {stat['both_lose']:>4}")
    print()
    # 一致性校验：a_only − b_only 必须等于 wins_a − wins_b
    check = (a_only - b_only) == (wa - wb)
    print(f"  一致性校验：a_only−b_only = {a_only-b_only}，"
          f"wins_a−wins_b = {wa-wb}  "
          f"{'✔ 相等' if check else '✘ 不等（数据有问题）'}")
    print()
    print(f"  甲胜率 = {wa}/{n} = {wa/n*100:.2f}%")
    print(f"  乙胜率 = {wb}/{n} = {wb/n*100:.2f}%")
    print(f"  Δ（甲−乙） = {d*100:+.2f}pp")
    print(f"  95%CI（配对口径） = [{lo*100:+.2f}, {hi*100:+.2f}]pp")
    print(f"  MDE(80% 功效)     = {mde*100:.2f}pp")
    print(f"  McNemar 精确 p    = {p:.4f}")
    print()
    print("  表述口径：")
    if p < 0.05 and d > 0:
        print(f"    「甲显著优于乙（p={p:.4f}）」")
    elif p < 0.05 and d < 0:
        print(f"    「甲显著劣于乙（p={p:.4f}）」")
    else:
        print(f"    「本轮未检出显著差异（p={p:.4f}，MDE={mde*100:.2f}pp）：")
        print(f"      小于 {mde*100:.2f}pp 的效应不可测，不等于不存在。」")
    print(f"    可排除范围：真实 |Δ| 大于 {max(abs(lo), abs(hi))*100:.2f}pp "
          f"已被排除。")
    return {"d": d, "lo": lo, "hi": hi, "p": p, "mde": mde, **stat}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--arm-a", default="open_plus_frontier")
    ap.add_argument("--arm-b", default="baseline")
    ap.add_argument("--extra-pairs", default=None,
                    help="额外配对，格式 armA:armB,armA:armB")
    args = ap.parse_args()

    if not self_test():
        return 1

    with open(args.raw, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"原始文件 {args.raw}：games={raw.get('games')} "
          f"seed={raw.get('seed')} 臂={list(raw['results'].keys())}\n")

    out = {"raw": args.raw, "pairs": []}
    st = extract_paired(raw, args.arm_a, args.arm_b)
    out["pairs"].append(report(st))

    if args.extra_pairs:
        for spec in args.extra_pairs.split(","):
            a, b = spec.split(":")
            print("\n" + "-" * 96 + "\n")
            out["pairs"].append(report(extract_paired(raw, a, b)))

    dest = os.path.join(ROOT, "state", "ab", "paired_recomputed.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n独立重算结果已写 {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
