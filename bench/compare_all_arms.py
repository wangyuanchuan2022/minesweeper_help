# -*- coding: utf-8 -*-
"""汇总多臂整局 A/B 结果：胜率 + 配对 McNemar + 置信区间 + 资源统计。

输入：state/ab/arm_<name>.json（由 bench/ab_policy.py 产出，需同 --seed 同局数）
输出：终端表格 + state/ab/summary.json

统计口径（严格）：
  · 每臂胜率 + Wilson 95% CI
  · **配对**比较：以 baseline 为基准，逐 seed 配对
      b = baseline 赢、该臂输的局数
      c = baseline 输、该臂赢的局数
    McNemar 精确检验（二项双尾）→ p 值
    配对胜率差 = (c - b) / N，附 95% CI
  · 噪声地板：200 局独立批次的跨批标准差实测 σ ≈ 3.66pp（见 review 报告）。
    因此**非配对**的胜率差小于约 2σ 时不可信；本脚本一律用配对口径。
  · 同时报告耗时、单决策最大耗时、回退次数（衡量可用性，不只是胜率）

用法:
  python bench/compare_all_arms.py
  python bench/compare_all_arms.py --arms baseline exact --dir state/ab
"""
import argparse
import glob
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def paired_diff_ci(c, b, n, z=1.96):
    """配对比例差 (c-b)/n 的 Wald 95% CI（c=该臂赢、b=baseline 赢）。"""
    if n == 0:
        return (0.0, 0.0, 0.0)
    d = (c - b) / n
    # 配对差方差：Var(p12 - p21) = (p12 + p21 - (p12-p21)^2)/n
    p12, p21 = c / n, b / n
    var = (p12 + p21 - (p12 - p21) ** 2) / n
    h = z * math.sqrt(max(var, 0.0))
    return (d, d - h, d + h)


def load_arm(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = {r["seed"]: r for r in d.get("results", [])
            if not r.get("error")}
    return d, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "state", "ab"))
    ap.add_argument("--arms", nargs="*", default=None)
    ap.add_argument("--baseline", default="baseline")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "arm_*.json")))
    if not files:
        print(f"未找到结果文件：{args.dir}\\arm_*.json")
        print("请先运行 run_baselines.cmd")
        return

    arms = {}
    meta = {}
    for p in files:
        name = os.path.basename(p)[len("arm_"):-len(".json")]
        if args.arms and name not in args.arms:
            continue
        d, rows = load_arm(p)
        if not rows:
            print(f"[跳过] {name}：无有效局")
            continue
        arms[name] = rows
        meta[name] = d

    if not arms:
        print("无可用数据")
        return

    print("=" * 100)
    print("各算法臂整局胜率（配对设计：同 seed 同棋盘）")
    print("=" * 100)
    print(f"{'臂':<12} {'局数':>6} {'胜':>5} {'胜率':>8} {'95%CI':>20} "
          f"{'耗时s':>9} {'单决策最大ms':>13} {'回退率':>8}")
    print("-" * 100)
    order = [args.baseline] + sorted(a for a in arms if a != args.baseline)
    order = [a for a in order if a in arms]
    for a in order:
        rows = arms[a]
        n = len(rows)
        w = sum(1 for r in rows.values() if r.get("won"))
        lo, hi = wilson(w, n)
        sec = meta[a].get("seconds", 0)
        mx = max((r.get("arm_ms_max", 0) for r in rows.values()), default=0)
        calls = sum(r.get("arm_calls", 0) for r in rows.values())
        fb = sum(r.get("arm_fallback", 0) for r in rows.values())
        fbr = (fb / calls * 100) if calls else 0.0
        print(f"{a:<12} {n:>6} {w:>5} {w/n*100:>7.2f}% "
              f"[{lo*100:>6.2f}%, {hi*100:>6.2f}%] {sec:>9.0f} "
              f"{mx:>13.0f} {fbr:>7.1f}%")

    # 配对比较（以 baseline 为基准）
    if args.baseline in arms:
        base = arms[args.baseline]
        print()
        print("=" * 100)
        print(f"配对 McNemar 检验（基准 = {args.baseline}）")
        print("=" * 100)
        print(f"{'臂':<12} {'配对n':>6} {'base赢臂输':>11} {'臂赢base输':>11} "
              f"{'配对胜率差':>12} {'95%CI':>22} {'p值':>9} {'判定':>8}")
        print("-" * 100)
        summary = {}
        for a in order:
            if a == args.baseline:
                continue
            rows = arms[a]
            common = sorted(set(base) & set(rows))
            b = c = 0
            for sd in common:
                bw = bool(base[sd].get("won"))
                aw = bool(rows[sd].get("won"))
                if bw and not aw:
                    b += 1
                elif aw and not bw:
                    c += 1
            n = len(common)
            p = mcnemar_exact(b, c)
            d, dlo, dhi = paired_diff_ci(c, b, n)
            sig = "显著" if p < 0.05 else "不显著"
            print(f"{a:<12} {n:>6} {b:>11} {c:>11} "
                  f"{d*100:>+11.2f}pp [{dlo*100:>+7.2f}pp, {dhi*100:>+7.2f}pp] "
                  f"{p:>9.4f} {sig:>8}")
            summary[a] = {"n": n, "base_win_arm_lose": b, "arm_win_base_lose": c,
                          "paired_diff": d, "ci": [dlo, dhi], "p": p}
        with open(os.path.join(args.dir, "summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        print()
        print("读法：p ≥ 0.05 = 当前样本未检出差异（**不等于等效**）；")
        print("      配对差 CI 跨 0 = 无法排除无差异。噪声地板 σ≈3.66pp（200局批次）。")
        print(f"明细已写 {os.path.join(args.dir, 'summary.json')}")
    else:
        print(f"\n[提示] 未找到基准臂 {args.baseline}，跳过配对检验")


if __name__ == "__main__":
    main()
