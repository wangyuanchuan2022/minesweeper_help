# -*- coding: utf-8 -*-
"""跨轮合并首点实验：两轮独立 seeds 的合并配对统计 + 可复现性判定。

=================== 为什么必须合并两轮 ====================
  第 1 轮（seeds 20269000-，500 局）：corner01 +6.20pp，p=0.0386
  第 2 轮（seeds 20270000-，500 局）：corner01 +1.60pp，p=0.6117
两轮方向一致（都为正）但幅度差 4 倍，且第 2 轮不显著。

⇒ 单轮读数都不能定案。合并 1000 局给出更窄的 CI，
  并以**两轮是否同向 + 合并是否显著**作为判据。

纪律（沿用 bench/recompute_paired_ab.py）：
  · 只读原始逐局结果，不读汇总数字
  · 先跑人工符号自证
  · 一致性校验 a_only−b_only == wins_a−wins_b
  · 跨轮 seed 段不得重叠（重叠则拒绝合并）

用法:
  python bench/pool_first_click.py
"""
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from recompute_paired_ab import (paired_diff, mde80,  # noqa: E402
                                 sign_test_exact, self_test)

BASE = "center"
ARM = "corner01"


def load_round(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    res = d["results"]
    if BASE not in res or ARM not in res:
        raise SystemExit(f"{path}: 缺臂，实有 {list(res.keys())}")
    out = {}
    for s in set(res[ARM]) & set(res[BASE]):
        xa, xb = res[ARM][s], res[BASE][s]
        if xa.get("error") or xb.get("error"):
            continue
        out[int(s)] = (bool(xa["won"]), bool(xb["won"]))
    return out, d.get("seed"), d.get("games")


def show(label, rows):
    a_only = sum(1 for wa, wb in rows.values() if wa and not wb)
    b_only = sum(1 for wa, wb in rows.values() if wb and not wa)
    both_w = sum(1 for wa, wb in rows.values() if wa and wb)
    both_l = sum(1 for wa, wb in rows.values() if not wa and not wb)
    n = len(rows)
    wa_t = a_only + both_w
    wb_t = b_only + both_w
    d, _se, (lo, hi) = paired_diff(a_only, b_only, n)
    mde = mde80(a_only, b_only, n)
    p = sign_test_exact(a_only, b_only)
    ok = (a_only - b_only) == (wa_t - wb_t)
    print(f"\n【{label}】")
    print(f"  n={n}  {ARM}赢/{BASE}输={a_only}  反向={b_only}  "
          f"都赢={both_w} 都输={both_l}")
    print(f"  一致性 a_only−b_only == wins差 : "
          f"{a_only - b_only} vs {wa_t - wb_t}  {'✔' if ok else '✘'}")
    print(f"  {ARM} {wa_t}/{n} = {wa_t/n*100:.2f}%   "
          f"{BASE} {wb_t}/{n} = {wb_t/n*100:.2f}%")
    print(f"  Δ = {d*100:+.2f}pp   95%CI [{lo*100:+.2f}, {hi*100:+.2f}]pp")
    print(f"  MDE(80%) = {mde*100:.2f}pp   McNemar p = {p:.4f}")
    return dict(label=label, n=n, a_only=a_only, b_only=b_only,
                d=d, lo=lo, hi=hi, mde=mde, p=p, check=ok,
                wa=wa_t, wb=wb_t)


def main():
    if not self_test():
        return 1

    rounds = [
        ("第 1 轮 seeds 20269000+", "state/ab/first_click_500.json"),
        ("第 2 轮 seeds 20270000+", "state/ab/first_click_repl500.json"),
    ]

    per_round = []
    pooled = {}
    for label, path in rounds:
        if not os.path.exists(path):
            print(f"  缺文件 {path}，跳过")
            continue
        rows, seed0, games = load_round(path)
        overlap = set(rows) & set(pooled)
        if overlap:
            print(f"✘ 两轮 seed 重叠（例 {sorted(overlap)[:3]}）⇒ 拒绝合并")
            return 2
        pooled.update(rows)
        per_round.append((label, rows))

    print("=" * 100)
    print(f"首点实验跨轮合并：{ARM}（角落） vs {BASE}（中心）")
    print("=" * 100)

    results = [show(label, rows) for label, rows in per_round]
    results.append(show(f"合并（{len(pooled)} 局，seed 去重）", pooled))

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    print(f"{'口径':<26} {'n':>5} {'Δ':>10} {'95%CI':>23} {'MDE':>9} {'p':>8}")
    print("-" * 100)
    for r in results:
        print(f"{r['label']:<26} {r['n']:>5} {r['d']*100:>+9.2f}pp "
              f"[{r['lo']*100:>+7.2f},{r['hi']*100:>+7.2f}]pp "
              f"{r['mde']*100:>8.2f}pp {r['p']:>8.4f}")

    # ---- 可复现性 ----
    dirs = [1 if r["d"] > 1e-12 else (-1 if r["d"] < -1e-12 else 0)
            for r in results[:-1]]
    same_dir = len(set(dirs)) == 1 and 0 not in dirs
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    print(f"  各轮 Δ 方向：{['+' if d > 0 else ('-' if d < 0 else '0') for d in dirs]}"
          f"  {'✔ 同向' if same_dir else '✘ 不同向'}")
    pooled_r = results[-1]
    print(f"  合并 Δ = {pooled_r['d']*100:+.2f}pp，"
          f"95%CI [{pooled_r['lo']*100:+.2f}, {pooled_r['hi']*100:+.2f}]pp，"
          f"p={pooled_r['p']:.4f}")

    print()
    if not same_dir:
        print("  ⇒ ✘ 两轮方向不一致 ⇒ 未通过可复现性，不能宣称有效。")
    elif pooled_r["p"] >= 0.05:
        print("  ⇒ 两轮同向，但**合并后仍不显著**（p≥0.05）。")
        print(f"     CI 含 0：无法排除『无效应』。")
        print(f"     MDE={pooled_r['mde']*100:.2f}pp：小于此值的效应测不出，")
        print(f"     **不等于不存在**，但也**不能作为改进依据**。")
    else:
        print("  ⇒ 两轮同向且合并显著。")
        print(f"     ⚠️ 但需检查：第 1 轮 {results[0]['d']*100:+.2f}pp vs "
              f"第 2 轮 {results[1]['d']*100:+.2f}pp 的幅度差异是否合理。")
        print(f"     若差异过大（如 >3x），提示第 1 轮可能是偶然高估。")

    # ---- 效应量稳定性 ----
    if len(results) >= 3:
        d1, d2 = results[0]["d"], results[1]["d"]
        if abs(d2) > 1e-9:
            ratio = d1 / d2
            print(f"\n  效应量稳定性：第1轮/第2轮 = {ratio:.2f}x")
            if ratio > 2.5 or ratio < 0.4:
                print("     ⚠️ 两轮幅度差 >2.5 倍 ⇒ 首轮效应量很可能被高估，")
                print("        应以合并值（更保守）为准。")

    out = os.path.join(ROOT, "state", "ab", "first_click_pooled.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": ARM, "base": BASE, "rounds": results,
                   "pooled_n": len(pooled), "same_direction": same_dir},
                  f, ensure_ascii=False, indent=1)
    print(f"\n结果已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
