# -*- coding: utf-8 -*-
"""中局「加前瞻」到底有没有优势：只在**世界数可枚举**的中局段上算忠实 V*。

=================== 为什么必须按世界数筛（而不是按 limitation 筛）====================
实测（bench/data/midgame_cases.json，40 个 limitation∈(8,20] 的决策点）：
  世界数从 11 到 **545,870**，跨度 5 个数量级。
  世界数 54.6 万的局面，V* 递归 × 每个候选一次 —— 纯 Python 无法在分钟级完成
  （实测跑 20 分钟零进展，已中止）。因此**必须按世界数分层**，并且
  **逐层报告覆盖了多少样本**，绝不拿"能算的那部分"冒充全体。

=================== 分层口径（关键）====================
  · `--max-worlds` 内的局面：算忠实 V*，各臂 regret 可直接比较（强证据）
  · 超出的局面：**只报结构量**（平局集大小、安全率分布），**不给 regret 结论**
    ——避免"按是否易算筛选样本"这一已记录的错误（见 memory: 对照组必须是真对照）

=================== 各臂（同一决策点、同一后验）====================
  prod        —— 生产实际选的格（快照记录的 picked，含其随机破平局结果）
  safety      —— 最大安全率，平局取字典序最小（确定性基线）
  open5x5     —— 生产的破平局规则（`open_num5x5`；把棋盘外计为已开）
  infogain    —— 1 步信息增益：safety × E[点开后新开格数]（无递归）
  ply2 / ply3 —— k 步前瞻（每层续玩准则用 safety）

用法:
  python bench/analyze_midgame_lookahead.py --max-worlds 60000
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402


def count_worlds(case):
    """纯算术数世界数（零内存），用于分层筛选。"""
    import itertools
    a = case["a"]
    num10 = case["num10"]
    U = case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = 0
        for gi, sel in enumerate(idx):
            mt += sum(int(v) for v in case["groups"][gi][sel])
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
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=20.0)
    ap.add_argument("--max-worlds", type=int, default=60000)
    ap.add_argument("--node-cap", type=int, default=2_000_000)
    ap.add_argument("--time-cap", type=float, default=120.0)
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]
    print("=" * 100)
    print(f"中局前瞻的价值（忠实 V* 口径）：limitation ∈ "
          f"({args.min_lim}, {args.max_lim}]")
    print(f"样本 {len(sample)}/{len(cases)} 个决策点；按世界数 ≤ "
          f"{args.max_worlds:,} 分层")
    print("=" * 100, flush=True)

    ARMS = ["prod", "safety", "expand", "ply2", "ply3"]
    res = {a: [] for a in ARMS}
    rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0, "no_world": 0}
    struct_only = []

    for k, c in enumerate(sample):
        nw = count_worlds(c)
        if nw > args.max_worlds:
            skipped["world_cap"] += 1
            struct_only.append({"limitation": c["limitation"], "worlds": nw,
                                "F": c["F"], "U": c["U"]})
            continue
        print(f"  [{k + 1:>2}/{len(sample)}] lim={c['limitation']:>6} "
              f"F={c['F']:>2} U={c['U']:>2} worlds={nw:>8,} …", flush=True)
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                              world_cap=None)
        except Exception as e:                              # noqa: BLE001
            skipped["err"] += 1
            print(f"      构造失败 {type(e).__name__}: {str(e)[:60]}", flush=True)
            continue
        if eng.n_worlds == 0:
            skipped["no_world"] += 1
            continue
        wk = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            skipped["no_world"] += 1
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)

        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
            v_opt = max(q.values())
            picks = {
                "safety": eng.pick(cands, wk2, op2, "safety", 0),
                "expand": eng.pick(cands, wk2, op2, "expand", 0),
                "ply2": eng.pick(cands, wk2, op2, "ply", 2),
                "ply3": eng.pick(cands, wk2, op2, "ply", 3),
            }
        except Budget as e:
            skipped["budget"] += 1
            print(f"      ⚠ 预算淘汰（{e}）——整点不计入统计", flush=True)
            continue
        pc = c.get("picked")
        if pc:
            pi = eng._idx_of((int(pc[0]), int(pc[1])))
            picks["prod"] = pi if pi in cands else None

        saf = {cd: eng.safety(cd, wk2) for cd in cands}
        smax = max(saf.values())
        tie = {cd for cd in cands if saf[cd] >= smax - 1e-12}

        row = {"limitation": c["limitation"], "F": c["F"], "U": c["U"],
               "worlds": eng.n_worlds, "n_cand": len(cands), "n_tie": len(tie),
               "v_opt": round(v_opt, 6),
               "nodes": eng.nodes}
        for a in ARMS:
            cd = picks.get(a)
            if cd is None or cd not in cands:
                row[a] = {"present": False}
                continue
            reg = v_opt - q[cd]
            row[a] = {"present": True, "regret": round(reg, 6),
                      "quality": round(q[cd], 6),
                      "in_tie": bool(cd in tie),
                      "safety": round(saf[cd], 6)}
            res[a].append(reg)
        rows.append(row)
        print(f"      V*={v_opt:.4f} 候选={len(cands)} 平局={len(tie)} "
              f"节点={eng.nodes:,}  "
              + "  ".join(f"{a}={row[a].get('regret', '-')}" for a in ARMS),
              flush=True)

    print("\n" + "=" * 100)
    print(f"可完整覆盖 {len(rows)}/{len(sample)} 个决策点"
          f"（世界数超限 {skipped['world_cap']}、预算淘汰 {skipped['budget']}、"
          f"无世界 {skipped['no_world']}、错误 {skipped['err']}）")
    print("=" * 100)
    if not rows:
        print("无覆盖样本，不出 regret 结论")
        return

    print(f"\n{'臂':<12} {'n':>4} {'全对':>8} {'平均regret':>12} {'最大':>10} "
          f"{'总损失':>10} {'选在平局集内':>12}")
    print("-" * 100)
    for a in ARMS:
        v = res[a]
        if not v:
            print(f"{a:<12} 无数据")
            continue
        ok = sum(1 for x in v if x <= 1e-12)
        intra = sum(1 for r in rows
                    if isinstance(r.get(a), dict) and r[a].get("in_tie"))
        print(f"{a:<12} {len(v):>4} {f'{ok}/{len(v)}':>8} "
              f"{sum(v) / len(v):>12.4f} {max(v):>10.4f} {sum(v):>10.4f} "
              f"{f'{intra}/{len(v)}':>12}")

    print("\n" + "=" * 100)
    print("配对比较（同一决策点；负 = A 更好）")
    print("=" * 100)
    pairs = [("expand", "safety"), ("ply2", "safety"),
             ("ply3", "safety"), ("ply3", "ply2"),
             ("prod", "safety"), ("prod", "expand"), ("safety", "expand")]
    print(f"{'A vs B':<22} {'A更好':>7} {'B更好':>7} {'n':>5} {'p':>8} {'平均Δ':>10}")
    print("-" * 100)
    for a, b in pairs:
        d = []
        for r in rows:
            ea, eb = r.get(a), r.get(b)
            if not isinstance(ea, dict) or not isinstance(eb, dict):
                continue
            if not ea.get("present") or not eb.get("present"):
                continue
            d.append(ea["regret"] - eb["regret"])
        w = sum(1 for x in d if x < -1e-12)
        l = sum(1 for x in d if x > 1e-12)
        p = sign_test(w, l)
        md = sum(d) / len(d) if d else 0.0
        print(f"{a + ' vs ' + b:<22} {w:>7} {l:>7} {len(d):>5} {p:>8.3f} "
              f"{md:>10.4f}  {'显著' if p < 0.05 else ''}")

    print("\n" + "=" * 100)
    print("分层：平局集大小")
    print("=" * 100)
    for lab, lo_t, hi_t in (("无平局(n_tie=1)", 1, 1), ("小平局(2-5)", 2, 5),
                            ("大平局(>5)", 6, 10 ** 9)):
        sub = [r for r in rows if lo_t <= r["n_tie"] <= hi_t]
        if not sub:
            continue
        print(f"\n[{lab}] {len(sub)} 个决策点")
        for a in ("safety", "expand", "prod"):
            v = [r[a]["regret"] for r in sub
                 if isinstance(r.get(a), dict) and r[a].get("present")]
            if not v:
                continue
            print(f"   {a:<10} n={len(v):>3} 平均regret {sum(v) / len(v):.4f} "
                  f"最大 {max(v):.4f}")

    # ---- 未覆盖局面：只报结构，不给 regret 结论 ----
    if struct_only:
        import statistics
        ws = sorted(x["worlds"] for x in struct_only)
        print("\n" + "=" * 100)
        print(f"**未覆盖**的中局局面（世界数 > {args.max_worlds:,}）："
              f"{len(struct_only)} 个")
        print("=" * 100)
        print(f"  世界数：中位 {statistics.median(ws):,.0f}  最大 {ws[-1]:,}")
        print("  ⇒ 这些局面**没有** regret 结论。它们的结构量见 "
              "bench/analyze_midgame_greedy.py（平局集/破平局规则分析，"
              "不需要精确 V*）。")
        print("  ⇒ 不许把上面的 regret 结论外推到这批局面。")

    out = os.path.join(ROOT, "state", "ab", "midgame_lookahead.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"max_worlds": args.max_worlds, "rows": rows,
                   "struct_only": struct_only, "skipped": skipped}, f,
                  ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
