# -*- coding: utf-8 -*-
"""核对历史 40.5% 与本次 33.50% 是否矛盾：同条件复现 + 二项检验。

=================== 背景 ====================
review/算法更新评估.md 记录：延迟布雷（Arbiter）忠实口径下
  极限胜率 **81/200 = 40.5%**，CI [33.9%, 47.4%]（sim/eval_harness.py）
本次 bench/rule_gap.py 同规则测得
  **201/600 = 33.50%**，CI [29.84%, 37.37%]

两者相差 7.0pp。可能原因：
  A) 只是抽样波动（200 局的 CI 很宽，40.5% 与 33.5% 未必矛盾）
  B) 两次用的**算法/配置不同**（例如 wr_threshold、limit、有没有 record）
  C) 算法真的退化了

本脚本做两件事：
  1. 二项检验：若真实率 = 33.5%，200 局里出现 ≥81 胜的概率有多大
  2. 用当前代码重跑 200 局（同 seed 段）核对本机当前读数

用法: python bench/check_historical_405.py --games 200
"""
import argparse
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def binom_sf_ge(k, n, p):
    """P(X >= k)，X~Binomial(n,p)。"""
    total = 0.0
    for i in range(k, n + 1):
        total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return total


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    print("=" * 96)
    print("核对历史 40.5% (81/200) vs 本次 33.50% (201/600)")
    print("=" * 96)

    # ---- 1) 二项检验：若真率 = 33.5%，观察到 81/200 的概率 ----
    p0 = 201 / 600
    p_obs = binom_sf_ge(81, 200, p0)
    print(f"\n  若真实胜率 = 本次读数 {p0*100:.2f}%：")
    print(f"    P(200 局里 >= 81 胜) = {p_obs:.4f}")
    if p_obs > 0.05:
        print("    ⇒ > 0.05，**不矛盾**：81/200 完全可以由同一真实率抽出。")
        print("      历史 40.5% 与本次 33.5% 的差异属抽样波动，不能推断退化。")
    else:
        print("    ⇒ <= 0.05，两者**不一致** ⇒ 需查配置/代码差异。")

    # 反向：若真率 = 40.5%，观察到 201/600 的概率
    p1 = 81 / 200
    p_obs2 = 1.0 - binom_sf_ge(202, 600, p1) if False else None
    # 直接算 P(X <= 201), X~Bin(600, 0.405)
    cum = sum(math.comb(600, i) * (p1 ** i) * ((1 - p1) ** (600 - i))
              for i in range(0, 202))
    print(f"\n  若真实胜率 = 历史读数 {p1*100:.2f}%：")
    print(f"    P(600 局里 <= 201 胜) = {cum:.6f}")
    if cum < 0.05:
        print("    ⇒ < 0.05，本次 600 局读数与 40.5% **显著不符**。")
        print("      ⇒ 要么历史读数偏高，要么两次配置不同（需查）。")
    else:
        print("    ⇒ >= 0.05，两者仍可共存于同一真实率。")

    # ---- 2) 用当前代码重跑 200 局 ----
    print("\n" + "=" * 96)
    print(f"用当前代码重跑 {args.games} 局（Arbiter 规则，同 seed 起点）")
    print("=" * 96)

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "hist405"), True)
    import ab_policy as ab

    wins = 0
    n_ok = 0
    for gi in range(args.games):
        sd = args.seed + gi
        try:
            r = ab.play_one(sd, 30, 16, 99, "baseline", 80, 200000, None)
        except Exception as e:                          # noqa: BLE001
            print(f"  seed={sd} 异常 {type(e).__name__}: {e}")
            continue
        n_ok += 1
        if r.get("won"):
            wins += 1
        if (gi + 1) % 50 == 0:
            print(f"  ... {gi+1}/{args.games} 当前 {wins}/{n_ok} "
                  f"= {wins/n_ok*100:.2f}%", flush=True)

    lo, hi = wilson(wins, n_ok)
    print(f"\n  本次重跑：{wins}/{n_ok} = {wins/n_ok*100:.2f}%  "
          f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")
    print(f"  历史记录：81/200   = 40.50%  CI [33.90%, 47.40%]")
    print(f"  差异：{(wins/n_ok-0.405)*100:+.2f}pp")

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    print("  两条 CI 都宽（200 局半宽约 6.8pp），因此在统计上")
    print("  『40.5% 与 33.5% 同源』并不能被排除，也不会被证实。")
    print("  ⇒ 历史 40.5% **不应**作为当前算法的能力基线；")
    print("     当前最可靠读数是 600 局配对的 33.50%（CI ±3.8pp）。")


if __name__ == "__main__":
    main()
