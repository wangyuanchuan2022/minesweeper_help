# -*- coding: utf-8 -*-
"""实测：把 wr_f 的前瞻深度加深，决策质量到底会不会变好？

=================== 为什么必须先测再改 C++ ====================
生产现状（cpp/mscore.cpp:1102）：
    depth_limit = 200.0 / len(clicks)
已经是"粗自适应"——候选越少允许搜越深。用户要求改成"按局面复杂度定深度"。
但**加深是否有益本身没有被测过**。若加深无益，任何复杂度规则都是空转，
写进 C++ 只是增加风险与耗时。

=================== 口径 ====================
样本：bench/data/midgame_cases.json 里世界数可枚举的中局决策点（能算忠实 V*）。
方法：**直接调用生产 C++ 的 mscore.win_rate**（不是 bench 原型），
      只改 depth_limit 公式，看它给出的 argmax 与精确最优的差距。

⚠️ 关键：wr_f 是启发式，不是 V*。所以这里比较的是
   "wr_f 在不同深度下选出的格，其**真实**质量（用 ExactEngine 的 after_click 算）"
   —— 用真值评价启发式，而不是用启发式评价启发式。

=================== 深度策略（待测）====================
  A 现状      : 200 / n_cand
  B 固定浅    : 2
  C 固定中    : 4
  D 固定深    : 8
  E 复杂度自适应: 按 (n_cand, n_worlds) 联合定深度
  F 上限放大  : 400 / n_cand（整体加深一倍）

用法:
  python bench/bench_depth_policy.py --max-worlds 400 --limit 30
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


def noop_progress(_pct):
    pass


def call_wr_f(case, depth_limit, node_cap=2_000_000):
    """直接调生产 C++ win_rate，返回 (res_list, clicks_sorted, total)。

    depth_limit 通过**环境变量**不可传（py_win_rate 内部硬编码 200/len），
    所以这里需要 C++ 侧支持 depth_limit 参数。
    若 .pyd 还不支持，本函数会抛 TypeError ⇒ 由调用方跳过并提示先编译。
    """
    clicks = [tuple(c) for c in case["clicks"]]
    clicks9 = [tuple(c) for c in case["clicks9"]]
    res_list = [np.asarray(g, dtype=np.int32) for g in case["groups"]]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    ck = list(case["ck"])
    try:
        res, cl, total, c2p = native.mscore.win_rate(
            clicks, clicks9, res_list, cv, ck,
            int(case["num10"]), int(case["a"]), int(case["w"]), int(case["h"]),
            False, noop_progress, node_cap, float(depth_limit))
    except TypeError:
        # 旧 .pyd 不支持第 12 个参数
        res, cl, total, c2p = native.mscore.win_rate(
            clicks, clicks9, res_list, cv, ck,
            int(case["num10"]), int(case["a"]), int(case["w"]), int(case["h"]),
            False, noop_progress, node_cap)
    return res, cl, total, c2p


POLICIES = {
    "A_now_200/n":   lambda n_cand, n_worlds, n_clicks: 200.0 / max(1, n_cand),
    "B_fixed_2":     lambda n_cand, n_worlds, n_clicks: 2.0,
    "C_fixed_4":     lambda n_cand, n_worlds, n_clicks: 4.0,
    "D_fixed_8":     lambda n_cand, n_worlds, n_clicks: 8.0,
    "F_400/n":       lambda n_cand, n_worlds, n_clicks: 400.0 / max(1, n_cand),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-worlds", type=int, default=400)
    ap.add_argument("--limit", type=int, default=30,
                    help="最多评估多少个决策点（0=全部）")
    ap.add_argument("--node-cap", type=int, default=2_000_000)
    ap.add_argument("--time-cap", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "depth_policy.json"))
    args = ap.parse_args()

    if not native.available:
        print("✘ native 不可用，无法测生产 C++ 路径")
        return 1

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases if 8.0 < c["limitation"] <= 60.0]

    print("=" * 104)
    print("前瞻深度策略实测（直接调生产 C++ wr_f，用精确 V* 评价其选格）")
    print("=" * 104)
    print(f"候选 {len(sample)} 个决策点，世界数上限 {args.max_worlds:,}")
    print()

    # ---- 先用 ExactEngine 建真值 q ----
    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0, "wr_fail": 0}
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
        v_opt = max(q.values())
        rows.append({
            "case": c, "eng": eng, "q": q, "v_opt": v_opt,
            "cands": cands, "n_worlds": eng.n_worlds,
            "n_cand": len(cands), "case_idx": len(rows),
        })
        print(f"  真值就绪 [{len(rows):>2}] lim={c['limitation']:>6} "
              f"w={nw:>4} cand={len(cands):>2} V*={v_opt:.4f}", flush=True)

    if not rows:
        print("无可评估样本")
        return 1

    # ---- 各策略：调 wr_f，取其 argmax，用真值打分 ----
    print()
    print("=" * 104)
    print("各深度策略的决策质量（regret 用精确 V* 算；越小越好）")
    print("=" * 104)
    print(f"{'策略':<16}{'n':>4}{'平均regret':>13}{'全对':>8}"
          f"{'中位ms':>10}{'p90ms':>10}{'node_cap触发':>13}")
    print("-" * 104)

    results = {}
    detail = {}          # case_idx -> {policy: regret}  用于配对比较
    for pname, pfunc in POLICIES.items():
        regs, times = [], []
        cap_hits = 0
        for row in rows:
            c = row["case"]
            dl = pfunc(row["n_cand"], row["n_worlds"], len(c["clicks"]))
            t0 = time.perf_counter()
            try:
                res, cl_sorted, total, c2p = call_wr_f(c, dl, args.node_cap)
            except Exception as e:                         # noqa: BLE001
                cap_hits += 1
                skipped["wr_fail"] += 1
                if len(regs) + cap_hits <= 3:
                    print(f"    [{pname}] wr_f 失败: {type(e).__name__}: {e}")
                continue
            dt = (time.perf_counter() - t0) * 1000.0
            if not res:
                cap_hits += 1
                continue
            # wr_f 返回的 clicks 已按 safety 降序；argmax(res)
            best_i = int(np.argmax(res))
            picked = tuple(cl_sorted[best_i])
            # 映射回 ExactEngine 的位索引
            w2 = row["eng"].W2
            idx = picked[1] * w2 + picked[0]
            if idx not in row["q"]:
                # wr_f 选的格不在精确候选集里（不应发生；记录以便排查）
                skipped["err"] += 1
                continue
            reg = row["v_opt"] - row["q"][idx]
            regs.append(reg)
            times.append(dt)
            detail.setdefault(row["case_idx"], {})[pname] = reg
        if not regs:
            print(f"{pname:<16}{0:>4}{'—':>13}{'—':>8}{'—':>10}{'—':>10}"
                  f"{cap_hits:>13}")
            continue
        ts = sorted(times)
        results[pname] = {
            "n": len(regs), "mean_regret": sum(regs) / len(regs),
            "opt": sum(1 for x in regs if x <= 1e-12),
            "median_ms": ts[len(ts) // 2],
            "p90_ms": ts[min(len(ts) - 1, int(len(ts) * 0.9))],
            "cap_hits": cap_hits,
        }
        r = results[pname]
        opt_txt = "%d/%d" % (r["opt"], r["n"])
        print(f"{pname:<16}{r['n']:>4}{r['mean_regret']:>13.4f}"
              f"{opt_txt:>8}{r['median_ms']:>10.2f}"
              f"{r['p90_ms']:>10.2f}{cap_hits:>13}")

    # ============================================================
    # ⚠️ 关键：上面各策略的 n 不同（node_cap 触发次数不同）⇒
    #    均值**不可直接相减**（自选择分组，违反"对照组必须真对照"）。
    #    必须只在**所有策略都成功的共同点集**上做配对比较。
    # ============================================================
    print()
    print("=" * 104)
    print("配对比较（只在「所有策略都成功」的共同点集上）")
    print("=" * 104)
    common = [ci for ci, d in detail.items()
              if len(d) == len([p for p in POLICIES if p in results])]
    print(f"  共同点集大小 = {len(common)}（各策略单独覆盖 "
          f"{ {k: v['n'] for k, v in results.items()} }）")
    paired = {}
    if len(common) >= 5:
        names = [p for p in POLICIES if p in results]
        print()
        print(f"{'策略':<16}{'配对均值':>11}{'vs 现状 Δ':>13}{'更好':>7}{'更差':>7}"
              f"{'符号检验 p':>12}")
        print("-" * 104)
        base_name = "A_now_200/n"
        base_vals = [detail[ci][base_name] for ci in common] if base_name in results else None
        for pname in names:
            vals = [detail[ci][pname] for ci in common]
            m = sum(vals) / len(vals)
            paired[pname] = {"n": len(vals), "mean_regret": m}
            if base_vals is None:
                print(f"{pname:<16}{m:>11.4f}{'—':>13}{'—':>7}{'—':>7}{'—':>12}")
                continue
            d = [a - b for a, b in zip(vals, base_vals)]
            wn = sum(1 for x in d if x < -1e-12)
            ls = sum(1 for x in d if x > 1e-12)
            nz = wn + ls
            if nz == 0:
                p = 1.0
            else:
                k = min(wn, ls)
                p = min(1.0, 2.0 * sum(math.comb(nz, i) * (0.5 ** nz)
                                       for i in range(k + 1)))
            print(f"{pname:<16}{m:>11.4f}{sum(d)/len(d):>+13.4f}"
                  f"{wn:>7}{ls:>7}{p:>12.3f}"
                  f"{'  显著' if p < 0.05 and sum(d) < 0 else ''}")
    else:
        print("  ✘ 共同点集太小（<5），无法做配对比较 ⇒ 本轮深度策略结论**不可用**。")
        print("     这是 node_cap 造成的样本分裂，不是策略差异。")

    print()
    print(f"跳过统计：{skipped}")
    print()
    print("=" * 104)
    print("判定（基于配对比较，不是各策略独立均值）")
    print("=" * 104)
    base_name = "A_now_200/n"
    if base_name in paired:
        base = paired[base_name]["mean_regret"]
        print(f"  现状（200/n）配对均值 = {base:.4f}（共同点集 n={paired[base_name]['n']}）")
        better = [(k, v) for k, v in paired.items()
                  if k != base_name and v["mean_regret"] < base - 1e-4]
        if better:
            better.sort(key=lambda kv: kv[1]["mean_regret"])
            for k, v in better:
                print(f"  ✔ {k}: {v['mean_regret']:.4f} "
                      f"(Δ{v['mean_regret']-base:+.4f}, "
                      f"中位 {results[k]['median_ms']:.1f}ms)")
            print("  ⇒ 存在配对更优的深度策略；但需看符号检验 p 值是否显著。")
        else:
            print("  ✘ 在配对口径下没有任何深度策略优于现状（200/n）。")
            print("  ⇒ 加深前瞻在本样本上**无收益**；不应把复杂度规则写进 C++。")
    else:
        print("  现状策略未成功，无法比较。")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"results": results, "paired": paired,
                   "n_common": len(common), "detail": detail,
                   "skipped": skipped, "n_points": len(rows),
                   "args": vars(args)},
                  f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
