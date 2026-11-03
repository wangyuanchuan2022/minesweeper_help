# -*- coding: utf-8 -*-
"""cap 扫描：各 cap 下的**样本内**胜率与资源成本（非理论胜率极限）。

=================== 命名与口径（按评审要求）====================
本脚本测的是「各 cap 下、在给定种子样本上的胜率与成本」，
**不是**"理论胜率极限"，也不是"最优 cap"。报告中一律称
「样本内胜率」并标注完成局数。

=================== 两个 cap 分开扫描（作用层级不同）====================
  · world_cap  —— **bench 前瞻臂**（`ab_policy` → `ExactEngine`）的世界枚举上限。
                  超限 → 该决策回退生产策略（贪心）。只影响 bench 评测，
                  **不影响生产**。
  · node_cap   —— 生产的 `wr_f` 递归节点上限（`cpp/mscore.cpp`）。
                  超限 → 生产 `number5_1` 降级走 `process_bigger_situation`。
                  这是**生产参数**。
两者层级不同，必须分别扫描、分别解读，不能混成一个"cap"。

=================== 公平性（按评审要求）====================
  · 相同种子、相同平局规则、**相同每臂时限**
  · 报告：完成局数、配对胜率差及区间、降级决策占比、耗时、峰值 RSS
  · **超时/未完成局单独列出，不剔除、不当作失败样本**
  · 逐局落盘（jsonl）、flush 进度、退出码记录、支持 --resume

=================== 用法 ====================
  # 扫 bench 的 world_cap（node_cap 固定默认）
  python bench/sweep_cap.py --axis world --values 20000 200000 2000000 \\
      --games 60 --arm safety

  # 扫生产的 node_cap（world_cap 固定默认）
  python bench/sweep_cap.py --axis node --values 100000 2000000 20000000 \\
      --games 60 --arm baseline
"""
import argparse
import gc
import json
import math
import os
import statistics
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ⚠️ 必须在 install_stubs 之前保存原始 sleep（否则采样线程忙等抢 GIL，
#    实测假慢 197x，见 review/算法性能评估与优化方案.md）
import time as _time_mod                      # noqa: E402
_REAL_SLEEP = _time_mod.sleep

import psutil                                 # noqa: E402

P = psutil.Process()


class Peak(threading.Thread):
    def __init__(self, interval=0.02):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0
        self.run_ = True

    def run(self):
        self.peak = P.memory_info().rss
        while self.run_:
            try:
                r = P.memory_info().rss
                if r > self.peak:
                    self.peak = r
            except Exception:                      # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)

    def stop(self):
        self.run_ = False
        self.join(timeout=2)
        return self.peak


def pctl(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


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


def run_one_value(axis, val, args, jsonl_path):
    """在某个 cap 值下跑一批局，逐局落盘。"""
    import ab_policy as ab

    world_cap = val if axis == "world" else args.world_cap
    node_cap = val if axis == "node" else args.node_cap

    rows = []
    exit_info = {"exit": "completed", "detail": ""}
    done = set()
    if args.resume and os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    done.add(d["seed"])
                    rows.append(d)
                except Exception:                  # noqa: BLE001
                    pass

    t_start = time.perf_counter()
    for i, sd in enumerate(args._seeds):
        if sd in done:
            continue
        # 每臂统一时限（按评审要求）
        if args.time_budget_s > 0:
            el = time.perf_counter() - t_start
            if el > args.time_budget_s:
                exit_info = {
                    "exit": "time_budget",
                    "detail": f"达 {args.time_budget_s:.0f}s 预算，"
                              f"完成 {len(rows)}/{len(args._seeds)} 局"}
                print(f"    [time-budget] 停止，完成 {len(rows)} 局", flush=True)
                break
        ab.reset_gate_stats()
        pk = Peak()
        pk.start()
        t0 = time.perf_counter()
        try:
            r = ab.play_one(sd, 30, 16, 99, args.arm, 80, world_cap, node_cap)
            err = r.get("error")
        except Exception as e:                     # noqa: BLE001
            r = {}
            err = f"{type(e).__name__}: {e}"
            exit_info = {"exit": "exception", "detail": err}
        dt = (time.perf_counter() - t0) * 1000.0
        peak = pk.stop()
        gc.collect()
        gs = ab.gate_stats()
        deg = gs["world_cap"] + gs["node_budget"]
        calls = r.get("arm_calls", 0)

        row = {
            "seed": sd, "axis": axis, "cap": val,
            "won": r.get("won"), "died": r.get("died"),
            "steps": r.get("steps"), "opened": r.get("opened"),
            "ms": dt, "peak_rss_mb": peak / 2 ** 20,
            "gate": dict(gs), "degrade_decisions": deg,
            "arm_calls": calls,
            "degrade_ratio": (deg / calls) if calls else 0.0,
            "error": err,
        }
        rows.append(row)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"    [{i + 1}/{len(args._seeds)}] seed={sd} {dt:>8.0f}ms "
              f"peak={peak / 2 ** 20:>6.1f}MB deg={deg:>3} "
              f"{'win' if r.get('won') else ('dead' if r.get('died') else '?')}"
              f"{' ERR' if err else ''}", flush=True)
    return rows, exit_info


def summarize(rows):
    ok = [r for r in rows if not r["error"]]
    errs = [r for r in rows if r["error"]]
    if not ok:
        return None
    ms = [r["ms"] for r in ok]
    calls = sum(r["arm_calls"] for r in ok)
    deg = sum(r["degrade_decisions"] for r in ok)
    return {
        "n_ok": len(ok), "n_err": len(errs),
        "wins": sum(1 for r in ok if r["won"]),
        "win_rate": sum(1 for r in ok if r["won"]) / len(ok),
        "ms_median": statistics.median(ms),
        "ms_p90": pctl(ms, 0.9), "ms_max": max(ms),
        "peak_rss_max": max(r["peak_rss_mb"] for r in ok),
        "degrade_decisions": deg,
        "arm_calls": calls,
        "degrade_ratio": (deg / calls) if calls else 0.0,
        "n_degraded_games": sum(1 for r in ok if r["degrade_decisions"] > 0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", choices=["world", "node"], required=True,
                    help="world=bench 前瞻臂世界枚举上限；node=生产 wr_f 节点上限")
    ap.add_argument("--values", nargs="+", type=int, required=True)
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--arm", default="safety")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000,
                    help="axis=node 时的固定 world_cap")
    ap.add_argument("--node-cap", type=int, default=2000000,
                    help="axis=world 时的固定 node_cap")
    ap.add_argument("--time-budget-s", type=float, default=180.0,
                    help="每个 cap 值的统一时限（秒）")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "sweepcap"), True)

    args._seeds = [args.seed + i for i in range(args.games)]
    outdir = args.outdir or os.path.join(ROOT, "state", "ab",
                                         f"sweep_{args.axis}_{args.arm}")
    os.makedirs(outdir, exist_ok=True)

    print("=" * 108)
    print(f"cap 扫描：axis={args.axis}（"
          f"{'bench 前瞻臂 world_cap' if args.axis == 'world' else '生产 wr_f node_cap'}）")
    print(f"臂={args.arm}，{args.games} 局，种子 {args._seeds[0]}..{args._seeds[-1]}，"
          f"每值时限 {args.time_budget_s:.0f}s")
    print("⚠️ 本表是**样本内**胜率与资源成本，不是理论胜率极限")
    print("=" * 108)

    results = {}
    exits = {}
    for val in args.values:
        print(f"\n---- {args.axis}_cap = {val:,} ----", flush=True)
        jp = os.path.join(outdir, f"{args.axis}_{val}.jsonl")
        rows, ei = run_one_value(args.axis, val, args, jp)
        exits[val] = ei
        s = summarize(rows)
        results[val] = s

    # ---------------- 汇总表 ----------------
    print()
    print("=" * 108)
    print(f"汇总（axis={args.axis}，臂={args.arm}）")
    print("=" * 108)
    print(f"{'cap':>12} {'完成':>5} {'错误':>5} {'胜率':>8} {'95%CI':>18} "
          f"{'中位ms':>9} {'p90ms':>9} {'峰值MB':>9} {'降级决策':>9} {'降级局':>7}")
    print("-" * 108)
    for val in args.values:
        s = results[val]
        if not s:
            print(f"{val:>12} 无有效局（退出：{exits[val]['exit']}）")
            continue
        lo, hi = wilson(s["wins"], s["n_ok"])
        print(f"{val:>12,} {s['n_ok']:>5} {s['n_err']:>5} "
              f"{s['win_rate'] * 100:>7.2f}% "
              f"[{lo * 100:>6.2f}%,{hi * 100:>6.2f}%] "
              f"{s['ms_median']:>9.0f} {s['ms_p90']:>9.0f} "
              f"{s['peak_rss_max']:>9.1f} {s['degrade_decisions']:>9} "
              f"{s['n_degraded_games']:>7}")

    # ---------------- 配对比较（相邻 cap 值）----------------
    print()
    print("=" * 108)
    print("配对比较（相邻 cap 值，同 seed 配对；McNemar 精确检验）")
    print("=" * 108)
    vals = [v for v in args.values if results.get(v)]
    if len(vals) >= 2:
        print(f"{'A vs B':>28} {'配对n':>6} {'A赢B输':>7} {'B赢A输':>7} "
              f"{'配对差':>9} {'p值':>8} {'判定':>8}")
        print("-" * 108)
        for i in range(len(vals) - 1):
            a, b = vals[i], vals[i + 1]
            ra = {r["seed"]: r for r in
                  (json.loads(l) for l in open(
                      os.path.join(outdir, f"{args.axis}_{a}.jsonl"),
                      encoding="utf-8")) if not r.get("error")}
            rb = {r["seed"]: r for r in
                  (json.loads(l) for l in open(
                      os.path.join(outdir, f"{args.axis}_{b}.jsonl"),
                      encoding="utf-8")) if not r.get("error")}
            common = sorted(set(ra) & set(rb))
            bb = cc = 0
            for sd in common:
                wa, wb = bool(ra[sd]["won"]), bool(rb[sd]["won"])
                if wa and not wb:
                    bb += 1
                elif wb and not wa:
                    cc += 1
            n = len(common)
            p = mcnemar_exact(bb, cc)
            d = (cc - bb) / n if n else 0.0
            print(f"{f'{a:,} vs {b:,}':>28} {n:>6} {bb:>7} {cc:>7} "
                  f"{d * 100:>+8.2f}pp {p:>8.3f} "
                  f"{'显著' if p < 0.05 else '不显著':>8}")
        print()
        print("注：p ≥ 0.05 = 当前样本未检出差异（不等于等效）。")

    # ---------------- 超时/未完成单独列出 ----------------
    print()
    print("=" * 108)
    print("超时 / 未完成（单独列出，不剔除、不当作失败样本）")
    print("=" * 108)
    any_incomplete = False
    for val in args.values:
        ei = exits[val]
        s = results.get(val)
        n_ok = s["n_ok"] if s else 0
        if ei["exit"] != "completed" or n_ok < args.games:
            any_incomplete = True
            print(f"  cap={val:,}: 完成 {n_ok}/{args.games} 局，"
                  f"退出={ei['exit']}  {ei['detail']}")
    if not any_incomplete:
        print("  无（全部 cap 值均完成全部局数）")

    out = os.path.join(outdir, "sweep_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"axis": args.axis, "arm": args.arm,
                   "games": args.games, "seed": args.seed,
                   "time_budget_s": args.time_budget_s,
                   "results": {str(k): v for k, v in results.items()},
                   "exits": {str(k): v for k, v in exits.items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n汇总已写 {out}")


if __name__ == "__main__":
    main()
