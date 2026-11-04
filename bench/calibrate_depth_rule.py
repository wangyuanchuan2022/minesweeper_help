# -*- coding: utf-8 -*-
"""标定：按局面复杂度（n_cand × n_worlds）定前瞻深度的规则。

=================== 为什么要标定而不是拍脑袋 ====================
用户要求"前瞻步数按局面复杂度而定，写入 C++"。
上一轮实测已确认三件事（bench/bench_depth_policy.py，19 点配对集）：
  1. 固定深度 2/4/8 层**显著劣于**现状 `200/len(clicks)`
     （Δ+0.0169 / +0.0157 / +0.0085，p=0.008 / 0.016 / 0.250）
     ⇒ 「加深」不是方向。
  2. `200/n` 已经是粗自适应（候选越少搜越深）。
  3. 真正的问题是 **node_cap 触发**：22 点里 3 次超限 ⇒ 整步降级为贪心
     （质量从 0.0065 掉到 0.0157 量级），p90 耗时 18.2s（预算 30s）。
     ⇒ 正确方向是**在复杂局面主动变浅以避免降级**，不是加深。

=================== 本脚本做什么 ====================
对每个决策点扫描一组深度上限，记录 (regret, ms, 是否 node_cap)，
然后拟合一条"给定复杂度 → 最优深度"的规则，并机检该规则
是否优于现状 200/n（同点配对）。

复杂度代理：cost ≈ n_cand × total（根节点 cp 计算 + 每层世界分裂的成本）。
这条代理可从 wr_f 的结构直接读出（见 cpp/mscore.cpp:1118-1126 的双重循环）。

用法:
  python bench/calibrate_depth_rule.py --max-worlds 400 --limit 24
"""
import argparse
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np                                        # noqa: E402
from arms_lookahead import ExactEngine, Budget, WKey      # noqa: E402
import utils.native as native                             # noqa: E402


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


def noop(_p):
    pass


def call_wr(case, depth, node_cap):
    clicks = [tuple(c) for c in case["clicks"]]
    clicks9 = [tuple(c) for c in case["clicks9"]]
    groups = [np.asarray(g, dtype=np.int32) for g in case["groups"]]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    return native.mscore.win_rate(
        clicks, clicks9, groups, cv, list(case["ck"]),
        int(case["num10"]), int(case["a"]), int(case["w"]), int(case["h"]),
        False, noop, node_cap, float(depth))


# 扫描的深度上限（None = 现状 200/n）
DEPTHS = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 40.0, 999.0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-worlds", type=int, default=400)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--node-cap", type=int, default=20_000_000)
    ap.add_argument("--time-cap", type=float, default=20.0)
    ap.add_argument("--time-budget-ms", type=float, default=5000.0,
                    help="单决策允许的前瞻耗时（生产 DECISION_TIME_BUDGET=30s，"
                         "但 win_rate 只占其中一部分）")
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "depth_rule_calib.json"))
    args = ap.parse_args()

    if not native.available:
        print("✘ native 不可用")
        return 1

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases if 8.0 < c["limitation"] <= 60.0]

    print("=" * 100)
    print("前瞻深度规则标定：逐点扫描深度，找「不降级且质量最优」的规则")
    print("=" * 100)
    print(f"单决策前瞻耗时上限 {args.time_budget_ms:.0f}ms，node_cap={args.node_cap:,}")
    print()

    # ---- 建真值 ----
    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0}
    for c in sample:
        if args.limit and len(rows) >= args.limit:
            break
        nw = count_worlds(c)
        if nw > args.max_worlds:
            skipped["world_cap"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=3_000_000, time_cap=args.time_cap,
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
        rows.append({
            "case": c, "eng": eng, "q": q, "v_opt": max(q.values()),
            "n_worlds": eng.n_worlds, "n_cand": len(cands),
            "n_clicks": len(c["clicks"]), "idx": len(rows),
        })
        print(f"  真值 [{len(rows):>2}] lim={c['limitation']:>6} "
              f"w={nw:>4} cand={len(cands):>2}", flush=True)

    if not rows:
        print("无样本")
        return 1

    # ---- 逐点扫描深度 ----
    print()
    print("=" * 100)
    print("逐点深度扫描（. = 未触发 cap，X = node_cap 触发降级）")
    print("=" * 100)
    hdr = "".join(f"{d:>7.0f}" for d in DEPTHS)
    print(f"{'#':>3}{'cand':>5}{'worlds':>8}{'200/n':>7} |{hdr}")
    print("-" * 100)
    scan = []
    for row in rows:
        c = row["case"]
        now_dl = 200.0 / max(1, row["n_cand"])
        rec = {"idx": row["idx"], "n_cand": row["n_cand"],
               "n_worlds": row["n_worlds"], "now_dl": now_dl, "points": {}}
        cells = []
        for d in DEPTHS:
            t0 = time.perf_counter()
            try:
                res, cl, total, _c2p = call_wr(c, d, args.node_cap)
                dt = (time.perf_counter() - t0) * 1000.0
                if not res:
                    rec["points"][d] = {"cap": True}
                    cells.append("      X")
                    continue
                bi = int(np.argmax(res))
                picked = tuple(cl[bi])
                idx = picked[1] * row["eng"].W2 + picked[0]
                if idx not in row["q"]:
                    rec["points"][d] = {"cap": False, "oob": True}
                    cells.append("      ?")
                    continue
                reg = row["v_opt"] - row["q"][idx]
                rec["points"][d] = {"cap": False, "regret": reg, "ms": dt}
                cells.append(f"{reg:>7.4f}")
            except Exception:                              # noqa: BLE001
                rec["points"][d] = {"cap": True}
                cells.append("      X")
        scan.append(rec)
        print(f"{row['idx']:>3}{row['n_cand']:>5}{row['n_worlds']:>8}"
              f"{now_dl:>7.1f} |{''.join(cells)}", flush=True)

    # ---- 找每个点的最优可行深度 ----
    print()
    print("=" * 100)
    print("每点的最优可行深度（在耗时预算内、不触发 cap、regret 最小）")
    print("=" * 100)
    print(f"{'#':>3}{'cand':>5}{'worlds':>8}{'cost':>12}"
          f"{'现状dl':>8}{'现状reg':>9}{'最优dl':>8}{'最优reg':>9}{'增益':>8}")
    print("-" * 100)
    calib = []
    for rec, row in zip(scan, rows):
        cost = rec["n_cand"] * rec["n_worlds"]
        pts = rec["points"]
        # 现状
        now_pt = pts.get(rec["now_dl"])
        if now_pt is None:
            # 现状 dl 不在扫描点里，取最接近的
            nd = min(DEPTHS, key=lambda d: abs(d - rec["now_dl"]))
            now_pt = pts[nd]
        now_reg = now_pt.get("regret")
        # 最优可行
        feas = [(d, p) for d, p in pts.items()
                if not p.get("cap") and p.get("regret") is not None
                and p.get("ms", 1e18) <= args.time_budget_ms]
        if not feas:
            continue
        best_d, best_p = min(feas, key=lambda kv: (kv[1]["regret"], kv[0]))
        rec["cost"] = cost
        rec["best_dl"] = best_d
        rec["best_reg"] = best_p["regret"]
        rec["now_reg"] = now_reg
        calib.append(rec)
        gain = (now_reg - best_p["regret"]) if now_reg is not None else None
        print(f"{rec['idx']:>3}{rec['n_cand']:>5}{rec['n_worlds']:>8}"
              f"{cost:>12,}{rec['now_dl']:>8.1f}"
              f"{(f'{now_reg:.4f}' if now_reg is not None else '  CAP'):>9}"
              f"{best_d:>8.0f}{best_p['regret']:>9.4f}"
              f"{(f'{gain:+.4f}' if gain is not None else '     —'):>8}")

    # ---- 拟合规则 ----
    print()
    print("=" * 100)
    print("拟合「复杂度 → 深度」规则")
    print("=" * 100)
    print("  候选规则形式：depth = K / (n_cand^alpha * log10(total)^beta)")
    print("  用网格搜索 K/alpha/beta，目标 = 该规则下总 regret 最小（含 cap 惩罚）")
    print()

    CAP_PENALTY = 0.0157      # cap 触发 ⇒ 降级为贪心，用贪心 regret 作惩罚
    best_rule = None
    for K in (100, 150, 200, 300, 400, 600, 800):
        for alpha in (0.5, 0.75, 1.0, 1.25):
            for beta in (0.0, 0.5, 1.0):
                tot = 0.0
                n_used = 0
                for rec in calib:
                    denom = (rec["n_cand"] ** alpha) * \
                            (max(1.0, math.log10(max(2, rec["n_worlds"]))) ** beta)
                    dl = K / max(1e-9, denom)
                    dl = max(1.0, min(999.0, dl))
                    # 找最接近的扫描点
                    d = min(DEPTHS, key=lambda x: abs(x - dl))
                    p = rec["points"][d]
                    if p.get("cap") or p.get("regret") is None:
                        tot += CAP_PENALTY
                    else:
                        tot += p["regret"]
                    n_used += 1
                if n_used == 0:
                    continue
                avg = tot / n_used
                if best_rule is None or avg < best_rule[0]:
                    best_rule = (avg, K, alpha, beta)
    if best_rule is None:
        print("  拟合失败")
        return 1
    avg, K, alpha, beta = best_rule
    print(f"  最佳规则: depth = {K} / (n_cand^{alpha} * log10(total)^{beta})")
    print(f"  该规则下平均 regret（含 cap 惩罚）= {avg:.4f}")

    # 对照：现状 200/n
    tot_now = 0.0
    for rec in calib:
        now_pt = rec["points"].get(rec["now_dl"])
        if now_pt is None:
            nd = min(DEPTHS, key=lambda d: abs(d - rec["now_dl"]))
            now_pt = rec["points"][nd]
        if now_pt.get("cap") or now_pt.get("regret") is None:
            tot_now += CAP_PENALTY
        else:
            tot_now += now_pt["regret"]
    avg_now = tot_now / len(calib)
    print(f"  现状 200/n 同口径平均 regret = {avg_now:.4f}")
    print(f"  Δ = {avg - avg_now:+.4f}（负 = 新规则更好）")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rule": {"K": K, "alpha": alpha, "beta": beta,
                            "avg_regret": avg},
                   "now_avg_regret": avg_now,
                   "calib": [{k: v for k, v in r.items()
                              if k in ("idx", "n_cand", "n_worlds", "now_dl",
                                       "best_dl", "best_reg", "now_reg", "cost")}
                             for r in calib],
                   "scan": scan, "args": vars(args)},
                  f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
