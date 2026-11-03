# -*- coding: utf-8 -*-
"""四维联合基准：效果 × 耗时 × 内存 × 降级（固定种子配对）。

=================== 为什么必须是四维联合 ====================
单看平均耗时会得出错误结论。具体陷阱：
  · 若某臂频繁触发 world_cap 而回退贪心，它的"计算量更小、更快"
    其实是**没算**，不是**算得快**。只看耗时会误报为"算法提速"。
  · 若某臂胜率更高但耗时翻倍，单看任一项都无法判断是否值得。
⇒ 四项必须同时报告，且耗时**按降级类型分层**。

=================== 配对设计 ====================
同一批固定 seed（默认 20261102 起），各臂面对**逐字节相同**的棋盘。
因此臂间差异只可能来自策略本身，不是棋盘难度差异。

=================== 分层口径 ====================
每局按"该局是否发生过资源降级"分成两类，分别统计：
  · clean   —— 全程未触发任何闸门（真实完整计算）
  · degraded—— 至少触发一次 world_cap / node_budget
报告耗时时**必须**分层，否则快慢不可比。
同时输出各闸门触发次数（invalid_case 必须恒为 0，否则说明局面校验有问题）。

=================== 长测可靠性（按评审要求）====================
  · 每局结束**立即**追加写一行到 .jsonl（中断后已完成样本仍可用）
  · 进度**无缓冲**打印（flush=True）
  · 顶层 try/except + 退出码落盘（区分正常完成 / 异常 / 被终止）
  · 每局记录峰值 RSS（后台采样）

用法:
  python bench/bench_four_axis.py --games 30 --arms baseline safety exact
  python bench/bench_four_axis.py --games 30 --arms baseline --resume
"""
import argparse
import gc
import json
import os
import signal
import statistics
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import psutil                            # noqa: E402

P = psutil.Process()


class PeakSampler(threading.Thread):
    def __init__(self, interval=0.01):
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
            except Exception:                          # noqa: BLE001
                break
            time.sleep(self.interval)

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


def run_arm(arm, seeds, args, jsonl_path):
    """跑一个臂，逐局落盘。返回 (rows, exit_info)。"""
    import ab_policy as ab

    rows = []
    exit_info = {"exit": "completed", "detail": ""}

    # resume：读已有行，跳过已完成的 seed
    done = set()
    if args.resume and os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    done.add(d["seed"])
                    rows.append(d)
                except Exception:                      # noqa: BLE001
                    pass
        if done:
            print(f"  [resume] 已有 {len(done)} 局，跳过", flush=True)

    for idx, sd in enumerate(seeds):
        if sd in done:
            continue
        ab.reset_gate_stats()
        smp = PeakSampler()
        smp.start()
        t0 = time.perf_counter()
        try:
            r = ab.play_one(sd, 30, 16, 99, arm, 80, args.world_cap,
                            args.node_cap)
            err = r.get("error")
        except Exception as e:                         # noqa: BLE001
            r = {}
            err = f"{type(e).__name__}: {e}"
            exit_info = {"exit": "exception", "detail": err}
        dt = (time.perf_counter() - t0) * 1000.0
        peak = smp.stop()
        gc.collect()
        end = P.memory_info().rss
        gs = ab.gate_stats()
        degraded = (gs["world_cap"] + gs["node_budget"]) > 0

        row = {
            "seed": sd, "arm": arm,
            # --- 效果 ---
            "won": r.get("won"), "died": r.get("died"),
            "opened": r.get("opened"), "steps": r.get("steps"),
            # --- 耗时 ---
            "ms": dt,
            "arm_ms_total": r.get("arm_ms_total", 0.0),
            "arm_ms_max": r.get("arm_ms_max", 0.0),
            # --- 内存 ---
            "peak_rss_mb": peak / 2 ** 20,
            "end_rss_mb": end / 2 ** 20,
            # --- 降级 ---
            "gate": dict(gs),
            "degraded": degraded,
            "arm_calls": r.get("arm_calls", 0),
            "arm_fallback": r.get("arm_fallback", 0),
            "error": err,
        }
        rows.append(row)
        # 逐局立即落盘（中断后已完成样本仍可用）
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  [{arm} {idx + 1}/{len(seeds)}] seed={sd} "
              f"{dt:>8.0f}ms peak={peak / 2 ** 20:>6.1f}MB "
              f"{'DEG' if degraded else '   '} "
              f"{'win' if r.get('won') else ('dead' if r.get('died') else '?')}"
              f"{' ERR:' + err[:40] if err else ''}", flush=True)

    return rows, exit_info


def summarize(arm, rows, budget_mb):
    ok = [r for r in rows if not r["error"]]
    if not ok:
        return None
    clean = [r for r in ok if not r["degraded"]]
    deg = [r for r in ok if r["degraded"]]

    def stat(sub):
        if not sub:
            return None
        ms = [r["ms"] for r in sub]
        return {
            "n": len(sub),
            "win_rate": sum(1 for r in sub if r["won"]) / len(sub),
            "ms_median": statistics.median(ms),
            "ms_p90": pctl(ms, 0.9),
            "ms_max": max(ms),
            "peak_rss_max": max(r["peak_rss_mb"] for r in sub),
            "peak_rss_median": statistics.median(
                [r["peak_rss_mb"] for r in sub]),
            "arm_ms_total": sum(r["arm_ms_total"] for r in sub),
        }

    gs_total = {}
    for r in ok:
        for k, v in r["gate"].items():
            gs_total[k] = gs_total.get(k, 0) + v

    return {
        "arm": arm, "n": len(ok), "n_error": len(rows) - len(ok),
        "all": stat(ok), "clean": stat(clean), "degraded": stat(deg),
        "gate_total": gs_total,
        "peak_rss_over_budget": any(
            r["peak_rss_mb"] > budget_mb for r in ok),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--arms", nargs="+",
                    default=["baseline", "safety", "exact"])
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--rss-budget", type=float, default=1536.0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "state", "ab",
                                                     "four_axis"))
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "four"), True)

    os.makedirs(args.outdir, exist_ok=True)
    seeds = [args.seed + i for i in range(args.games)]

    print("=" * 104)
    print(f"四维联合基准：{len(args.arms)} 臂 × {args.games} 局（固定种子配对）")
    print(f"种子 {seeds[0]}..{seeds[-1]}；world_cap={args.world_cap:,} "
          f"node_cap={args.node_cap:,}；RSS 预算 {args.rss_budget:.0f} MB")
    print("=" * 104)

    summaries = {}
    exit_log = {}
    for arm in args.arms:
        jsonl = os.path.join(args.outdir, f"{arm}.jsonl")
        print(f"\n---- 臂 {arm} ----", flush=True)
        try:
            rows, ei = run_arm(arm, seeds, args, jsonl)
        except KeyboardInterrupt:
            rows, ei = [], {"exit": "interrupted", "detail": "KeyboardInterrupt"}
            print("  ! 被用户中断", flush=True)
        except Exception as e:                         # noqa: BLE001
            rows, ei = [], {"exit": "exception",
                            "detail": f"{type(e).__name__}: {e}"}
            traceback.print_exc()
        exit_log[arm] = ei
        s = summarize(arm, rows, args.rss_budget)
        if s:
            summaries[arm] = s
            with open(os.path.join(args.outdir, f"{arm}_summary.json"), "w",
                      encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=1)

    # ---------------- 汇总表 ----------------
    print()
    print("=" * 104)
    print("四维联合汇总（配对：同 seed 同棋盘）")
    print("=" * 104)
    hdr = (f"{'臂':<10} {'完成':>5} {'胜率':>7} | "
           f"{'clean n':>7} {'clean中位ms':>12} {'clean胜率':>9} | "
           f"{'deg n':>6} {'deg中位ms':>10} | {'峰值MB':>8} {'降级次数':>9}")
    print(hdr)
    print("-" * 104)
    for arm, s in summaries.items():
        a = s["all"]
        c = s["clean"] or {}
        d = s["degraded"] or {}
        gs = s["gate_total"]
        deg_n = gs.get("world_cap", 0) + gs.get("node_budget", 0)
        print(f"{arm:<10} {s['n']:>5} {a['win_rate'] * 100:>6.1f}% | "
              f"{c.get('n', 0):>7} {c.get('ms_median', 0):>12.0f} "
              f"{c.get('win_rate', 0) * 100:>8.1f}% | "
              f"{d.get('n', 0):>6} {d.get('ms_median', 0):>10.0f} | "
              f"{a['peak_rss_max']:>8.1f} {deg_n:>9}")

    print()
    print("闸门触发明细：")
    for arm, s in summaries.items():
        gs = s["gate_total"]
        inv = gs.get("invalid_case", 0)
        flag = "  <<< 异常！局面校验不该触发" if inv else ""
        print(f"  {arm:<10} {gs}{flag}")

    print()
    print("退出状态：")
    for arm, ei in exit_log.items():
        print(f"  {arm:<10} {ei['exit']}"
              + (f"  {ei['detail'][:70]}" if ei["detail"] else ""))

    # 结论性提示
    print()
    print("=" * 104)
    print("读法（重要）")
    print("=" * 104)
    print("· clean 与 degraded 的耗时分列：degraded 局因回退贪心而更快，")
    print("  那是「没算」不是「算得快」，跨臂比较必须看同一层。")
    print("· 胜率用配对同 seed，但仍需 McNemar 检验才能说显著；")
    print("  本表只给原始读数，不下显著性结论。")
    print("· invalid_case 必须恒为 0；非 0 说明局面校验逻辑有问题。")

    with open(os.path.join(args.outdir, "four_axis_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump({"summaries": summaries, "exit": exit_log,
                   "games": args.games, "seed": args.seed,
                   "world_cap": args.world_cap,
                   "node_cap": args.node_cap},
                  f, ensure_ascii=False, indent=1)
    print(f"\n汇总已写 {os.path.join(args.outdir, 'four_axis_summary.json')}")


if __name__ == "__main__":
    main()
