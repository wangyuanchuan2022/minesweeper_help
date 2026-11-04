# -*- coding: utf-8 -*-
"""诊断：坐标下降的"多种子"是否为虚假重复。

=================== 问题的由来 ====================
`optimize_v3.py` 跑了 10 个"坐标下降种子"，10 个 LOO 值**完全相同**
（全部 0.025044）。这触发了本会话已反复强调的判别法则：

> 若两个差异很大的条件给出完全相同的结果，先怀疑读了常量/确定性路径，
> 而不是相信"确实没有差别"。

查代码确认：`coord_descent(pk, rounds, grid, init=None)` **不接受 seed 参数**。
它是一个**确定性**算法（从 init 出发做坐标线搜索，无随机性）。
因此对它循环 10 次"不同 seed"并没有改变任何输入 ⇒ 得到 10 个相同值是必然的，
**不是 10 个独立观测**。

⇒ 原判据 (b)"≥80% 种子 LOO < S0"对坐标下降而言是**虚假通过**：
   10/10 只是同一个确定性结果的重复计数，不能当作符号一致性证据。

=================== 本脚本要回答 ====================
坐标下降的真实"变异性来源"是 **init**（起点），不是 seed。据此重做：
  · 用 N 个**不同的 init**（等权 / S0 单指标 / 各单指标 / 随机 Dirichlet）
  · 每个 init 各自跑完整 LOO
  · 报 LOO 分布与符号一致性

这样得到的分布才是"坐标下降在这个数据集上有多稳"的真实刻画。

用法:
  python bench/diag_cd_variance.py --inits 10
"""
import argparse
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from optimize_v3 import Packed, coord_descent, METRICS     # noqa: E402


def load_rows(path):
    """优先读最终 JSON；不存在则从 journal 重建。"""
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)["rows"], "final"
    jp = p + ".journal.jsonl"
    rows = []
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == "ok":
                rows.append(r["row"])
    return rows, "journal"


def make_inits(n_rand=8, seed=12345):
    """构造不同起点：等权、S0、每个单指标、若干随机 Dirichlet。"""
    d = len(METRICS)
    inits = []
    inits.append(("等权", np.full(d, 1.0 / d)))
    for k, m in enumerate(METRICS):
        w = np.zeros(d)
        w[k] = 1.0
        inits.append(("单指标:" + m, w))
    rng = np.random.default_rng(seed)
    for i in range(n_rand):
        g = rng.gamma(1.0, size=d)
        inits.append(("随机Dirichlet#%d" % (i + 1), g / g.sum()))
    return inits


def loo_for_init(pk, init, cd_rounds, grid):
    """给定 init，跑留一：每折用**其余点**从该 init 做坐标下降。"""
    n = pk.n
    regs = np.zeros(n)
    all_idx = np.arange(n)
    for i in range(n):
        tr = np.delete(all_idx, i)
        pk_tr = pk.subset(tr)
        w, _ = coord_descent(pk_tr, rounds=cd_rounds, grid=grid, init=init)
        regs[i] = float(pk.regrets(w)[i])
    return float(np.mean(regs)), regs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--inits", type=int, default=8, help="随机 init 个数")
    ap.add_argument("--cd-rounds", type=int, default=15)
    ap.add_argument("--grid", type=int, default=11)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "cd_variance.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    print("=" * 100)
    print("坐标下降的真实变异性诊断（变异性来自 init，不是 seed）")
    print("=" * 100)
    print("  数据来源：%s（%d 点）" % (src, len(rows)))
    pk = Packed(rows)

    s0_w = np.zeros(len(METRICS))
    s0_w[0] = 1.0
    s0 = pk.mean_regret(s0_w)
    print("  S0 in-sample regret = %.6f" % s0)
    print()

    inits = make_inits(n_rand=args.inits)
    print("-" * 100)
    print("对 %d 个不同 init 各跑一次完整 LOO（n=%d）" % (len(inits), pk.n))
    print("-" * 100)

    results = []
    for name, init in inits:
        v, _ = loo_for_init(pk, init, args.cd_rounds, args.grid)
        results.append((name, v))
        print("    %-18s LOO = %.6f   (Δ vs S0 %+.6f)"
              % (name, v, v - s0), flush=True)

    vals = [v for _, v in results]
    print()
    print("-" * 100)
    print("分布")
    print("-" * 100)
    s = sorted(vals)
    below = sum(1 for v in vals if v < s0)
    print("  中位 %.6f   范围 [%.6f, %.6f]   极差 %.6f"
          % (statistics.median(s), s[0], s[-1], s[-1] - s[0]))
    print("  中位 Δ vs S0 = %+.6f" % (statistics.median(s) - s0))
    print("  低于 S0 的 init 数 = %d/%d (%.0f%%)"
          % (below, len(vals), 100 * below / len(vals)))

    # 判据（与 optimize_v3 一致，但作用于真实变异性来源）
    a = statistics.median(s) < s0
    b = below / len(vals) >= 0.8
    print()
    print("  判据：(a) 中位 LOO < S0  (b) >=80%% init LOO < S0")
    print("    (a)=%s  (b)=%s" % ("通过" if a else "未过", "通过" if b else "未过"))
    if a and b:
        vtxt = "有正向信号（且变异性来自 init，是真实观测）"
    elif a and not b:
        vtxt = "不稳定（中位低但符号一致性不足）"
    else:
        vtxt = "无正向信号"
    print("    ⇒ %s" % vtxt)

    print()
    print("=" * 100)
    print("关键说明：为什么原始结果的 10/10 一致是假的")
    print("=" * 100)
    print("  `coord_descent` 不接受 seed，是确定性算法；")
    print("  对它用 10 个'种子'跑，等价于把**同一个结果**重复 10 次。")
    print("  因此原 `optimize_v3.json` 中坐标下降的 (b)=通过 不能作为证据。")
    print("  本脚本改用 10 个不同 init，得到的才是真实分布。")

    out = {
        "n_points": pk.n, "s0_in_sample": s0,
        "method": "coord_descent over distinct inits (not seeds)",
        "results": [{"init": n, "loo": v, "delta_vs_s0": v - s0}
                    for n, v in results],
        "median": statistics.median(s), "min": s[0], "max": s[-1],
        "range": s[-1] - s[0], "delta_vs_s0": statistics.median(s) - s0,
        "n_below_s0": below, "n_inits": len(vals),
        "criterion_a": bool(a), "criterion_b": bool(b), "verdict": vtxt,
        "note": "原始 optimize_v3 的 10 个'种子'是同一确定性结果的重复，本文件是更正后读数",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
