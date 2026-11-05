# -*- coding: utf-8 -*-
"""按用户纠正：能前瞻时以**前瞻为主准则**（不是破平局的辅助）。

=================== 用户明确的设计（2026-11-04）====================
"能前瞻时应该以前瞻作为主准则，前瞻是更强的结果"

这纠正了我此前的两个理解：
  ① 我做过「安全优先 → 收益破平局」（字典序/次级）——错在把前瞻降为辅助
  ② 我做过「min-max(S0, PLY3)」（两准则取小）——错在仍让 S0 参与压制前瞻

正确形态应当是：
    **能算前瞻时：直接 argmax(PLY3)**，S0 不再是主准则
    **算不动时：退回 S0（safety）或启发式**

前瞻深度随复杂度自适应（复杂度越高，深度越浅；浅到 k=1 等价于退回 safety）。

=================== 本脚本的比较（全部在同一批点上）====================
形态 A：S0 主准则（现状基线）           argmax(S0)
形态 B：PLY3 主准则（用户的设计）        argmax(PLY3)
形态 C：PLY2 主准则                    argmax(PLY2)
形态 D：min-max(S0, PLY3)（我此前的错误形态，作对照）  argmax(min(norm S0, norm PLY3))
形态 E：自适应混合：limitation <= T 时用 PLY3，否则用 S0

全部用 q（真实续玩胜率）评价。确定性 tie-break（坐标字典序）避免顺序随机。

=================== 判据（预注册）====================
主判据：形态 B 的 mean regret 是否显著低于形态 A，且 bootstrap CI 上界 < 0。
副判据：形态 E（自适应）能否接近形态 B 而成本更低（成本另行测）。

用法:
  python bench/ply_as_primary.py --limit 150
"""
import argparse
import json
import math
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from offline_multistep import (                            # noqa: E402
    load_v3_with_cases, build_multistep)


def argmax_det(score, coords):
    """确定性 argmax：并列时按 (y,x) 字典序取最小。"""
    mx = float(np.max(score))
    cand = [i for i in range(len(score)) if abs(score[i] - mx) < 1e-12]
    return min(cand, key=lambda i: (coords[i][1], coords[i][0]))


def norm(v):
    lo, hi = float(v.min()), float(v.max())
    return np.full_like(v, 0.5) if (hi - lo) < 1e-12 else (v - lo) / (hi - lo)


def sign_test(diffs):
    pos = sum(1 for d in diffs if d > 1e-12)   # worse
    neg = sum(1 for d in diffs if d < -1e-12)  # better
    n = pos + neg
    if n == 0:
        return 1.0, pos, neg
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / float(2 ** n)
    return min(1.0, 2.0 * tail), pos, neg


def boot_ci(diffs, n_boot=5000, seed=7):
    d = np.asarray(diffs, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = len(d)
    ms = np.empty(n_boot)
    for i in range(n_boot):
        ms[i] = d[rng.integers(0, n, n)].mean()
    return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--t-adaptive", type=float, default=12.0,
                    help="形态 E 的 limitation 分界（<=T 用 PLY3）")
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "ply_primary.json"))
    args = ap.parse_args()

    rows, cases = load_v3_with_cases(args.limit)
    rows, feats = build_multistep(rows, cases, 50_000_000)
    print("=" * 100)
    print("前瞻作为**主准则**（用户设计）vs 其他形态")
    print("=" * 100)
    print("  样本 %d 点，worlds %d~%d，中位 %d"
          % (len(rows), min(r["worlds"] for r in rows),
             max(r["worlds"] for r in rows),
             int(statistics.median([r["worlds"] for r in rows]))))
    print("  平局/并列一律用确定性 tie-break（坐标字典序），消除顺序随机性")

    # 计算 limitation（生产同款），用于形态 E
    def lim_of(r):
        return r["limitation"]

    usable = [i for i, r in enumerate(rows) if len(r["q"]) >= 3]
    print("  可用点（候选>=3）：%d" % len(usable))

    def eval_form(fn, name):
        regs = []
        for i in usable:
            r, f = rows[i], feats[i]
            q = np.asarray(r["q"], dtype=np.float64)
            coords = [tuple(int(v) for v in x) for x in r["coords"]]
            idx = fn(r, f, coords)
            regs.append(float(q.max() - q[idx]))
        return np.asarray(regs)

    forms = {}
    forms["A: S0主准则（基线）"] = eval_form(
        lambda r, f, co: argmax_det(f["S0"], co), "A")
    forms["B: PLY3主准则（用户设计）"] = eval_form(
        lambda r, f, co: argmax_det(f["PLY3"], co), "B")
    forms["C: PLY2主准则"] = eval_form(
        lambda r, f, co: argmax_det(f["PLY2"], co), "C")
    forms["D: min-max(S0,PLY3)（我此前形态）"] = eval_form(
        lambda r, f, co: argmax_det(np.minimum(norm(f["S0"]),
                                               norm(f["PLY3"])), co), "D")
    forms["E: 自适应（lim<=%.0f用PLY3否则S0）" % args.t_adaptive] = eval_form(
        lambda r, f, co: argmax_det(
            f["PLY3"] if lim_of(r) <= args.t_adaptive else f["S0"], co), "E")

    base = forms["A: S0主准则（基线）"]
    print()
    print("=" * 100)
    print("结果（q 评价，越小越好）")
    print("=" * 100)
    print("  %-34s %11s %13s %8s %8s %9s"
          % ("形态", "mean_reg", "Δ vs A", "更好", "更差", "p"))
    print("  " + "-" * 88)

    results = []
    for name, regs in forms.items():
        diffs = regs - base
        p, pos, neg = sign_test(diffs)
        lo, hi = boot_ci(diffs)
        delta = regs.mean() - base.mean()
        print("  %-34s %11.6f %+13.6f %8d %8d %9.4f"
              % (name, regs.mean(), delta, neg, pos, p), flush=True)
        results.append({"form": name, "mean_regret": float(regs.mean()),
                        "delta": float(delta), "n_better": neg, "n_worse": pos,
                        "p": p, "ci95": [lo, hi]})

    print()
    print("=" * 100)
    print("判据检验：形态 B（PLY3 主准则）vs 形态 A（S0 基线）")
    print("=" * 100)
    rb = next(r for r in results if r["form"].startswith("B:"))
    lo, hi = rb["ci95"]
    passed = (rb["delta"] < 0) and (hi < 0) and (rb["p"] < 0.05)
    print("  Δ = %+.6f，p = %.4f，bootstrap 95%%CI = [%+.6f, %+.6f]"
          % (rb["delta"], rb["p"], lo, hi))
    print("  ⇒ **%s**" % ("通过：前瞻作主准则显著优于纯安全度" if passed
                          else "未通过"))

    print()
    print("  与形态 D（我此前的 min-max）对比：")
    rd = next(r for r in results if r["form"].startswith("D:"))
    print("    B: %+.6f（PLY3 主准则）" % rb["delta"])
    print("    D: %+.6f（min-max）" % rd["delta"])
    if rb["delta"] <= rd["delta"]:
        print("    ⇒ **纯前瞻主准则优于 min-max** ⇒ 用户的设计更彻底、也更有效。")
        print("       （我此前用 min-max 让 S0 压制了前瞻，是次优形态）")
    else:
        print("    ⇒ min-max 略优，需进一步分析")

    print()
    print("  形态 E（自适应）与 B 的差距：")
    re_ = next(r for r in results if r["form"].startswith("E:"))
    print("    E: %+.6f  vs  B: %+.6f" % (re_["delta"], rb["delta"]))
    n_ply3 = sum(1 for i in usable if lim_of(rows[i]) <= args.t_adaptive)
    print("    形态 E 中走 PLY3 的点：%d/%d = %.1f%%"
          % (n_ply3, len(usable), 100 * n_ply3 / len(usable)))
    gap = re_["delta"] - rb["delta"]
    print("    E 比 B 差 %+.6f（即为节省成本付出的质量代价）" % gap)

    out = {"n_points": len(usable),
           "t_adaptive": args.t_adaptive,
           "results": results,
           "caliber": "v3 初始状态 + C++ 全枚举；确定性 tie-break",
           "caveat": "样本 worlds %d~%d；整局 A/B 未做"
                     % (min(r["worlds"] for r in rows),
                        max(r["worlds"] for r in rows)),
           "note": "用户设计：能前瞻时以前瞻为主准则"}
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
