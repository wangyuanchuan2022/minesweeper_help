# -*- coding: utf-8 -*-
"""桥梁实验：**采样近似**能否复现精确 after_click 的排序？

=================== 为什么这是最关键的一块 ====================
实测硬事实（无法绕过）：
  · 真实对局 81.6% 的中局决策 limitation > 60（1215/1488）
  · 这些决策的世界数是 1e24 量级
  ⇒ **精确前瞻在这些局面里物理上不可算**，不是"慢"，是"不可能"。
所以"中局加前瞻"若要有意义，必须是**采样近似**：只采样有限个世界，
在每个世界下做真实 flood-fill，估每格的期望新开格数 / 续玩价值。

本脚本要判定的就是：**采样近似够不够准**？
口径（干净、可证伪）：
  1. 取世界数可枚举的中局决策点（能算忠实 after_click = 真值）
  2. 只从这些点里抽 K 个世界（模拟"中局只能采样"的情形）
  3. 用采样世界算各格打分，看它选出的格 与 精确最优格 的差距
  4. 关键指标：采样近似的 regret 相对**纯 safety** 的 regret 是变大还是变小

=================== 本脚本明确不做什么 ====================
  · 不宣称胜率提升（regret 是单步决策质量，不是整局胜率）
  · 不把结果外推到世界数 1e24 的超大局（那里连真值都没有）

用法:
  python bench/verify_sampling_lookahead.py --max-worlds 900 --k 64
"""
import argparse
import json
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = sum(sum(int(v) for v in case["groups"][gi][sel])
                 for gi, sel in enumerate(idx))
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def sign_test(wins, losses):
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=60.0)
    ap.add_argument("--max-worlds", type=int, default=900)
    ap.add_argument("--node-cap", type=int, default=1_200_000)
    ap.add_argument("--time-cap", type=float, default=25.0)
    ap.add_argument("--k", type=int, default=64,
                    help="采样世界数（模拟中局只能采样的情形）")
    ap.add_argument("--reps", type=int, default=5, help="采样重复次数（降采样噪声）")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]
    print("=" * 100)
    print(f"采样近似前瞻的保真度：limitation ∈ ({args.min_lim}, {args.max_lim}]")
    print(f"世界数 ≤ {args.max_worlds:,}；采样 K={args.k}，重复 {args.reps} 次")
    print("=" * 100, flush=True)

    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0}

    for c in sample:
        nw = count_worlds(c)
        if nw > args.max_worlds:
            skipped["world_cap"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                              world_cap=None)
        except Exception:                                  # noqa: BLE001
            skipped["err"] += 1
            continue
        if eng.n_worlds == 0:
            skipped["err"] += 1
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk_all, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            skipped["err"] += 1
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            skipped["budget"] += 1
            continue
        v_opt = max(q.values())
        saf = {cd: eng.safety(cd, wk2) for cd in cands}

        # ---- 采样近似：K 个世界下的"期望新开格数" ----
        n_all = eng.n_worlds
        rng = random.Random(1000 + len(rows))
        samp_idx = list(range(n_all)) if n_all <= args.k else \
            rng.sample(range(n_all), args.k)
        samp_wk = WKey.from_indices(samp_idx, n_all)

        def sample_expand(cd):
            """在采样世界下估：点开 cd 期望新开多少格（真实 flood）。"""
            tot = 0
            cnt = 0
            for i in samp_idx:
                wb = eng.world_bits[i]
                if wb >> cd & 1:
                    continue                       # 该世界下是雷，直接输
                tot += eng.pop(eng.flood(wb, op2 | (1 << cd), cd) & ~op2)
                cnt += 1
            n_safe_s = cnt
            p_safe_s = n_safe_s / max(1, len(samp_idx))
            mean_new = tot / max(1, n_safe_s)
            return p_safe_s, mean_new

        picks = {}
        picks["safety"] = max(cands, key=lambda cd: (saf[cd], -cd))
        # 采样近似：safety × E[新开格数]（重复取均值降低采样噪声）
        scores = {}
        for cd in cands:
            acc = 0.0
            for _r in range(args.reps):
                p_s, mn = sample_expand(cd)
                acc += p_s * mn
            scores[cd] = acc / args.reps
        picks["sampled_infogain"] = max(cands, key=lambda cd: (scores[cd], -cd))
        # 采样 safety 估计（用采样世界代替全枚举，检验采样是否够准）
        cs = {}
        for cd in cands:
            bad = sum(1 for i in samp_idx if eng.world_bits[i] >> cd & 1)
            cs[cd] = 1.0 - bad / max(1, len(samp_idx))
        picks["sampled_safety"] = max(cands, key=lambda cd: (cs[cd], -cd))

        row = {"limitation": c["limitation"], "worlds": nw,
               "sampled_worlds": len(samp_idx),
               "F": c["F"], "U": c["U"], "n_cand": len(cands),
               "v_opt": round(v_opt, 6)}
        for a, cd in picks.items():
            reg = v_opt - q[cd]
            row[a] = {"regret": round(reg, 6), "is_opt": bool(reg <= 1e-12)}
        # 采样 safety 与真实 safety 的最大偏差
        row["safety_err_max"] = round(max(abs(cs[cd] - saf[cd]) for cd in cands), 6)
        rows.append(row)
        print(f"  [{len(rows):>2}] lim={c['limitation']:>7} worlds={nw:>6,} "
              f"k={len(samp_idx):>4} V*={v_opt:.4f} | "
              f"safety={row['safety']['regret']:.4f}  "
              f"sampled_safety={row['sampled_safety']['regret']:.4f}  "
              f"sampled_infogain={row['sampled_infogain']['regret']:.4f}  "
              f"(safety 采样误差 max {row['safety_err_max']:.4f})", flush=True)

    print("\n" + "=" * 100)
    print(f"可覆盖 {len(rows)}/{len(sample)}（{skipped}）")
    print("=" * 100)
    if not rows:
        return

    ARMS = ("safety", "sampled_safety", "sampled_infogain")
    print(f"\n{'臂':<22} {'全对':>9} {'平均regret':>12} {'最大':>10} {'总损失':>10}")
    print("-" * 100)
    for a in ARMS:
        v = [r[a]["regret"] for r in rows]
        ok = sum(1 for x in v if x <= 1e-12)
        print(f"{a:<22} {f'{ok}/{len(v)}':>9} {sum(v)/len(v):>12.4f} "
              f"{max(v):>10.4f} {sum(v):>10.4f}")

    print("\n" + "=" * 100)
    print("配对检验（同一决策点；负 = A 更好）")
    print("=" * 100)
    for a, b in [("sampled_infogain", "safety"),
                 ("sampled_safety", "safety"),
                 ("sampled_infogain", "sampled_safety")]:
        d = [r[a]["regret"] - r[b]["regret"] for r in rows]
        w = sum(1 for x in d if x < -1e-12)
        l = sum(1 for x in d if x > 1e-12)
        print(f"{a + ' vs ' + b:<36} A更好 {w:>3}  B更好 {l:>3}  "
              f"n={len(d):>3}  p={sign_test(w, l):.3f}  "
              f"平均Δ {sum(d)/len(d):+.4f}")

    se = [r["safety_err_max"] for r in rows]
    se.sort()
    print(f"\n采样 safety 与精确 safety 的最大偏差："
          f"中位 {se[len(se)//2]:.4f}  p90 {se[int(0.9*len(se))]:.4f} "
          f"max {se[-1]:.4f}")
    print("  ⇒ 该值反映 K 个世界估概率的噪声下限。若它比候选间距还大，")
    print("    采样近似就不可能复现精确排序。")

    out = os.path.join(ROOT, "state", "ab", "sampling_lookahead.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"k": args.k, "reps": args.reps, "rows": rows,
                   "skipped": skipped}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
