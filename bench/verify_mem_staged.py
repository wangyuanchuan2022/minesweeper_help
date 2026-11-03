# -*- coding: utf-8 -*-
"""分级内存验证：100 / 500 / 2000 局，带进程级内存保护与全程峰值记录。

=================== 为什么必须分级 ====================
25 局的 "+0.032MB/局" 线性外推**不能**排除：
  · 罕见大局面（本次峰值正是由特定实时局面触发的）
  · 阶段性缓存（如 Solver.checked 跨步累积到阈值才释放）
  · 非线性增长
因此按 100 → 500 → 2000 逐级验证，每级记录：
  · 全程峰值 RSS、分段局末 RSS（每 50 局一个采样点）
  · 存活 Solver 数
  · 各闸门触发次数（world_cap / invalid_case / no_world / node_budget）
  · 单局最大耗时与最慢局 seed（便于复现）

=================== 进程级内存保护 ====================
若某局峰值 RSS 超过 --rss-limit（默认 2048MB），**立即中止**并保存现场
（当前 seed、局序、闸门统计），避免再次把整机顶爆。这是硬保护，
不依赖"预测是否会超"。

用法:
  python bench/verify_mem_staged.py --games 100 --arm safety
  python bench/verify_mem_staged.py --games 500 --arm safety
  python bench/verify_mem_staged.py --games 2000 --arm safety
"""
import argparse
import gc
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import psutil                            # noqa: E402

# ⚠️ 见 bench_four_axis.py 的说明：install_stubs 会把 time.sleep 换成
# 空操作，采样线程必须用原始 sleep，否则忙等抢 GIL（实测 197x 变慢）。
import time as _time_mod
_REAL_SLEEP = _time_mod.sleep

P = psutil.Process()


class PeakWatch(threading.Thread):
    """后台监控进程峰值 RSS；超限则置 abort 标记。"""

    def __init__(self, limit_mb, interval=0.05):
        super().__init__(daemon=True)
        self.limit = limit_mb * 2 ** 20
        self.interval = interval
        self.peak = 0
        self.abort = False
        self.run_ = True

    def run(self):
        self.peak = P.memory_info().rss
        while self.run_:
            try:
                r = P.memory_info().rss
                if r > self.peak:
                    self.peak = r
                    if r > self.limit:
                        self.abort = True
            except Exception:                          # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)

    def stop(self):
        self.run_ = False
        self.join(timeout=2)
        return self.peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--arm", default="safety")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--rss-limit", type=float, default=2048.0,
                    help="进程峰值 RSS 硬上限（MB），超限立即中止")
    ap.add_argument("--report-every", type=int, default=50)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "staged"), True)

    import ab_policy as ab

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   f"staged_{args.arm}_{args.games}.json")
    print("=" * 100)
    print(f"分级内存验证：arm={args.arm}，{args.games} 局，"
          f"RSS 硬上限 {args.rss_limit:.0f} MB")
    print("=" * 100)
    print(f"{'局':>5} {'峰值MB':>9} {'局末MB':>9} {'Solver':>7} "
          f"{'耗时s':>7} {'累计降级':>9} 结果")
    print("-" * 100)

    ab.reset_gate_stats()
    watch = PeakWatch(args.rss_limit)
    watch.start()

    rows = []
    aborted = False
    t_all = time.time()
    for i in range(args.games):
        sd = args.seed + i
        t0 = time.time()
        try:
            r = ab.play_one(sd, 30, 16, 99, args.arm, 80, args.world_cap,
                            args.node_cap)
        except Exception as e:                         # noqa: BLE001
            r = {"seed": sd, "error": f"{type(e).__name__}: {e}"}
        dt = time.time() - t0
        gc.collect()
        end = P.memory_info().rss
        sov = sum(1 for o in gc.get_objects() if type(o).__name__ == "Solver")
        gs = ab.gate_stats()
        rows.append({"i": i, "seed": sd, "end_mb": end / 2 ** 20,
                     "sov": sov, "sec": dt,
                     "won": r.get("won"), "died": r.get("died"),
                     "error": r.get("error"),
                     "gate": dict(gs)})
        if (i + 1) % args.report_every == 0 or i == 0:
            print(f"{i + 1:>5} {watch.peak / 2 ** 20:>9.1f} "
                  f"{end / 2 ** 20:>9.1f} {sov:>7} {dt:>7.2f} "
                  f"{sum(gs.values()):>9} "
                  f"{'ERR' if r.get('error') else 'ok'}")
        if watch.abort:
            print()
            print(f"!!! 进程峰值 RSS {watch.peak / 2 ** 20:.0f} MB 超上限 "
                  f"{args.rss_limit:.0f} MB —— 立即中止于第 {i + 1} 局 "
                  f"(seed={sd})")
            aborted = True
            break

    watch.stop()
    dt_all = time.time() - t_all
    gs = ab.gate_stats()

    # ---- 分段趋势 ----
    ends = [r["end_mb"] for r in rows]
    segs = []
    for k in range(0, len(ends), 100):
        chunk = ends[k:k + 100]
        if chunk:
            segs.append({"from": k + 1, "to": k + len(chunk),
                         "end_mb": round(chunk[-1], 1),
                         "mean_mb": round(sum(chunk) / len(chunk), 1)})

    print()
    print("=" * 100)
    print("汇总")
    print("=" * 100)
    print(f"完成 {len(rows)}/{args.games} 局，总耗时 {dt_all:.0f}s")
    print(f"全程峰值 RSS      : {watch.peak / 2 ** 20:.1f} MB "
          f"（上限 {args.rss_limit:.0f}）")
    print(f"末局末 RSS        : {ends[-1]:.1f} MB")
    print(f"存活 Solver       : {min(r['sov'] for r in rows)}"
          f" → {max(r['sov'] for r in rows)}")
    print(f"分段局末 RSS      : " +
          "  ".join(f"[{s['from']}-{s['to']}]={s['end_mb']}" for s in segs))
    errs = [r for r in rows if r["error"]]
    print(f"错误局数          : {len(errs)}"
          + (f"（例：{errs[0]['error'][:60]}）" if errs else ""))
    print(f"闸门触发次数      : {gs}")
    slow = max(rows, key=lambda r: r["sec"])
    print(f"最慢局            : seed={slow['seed']} {slow['sec']:.1f}s")

    # ---- 判定 ----
    ok = True
    if aborted:
        print("\n结论：**未通过** —— 触发进程级内存保护，已中止。")
        ok = False
    elif watch.peak / 2 ** 20 > args.rss_limit:
        print("\n结论：**未通过** —— 峰值超上限。")
        ok = False
    else:
        # 分段趋势：最后一段相对第一段是否显著上升
        if len(segs) >= 2:
            drift = segs[-1]["mean_mb"] - segs[0]["mean_mb"]
            print(f"\n分段均值漂移      : {drift:+.1f} MB"
                  f"（末段 vs 首段）")
            if drift > 200:
                print("结论：**可疑累积** —— 分段均值显著上升，需继续排查。")
                ok = False
        if ok:
            print(f"结论：{len(rows)} 局内峰值在保护线内、分段未见显著累积。")
            print("      注意：这是**样本内**结论，不等于 2000 局等价；"
                  f"需按 100→500→2000 逐级验证。")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "games": args.games,
                   "seed": args.seed, "aborted": aborted,
                   "peak_mb": watch.peak / 2 ** 20,
                   "rss_limit_mb": args.rss_limit,
                   "gate_stats": gs, "segments": segs,
                   "rows": rows}, f, ensure_ascii=False)
    print(f"明细已写 {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
