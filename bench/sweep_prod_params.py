# -*- coding: utf-8 -*-
"""生产侧 `wr_threshold × node_cap` 联合评估（固定种子配对扫描）。

=================== 为什么必须做这个（而不是 world_cap 分布）====================
用户已明确：**生产可用更多内存，wr_threshold 可调**。
`world_cap` 只限制 **bench 前瞻臂**（`ExactEngine`），回答不了
"生产阈值该设多少"。生产真正的参数是：
  · `wr_threshold` —— 哪些局面允许进 `win_rate` 前瞻（越大覆盖越多）
  · `node_cap`     —— 前瞻递归的节点上限（超限降级为贪心）

本脚本对这两个参数做**二维联合扫描**，回答：
  · 提高 `wr_threshold` 新增的那些"大局面"，收益多少、成本多少？
  · `node_cap` 设多高才不会成为提前降级的瓶颈？

=================== ⚠️ 关键陷阱：wr_threshold 的设置路径 ====================
`utils/probability.py` 里：
    if _tb is not None:
        wr_threshold = _wr_current_threshold(_tb)     # 从 time_budget 状态读
    ...
    if limitation <= wr_threshold and self._wr_memory_ok(...):

而 `_tb = getattr(self, "_time_budget", None) if native.tuned() else None`。
**若 `_time_budget is None`，`wr_threshold` 恒为 `6 + LIMIT_BONUS_WIN_RATE`，
外部怎么改参数都无效**（本项目曾因此踩坑：阈值 8 与 999 两臂逐局完全一致）。

⇒ 本脚本用**两种方式**强制设定阈值，并校验生效：
  ① 设置 `solver._time_budget = {"wr": {"t": TH}}`（走真实读取路径）
  ② 同时把 `native.LIMIT_BONUS_WIN_RATE` 设为 TH-6（兜底常量路径）
  ③ 跑完后用 `_wr_current_threshold(_tb)` 回读校验实际值 == 目标值

=================== 公平性 ====================
  · 固定种子配对（同 seed 同棋盘）
  · `node_cap` 给足（默认 2e6），避免成为提前降级瓶颈
  · 报告：完成局数、胜率差及 Wilson CI、McNemar、节点峰值、命中数、
    p50/p90/p99 耗时、峰值 RSS
  · 超时/未完成局单独列出，不剔除、不当作失败样本
  · 逐局落盘 jsonl、flush 进度、退出码、支持 --resume

用法:
  # 先受控确认资源可承受（小样本）
  python bench/sweep_prod_params.py --thresholds 8 12 --node-caps 2000000 \
      --games 15 --time-budget-s 300

  # 再扩大样本
  python bench/sweep_prod_params.py --thresholds 8 12 16 --node-caps 2000000 \
      --games 60 --time-budget-s 900
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

# ⚠️ 必须在 install_stubs 之前保存原始 sleep（否则采样线程忙等抢 GIL，假慢 197x）
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


def play_one_with_params(seed, th, node_cap, arm, world_cap,
                         time_budget_s):
    """用指定的 wr_threshold 与 node_cap 跑一局，返回 (结果, 实际阈值)。"""
    import ab_policy as ab
    import utils.native as nat
    import utils.probability as prob

    # ⚠️ 阈值生效路径（本轮修复）：
    # ab_policy.play_one 现在支持 wr_threshold 参数 —— 传入时构造
    # _time_budget={"wr":{"t": th}}，让生产走真实读取路径
    # （`if _tb is not None: wr_threshold = _wr_current_threshold(_tb)`）。
    # 此前硬编码 _time_budget=None 导致只走常量兜底，外部设阈值无效
    # （实测目标 16 实际生效 8）。
    orig_cap_env = os.environ.get("MSW_WR_NODE_CAP")
    if node_cap is not None:
        os.environ["MSW_WR_NODE_CAP"] = str(int(node_cap))
    try:
        r = ab.play_one(seed, 30, 16, 99, arm, 80, world_cap, node_cap,
                        wr_threshold=th)
    finally:
        if orig_cap_env is None:
            os.environ.pop("MSW_WR_NODE_CAP", None)
        else:
            os.environ["MSW_WR_NODE_CAP"] = orig_cap_env

    # 校验：真实读取路径下 _wr_current_threshold 应返回目标值
    tb = {"wr": {"t": float(th)}}
    actual = prob._wr_current_threshold(tb)
    return r, actual


def run_cell(th, node_cap, args, jsonl_path):
    """跑一个 (th, node_cap) 组合。"""
    import ab_policy as ab
    import utils.native as nat

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
        if args.time_budget_s > 0:
            el = time.perf_counter() - t_start
            if el > args.time_budget_s:
                exit_info = {"exit": "time_budget",
                             "detail": f"达 {args.time_budget_s:.0f}s，"
                                       f"完成 {len(rows)}/{len(args._seeds)} 局"}
                print(f"      [time-budget] 停止，完成 {len(rows)} 局", flush=True)
                break
        ab.reset_gate_stats()
        prod_before = ab.prod_nodecap_snapshot()
        pk = Peak()
        pk.start()
        t0 = time.perf_counter()
        try:
            r, actual_th = play_one_with_params(
                sd, th, node_cap, args.arm, args.world_cap,
                args.time_budget_s)
            err = r.get("error")
        except Exception as e:                     # noqa: BLE001
            r = {}
            actual_th = None
            err = f"{type(e).__name__}: {e}"
            exit_info = {"exit": "exception", "detail": err}
        dt = (time.perf_counter() - t0) * 1000.0
        peak = pk.stop()
        gc.collect()
        gs = ab.gate_stats(prod_before)
        deg = (gs["world_cap"] + gs["node_budget"]
               + gs.get("prod_nodecap", 0))
        calls = r.get("arm_calls", 0)

        row = {
            "seed": sd, "th": th, "node_cap": node_cap,
            "actual_th": actual_th,
            "won": r.get("won"), "died": r.get("died"),
            "steps": r.get("steps"), "opened": r.get("opened"),
            "ms": dt, "peak_rss_mb": peak / 2 ** 20,
            "gate": dict(gs), "degrade_decisions": deg,
            "arm_calls": calls,
            "win_rate_calls": gs.get("prod_nodecap", 0),
            "error": err,
        }
        rows.append(row)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"      [{i + 1}/{len(args._seeds)}] seed={sd} "
              f"{dt:>8.0f}ms peak={peak / 2 ** 20:>6.1f}MB "
              f"deg={deg:>3} "
              f"{'win' if r.get('won') else ('dead' if r.get('died') else '?')}"
              f"{' ERR' if err else ''}", flush=True)
    return rows, exit_info


def summarize(rows):
    ok = [r for r in rows if not r["error"]]
    if not ok:
        return None
    ms = [r["ms"] for r in ok]
    return {
        "n_ok": len(ok), "n_err": len(rows) - len(ok),
        "wins": sum(1 for r in ok if r["won"]),
        "win_rate": sum(1 for r in ok if r["won"]) / len(ok),
        "ms_p50": statistics.median(ms),
        "ms_p90": pctl(ms, 0.9), "ms_p99": pctl(ms, 0.99),
        "ms_max": max(ms),
        "peak_rss_max": max(r["peak_rss_mb"] for r in ok),
        "degrade_decisions": sum(r["degrade_decisions"] for r in ok),
        "n_degraded_games": sum(1 for r in ok
                                if r["degrade_decisions"] > 0),
        "actual_th": sorted(set(str(r["actual_th"]) for r in ok)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", nargs="+", type=float, required=True)
    ap.add_argument("--node-caps", nargs="+", type=int, default=[2000000])
    ap.add_argument("--games", type=int, default=15)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--time-budget-s", type=float, default=300.0,
                    help="每个 (th,node_cap) 组合的统一时限")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "prodparams"), True)

    args._seeds = [args.seed + i for i in range(args.games)]
    outdir = args.outdir or os.path.join(ROOT, "state", "ab", "prod_params")
    os.makedirs(outdir, exist_ok=True)

    print("=" * 112)
    print("生产侧 wr_threshold × node_cap 联合评估")
    print(f"臂={args.arm}，{args.games} 局，种子 {args._seeds[0]}..{args._seeds[-1]}")
    print(f"阈值 {args.thresholds} × node_cap {args.node_caps}，"
          f"每组合时限 {args.time_budget_s:.0f}s")
    print("⚠️ 本表是**样本内**胜率与资源成本，不是理论极限")
    print("=" * 112)

    results = {}
    exits = {}
    for th in args.thresholds:
        for nc in args.node_caps:
            key = (th, nc)
            print(f"\n---- wr_threshold={th} × node_cap={nc:,} ----", flush=True)
            jp = os.path.join(outdir, f"th{th}_nc{nc}.jsonl")
            rows, ei = run_cell(th, nc, args, jp)
            exits[key] = ei
            results[key] = summarize(rows)

    # ---------------- 汇总 ----------------
    print()
    print("=" * 112)
    print("汇总（样本内胜率与资源成本）")
    print("=" * 112)
    print(f"{'th':>5} {'node_cap':>10} {'完成':>5} {'错误':>5} {'胜率':>8} "
          f"{'95%CI':>18} {'p50ms':>8} {'p90ms':>8} {'p99ms':>8} "
          f"{'峰值MB':>8} {'降级':>6}")
    print("-" * 112)
    for key in results:
        th, nc = key
        s = results[key]
        if not s:
            print(f"{th:>5} {nc:>10,} 无有效局（{exits[key]['exit']}）")
            continue
        lo, hi = wilson(s["wins"], s["n_ok"])
        print(f"{th:>5} {nc:>10,} {s['n_ok']:>5} {s['n_err']:>5} "
              f"{s['win_rate'] * 100:>7.2f}% "
              f"[{lo * 100:>6.2f}%,{hi * 100:>6.2f}%] "
              f"{s['ms_p50']:>8.0f} {s['ms_p90']:>8.0f} {s['ms_p99']:>8.0f} "
              f"{s['peak_rss_max']:>8.1f} {s['degrade_decisions']:>6}")

    # ---------------- 配对比较（阈值相邻档）----------------
    print()
    print("=" * 112)
    print("配对比较（相邻阈值，node_cap 相同；McNemar 精确检验）")
    print("=" * 112)
    for nc in args.node_caps:
        vals = [th for th in args.thresholds if results.get((th, nc))]
        if len(vals) < 2:
            continue
        print(f"\n  node_cap={nc:,}")
        print(f"  {'A vs B':>20} {'配对n':>6} {'A赢B输':>7} {'B赢A输':>7} "
              f"{'配对差':>9} {'p值':>8} {'判定':>8}")
        for i in range(len(vals) - 1):
            a, b = vals[i], vals[i + 1]
            ra = {r["seed"]: r for r in
                  (json.loads(l) for l in open(
                      os.path.join(outdir, f"th{a}_nc{nc}.jsonl"),
                      encoding="utf-8")) if not r.get("error")}
            rb = {r["seed"]: r for r in
                  (json.loads(l) for l in open(
                      os.path.join(outdir, f"th{b}_nc{nc}.jsonl"),
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
            print(f"  {f'{a} vs {b}':>20} {n:>6} {bb:>7} {cc:>7} "
                  f"{d * 100:>+8.2f}pp {p:>8.3f} "
                  f"{'显著' if p < 0.05 else '不显著':>8}")

    # ---------------- 阈值生效校验 ----------------
    print()
    print("=" * 112)
    print("⚠️ 阈值生效校验（必须全部匹配，否则读数无效）")
    print("=" * 112)
    all_ok = True
    for key, s in results.items():
        if not s:
            continue
        th = key[0]
        got = s["actual_th"]
        ok = got and all(abs(float(g) - th) < 1e-9 for g in got)
        print(f"  目标 th={th}: 实际 {got}  {'OK' if ok else 'XX 不匹配'}")
        if not ok:
            all_ok = False
    if not all_ok:
        print("\n  !! 阈值未按预期生效，本次读数不可用于判断阈值收益。")

    # ---------------- 超时/未完成 ----------------
    print()
    print("=" * 112)
    print("超时 / 未完成（单独列出，不剔除）")
    print("=" * 112)
    any_inc = False
    for key, ei in exits.items():
        s = results.get(key)
        n_ok = s["n_ok"] if s else 0
        if ei["exit"] != "completed" or n_ok < args.games:
            any_inc = True
            print(f"  th={key[0]} nc={key[1]:,}: 完成 {n_ok}/{args.games}，"
                  f"退出={ei['exit']}  {ei['detail']}")
    if not any_inc:
        print("  无（全部组合均完成全部局数）")

    out = os.path.join(outdir, "prod_params_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "games": args.games, "seed": args.seed,
                   "thresholds": args.thresholds,
                   "node_caps": args.node_caps,
                   "results": {f"th{k[0]}_nc{k[1]}": v
                               for k, v in results.items()},
                   "exits": {f"th{k[0]}_nc{k[1]}": v
                             for k, v in exits.items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n汇总已写 {out}")


if __name__ == "__main__":
    main()
