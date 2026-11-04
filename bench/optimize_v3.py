# -*- coding: utf-8 -*-
"""第 4-5 步：随机搜索基线 + 坐标下降（**正确口径** v3 矩阵）。

=================== 与历史做法的三处关键差异 ====================
历史（`optimize_weights.py`）的问题：
  1. 数据建在 saturate 口径上 ⇒ q 与 F 都可能错（v1/v2 均如此）
  2. 单次 LOO 读数，符号不稳定（本会话已量化：跨度 0.0069 > 方法差异 0.0043）
  3. `--loo-limit 40` 缩小样本，与全量 S0 不同分母（Δ 高估 83%）

本脚本：
  1. **数据用 v3**（初始状态 + C++ 全枚举，q 与 F 同一状态同一候选集）
  2. LOO **多种子重复（默认 10）**，报分布（中位/范围/符号一致性），不报单点
  3. LOO 用**全样本**，不做 limit 截断

=================== 判据（预注册，看结果前写定）====================
主判据：**随机搜索/坐标下降的 LOO 分布中位数是否显著低于 S0**。
· 同时满足以下两条才算"有正向信号"：
  (a) 10 个种子的 LOO 中位数 < S0
  (b) 至少 8/10 个种子的 LOO < S0（符号一致性 ≥ 80%）
· 若只有中位数低但符号一致性 < 80% ⇒ "不稳定，不足以支持采纳"
· 若中位数不低于 S0 ⇒ "无正向信号"

⚠️ 即使有正向信号，也**不等于**可用于生产：
   样本仅覆盖 worlds≤1980 的局面（偏简单），整局 A/B 未做。

用法:
  python bench/optimize_v3.py --seeds 10 --trials 400 --cd-rounds 15
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")
PAD = -1e18


class Packed:
    """把 metric matrix 打包成定长 numpy 数组（padding 用有限大负数）。"""

    def __init__(self, rows):
        self.n = len(rows)
        self.m = len(METRICS)
        ncand = [len(r["q"]) for r in rows]
        self.C = max(ncand) if ncand else 0
        C, n = self.C, self.n
        self.F = np.full((self.m, n, C), PAD, dtype=np.float64)
        self.q = np.zeros((n, C), dtype=np.float64)
        self.qmax = np.zeros((n, 1), dtype=np.float64)
        for i, r in enumerate(rows):
            c = len(r["q"])
            for k, mname in enumerate(METRICS):
                self.F[k, i, :c] = r["F"][mname]
            self.q[i, :c] = r["q"]
            self.qmax[i, 0] = max(r["q"])

    def regrets(self, w):
        w = np.asarray(w, dtype=np.float64)
        score = np.einsum("k,kic->ic", w, self.F)
        idx = np.argmax(score, axis=1)
        picked = self.q[np.arange(self.n), idx]
        return self.qmax[:, 0] - picked

    def mean_regret(self, w):
        return float(np.mean(self.regrets(w)))

    def subset(self, idx):
        obj = Packed.__new__(Packed)
        obj.n = len(idx)
        obj.m = self.m
        obj.C = self.C
        ii = np.asarray(idx)
        obj.F = self.F[:, ii, :]
        obj.q = self.q[ii, :]
        obj.qmax = self.qmax[ii, :]
        return obj


def random_search(pk, trials, rng):
    best_w, best_r = None, None
    d = len(METRICS)
    for _ in range(trials):
        g = rng.gamma(shape=1.0, size=d)
        s = g.sum()
        if s <= 0:
            continue
        w = g / s
        r = pk.mean_regret(w)
        if best_r is None or r < best_r - 1e-15:
            best_w, best_r = w, r
    if best_w is None:
        best_w = np.full(d, 1.0 / d)
    return best_w


def coord_descent(pk, rounds=15, grid=11, init=None):
    d = len(METRICS)
    w = np.array(init, dtype=np.float64) if init is not None else np.full(d, 1.0 / d)
    w = w / w.sum()
    best_r = pk.mean_regret(w)
    n_evals = 0
    for _ in range(rounds):
        improved = False
        for k in range(d):
            others = np.delete(w, k)
            others_sum = others.sum()
            best_local = (best_r, w[k])
            best_cand = w.copy()
            for t in range(grid + 1):
                nk = t / grid
                if nk >= 1.0:
                    continue
                scale = (1.0 - nk) / others_sum if others_sum > 1e-12 else 0.0
                cand = w.copy()
                cand[k] = nk
                for j in range(d):
                    if j != k:
                        cand[j] = w[j] * scale
                r = pk.mean_regret(cand)
                n_evals += 1
                if r < best_local[0] - 1e-15:
                    best_local = (r, nk)
                    best_cand = cand
                    improved = True
            if best_local[0] < best_r - 1e-15:
                best_r = best_local[0]
                w = best_cand
        if not improved:
            break
    return w, n_evals


def loo_one_seed(pk, trials, cd_rounds, seed, method, verbose=False):
    """单种子的 LOO：每折**独立**搜索。返回 (mean_regret, regs)。"""
    n = pk.n
    rng = np.random.default_rng(seed)
    regs = np.zeros(n)
    all_idx = np.arange(n)
    for i in range(n):
        tr = np.delete(all_idx, i)
        pk_tr = pk.subset(tr)
        if method == "random":
            w = random_search(pk_tr, trials, rng)
        else:
            w, _ = coord_descent(pk_tr, rounds=cd_rounds)
        regs[i] = float(pk.regrets(w)[i])
        if verbose and (i + 1) % 100 == 0:
            print("        %s LOO %d/%d" % (method, i + 1, n), flush=True)
    return float(np.mean(regs)), regs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--trials", type=int, default=400)
    ap.add_argument("--cd-rounds", type=int, default=15)
    ap.add_argument("--max-points", type=int, default=0,
                    help="0=全部；>0 则只取前 N 点（LOO 代价 O(n)）")
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "optimize_v3.json"))
    args = ap.parse_args()

    # 优先读最终 JSON；若还没有，从 journal 重建
    mp = args.matrix if os.path.isabs(args.matrix) else os.path.join(ROOT, args.matrix)
    rows = None
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            rows = json.load(f)["rows"]
        print("数据来源：%s（最终文件，%d 点）"
              % (os.path.basename(mp), len(rows)))
    else:
        jp = mp + ".journal.jsonl"
        if not os.path.exists(jp):
            print("缺少 %s 与其 journal" % mp)
            return 1
        rows = []
        with open(jp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("status") == "ok":
                    rows.append(r["row"])
        print("数据来源：journal（矩阵尚未跑完，%d 点）" % len(rows))

    if not rows:
        print("无可用行")
        return 1
    if args.max_points and len(rows) > args.max_points:
        # 按 worlds 升序截断（数据本身已按 worlds 升序写入）
        rows = rows[:args.max_points]
        print("按 --max-points 截断到 %d 点" % len(rows))

    pk = Packed(rows)
    print("样本 n=%d，候选格合计 %d，C_max=%d"
          % (pk.n, sum(len(r["q"]) for r in rows), pk.C))

    print()
    print("=" * 100)
    print("第 4-5 步：随机搜索基线 + 坐标下降（正确口径 v3）")
    print("=" * 100)

    # 基线（零参数，无需 LOO）
    s0_w = np.zeros(len(METRICS))
    s0_w[0] = 1.0
    s0 = pk.mean_regret(s0_w)
    eq_w = np.full(len(METRICS), 1.0 / len(METRICS))
    eq = pk.mean_regret(eq_w)
    print("  S0 单指标    regret = %.6f" % s0)
    print("  7 指标等权   regret = %.6f" % eq)
    print()
    print("  ⚠️ 以上是 in-sample（零参数基线，无拟合，可直接比）")

    # in-sample（含选择偏差，仅作对照）
    print()
    rng = np.random.default_rng(20261105)
    t0 = time.perf_counter()
    w_rs = random_search(pk, args.trials, rng)
    rs_in = pk.mean_regret(w_rs)
    w_cd, cd_evals = coord_descent(pk, rounds=args.cd_rounds, init=w_rs)
    cd_in = pk.mean_regret(w_cd)
    print("  in-sample（含选择偏差，**不作结论**）：")
    print("    随机搜索(=%d trials)  regret = %.6f" % (args.trials, rs_in))
    print("    坐标下降(=%d evals)   regret = %.6f" % (cd_evals, cd_in))
    print("    （in-sample 必然优于 S0，这不是证据）")

    # 多种子 LOO
    print()
    print("-" * 100)
    print("多种子 LOO（%d 个种子，每折独立搜索，全样本 n=%d）"
          % (args.seeds, pk.n))
    print("-" * 100)
    print("  代价：随机搜索 = %d 种子 × %d 折 × %d trials = %d 次权重评估"
          % (args.seeds, pk.n, args.trials,
             args.seeds * pk.n * args.trials))

    t0 = time.perf_counter()
    rs_loos = []
    for s in range(args.seeds):
        v, _ = loo_one_seed(pk, args.trials, args.cd_rounds,
                            seed=1000 + s, method="random",
                            verbose=(pk.n > 200))
        rs_loos.append(v)
        print("    随机搜索 seed=%d  LOO = %.6f  (Δ vs S0 %+.6f)"
              % (1000 + s, v, v - s0), flush=True)
    el_rs = time.perf_counter() - t0

    print()
    t0 = time.perf_counter()
    cd_loos = []
    for s in range(args.seeds):
        v, _ = loo_one_seed(pk, args.trials, args.cd_rounds,
                            seed=2000 + s, method="coord",
                            verbose=(pk.n > 200))
        cd_loos.append(v)
        print("    坐标下降 seed=%d  LOO = %.6f  (Δ vs S0 %+.6f)"
              % (2000 + s, v, v - s0), flush=True)
    el_cd = time.perf_counter() - t0

    # 分布统计
    print()
    print("=" * 100)
    print("LOO 分布（这是唯一可作证据的读数）")
    print("=" * 100)

    def summarize(name, vals):
        vals = sorted(vals)
        med = statistics.median(vals)
        below = sum(1 for v in vals if v < s0)
        print("  %s：" % name)
        print("    中位 %.6f  范围 [%.6f, %.6f]"
              % (med, vals[0], vals[-1]))
        print("    中位 Δ vs S0 = %+.6f" % (med - s0))
        print("    低于 S0 的种子数 = %d/%d (%.0f%%)"
              % (below, len(vals), 100 * below / len(vals)))
        return {"median": med, "min": vals[0], "max": vals[-1],
                "delta_vs_s0": med - s0, "n_below_s0": below,
                "n_seeds": len(vals), "all": vals}

    rs_sum = summarize("随机搜索", rs_loos)
    print()
    cd_sum = summarize("坐标下降", cd_loos)

    # 判据
    print()
    print("=" * 100)
    print("按预注册判据裁定")
    print("=" * 100)
    print("  判据：(a) 中位 LOO < S0  (b) ≥80%% 种子 LOO < S0")
    print()
    verdict = {}
    for name, s in (("随机搜索", rs_sum), ("坐标下降", cd_sum)):
        a = s["median"] < s0
        b = s["n_below_s0"] / s["n_seeds"] >= 0.8
        if a and b:
            v = "有正向信号"
        elif a and not b:
            v = "不稳定（中位低但符号一致性 <80%）"
        else:
            v = "无正向信号"
        verdict[name] = {"criterion_a": bool(a), "criterion_b": bool(b),
                         "verdict": v}
        print("  %s：(a)=%s (b)=%s ⇒ **%s**"
              % (name, "通过" if a else "未过", "通过" if b else "未过", v))

    print()
    print("  ⚠️ 即使有正向信号，边界仍然存在：")
    print("     · 样本 worlds 范围 %d-%d（偏简单）"
          % (min(r["worlds"] for r in rows), max(r["worlds"] for r in rows)))
    print("     · 整局 A/B 未做 ⇒ regret 改善 ≠ 整局胜率提升")
    print("     · 本判据只回答'该权重在样本内的 LOO 上是否优于 S0'")

    out = {
        "matrix": os.path.basename(mp),
        "n_points": pk.n,
        "worlds_range": [min(r["worlds"] for r in rows),
                         max(r["worlds"] for r in rows)],
        "caliber": "v3 initial_state + cpp full enumeration",
        "s0_in_sample": s0, "eq_in_sample": eq,
        "random_search_in_sample": rs_in,
        "coord_descent_in_sample": cd_in,
        "cd_evals": cd_evals,
        "seeds": args.seeds, "trials": args.trials,
        "random_search_loo": rs_sum,
        "coord_descent_loo": cd_sum,
        "verdict": verdict,
        "elapsed_rs_s": el_rs, "elapsed_cd_s": el_cd,
        "caveat": "耗时不可用于策略排序；样本偏简单；整局A/B未做",
        "args": vars(args),
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
