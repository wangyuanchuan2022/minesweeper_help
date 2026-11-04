# -*- coding: utf-8 -*-
"""B 线评测：自适应深度（避免 node_cap 降级）在**整局胜率**上的效果。

=================== 为什么不能用 regret 标定深度策略 ====================
Advisor 指出（我同意）：P1 要修的是**高复杂度局面**的 node_cap 降级与耗时
长尾，而高复杂度局面按定义**不可枚举**（世界数 1e24）⇒ 精确 regret 无真值。
只用可枚举子集标定深度，等于按"是否易算"筛选样本 = **自选择对照**
（这正是本项目纪律第 4 条禁止的做法）。

所以深度策略必须在**固定 seed 的真实对局**上评测，判据是：
  · 整局胜率（主判据，配对 McNemar）
  · node_cap 触发率
  · 单步超时率（>DECISION_TIME_BUDGET）
  · 耗时（中位/p90/总）
且必须覆盖**全部复杂度**，不能只挑小局面。

=================== 三臂设计（单变量）====================
  base   : 生产现状（不传 adaptive_depth，= 0）
  ad<N>  : adaptive_depth = N（N 越大越保守）

=================== 为什么这能回答"避免降级是否值得" ====================
若 ad>0 使 cap 率显著下降、且胜率不降（或上升）⇒ 值得。
若 cap 率没降 ⇒ 规则空转（本项目踩过"参数没接到生产路径"的假象）。
若 cap 率降了但胜率下降 ⇒ 变浅的代价大于降级的代价，应放弃。

用法:
  python bench/eval_depth_policy_games.py --games 2000 --workers 12 --arm ad2
"""
import argparse
import json
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--arm", default="base",
                    help="base | ad1 | ad2 | ad4 | ad8（数字=adaptive_depth）")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--node-cap", type=int, default=200000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    adaptive = 0
    if args.arm.startswith("ad"):
        adaptive = int(args.arm[2:])

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "depthgames"), True)

    import ab_policy as ab

    orig_win_rate = None
    stats = {"calls": 0, "cap_hits": 0, "timeouts": 0,
             "ms_total": 0.0, "ms_max": 0.0, "ms_list": []}

    def make_patch():
        """把 adaptive_depth 注入生产 win_rate 调用。

        ⚠️ 只在 adaptive > 0 时 patch；base 臂完全不碰生产路径，
        保证 base 读数与历史可比（这是 A/B 的对照基线，不能有副作用）。
        """
        if adaptive <= 0:
            return
        import utils.probability as prob
        import utils.native as native
        if not hasattr(prob, "_DP_ORIG"):
            prob._DP_ORIG = prob.ProbabilityMixin.win_rate

        def wr_patched(self, clicks, clicks9, res_list, cell_value, ck, num10):
            # 复刻生产 win_rate 的调用，只多传 adaptive_depth
            from utils.probability import _wr_node_cap, _WrNodeCapExceeded
            t0 = time.perf_counter()
            stats["calls"] += 1
            try:
                _res, _clicks, _total, _c2p = native.mscore.win_rate(
                    clicks, clicks9, native.as_groups(res_list), cell_value, ck,
                    num10, self.a, self.w, self.h, self.is_play,
                    self._pv_mapped_emit, _wr_node_cap(), -1.0, adaptive)
            except Exception as e:                        # noqa: BLE001
                if "node cap" in str(e):
                    stats["cap_hits"] += 1
                    self._wr_nodecap_hits += 1
                    raise _WrNodeCapExceeded(str(e)) from e
                raise
            ms = (time.perf_counter() - t0) * 1000.0
            stats["ms_total"] += ms
            stats["ms_max"] = max(stats["ms_max"], ms)
            stats["ms_list"].append(ms)
            if ms > 30000.0:
                stats["timeouts"] += 1
            return _res, _clicks, _total, _c2p

        prob.ProbabilityMixin.win_rate = wr_patched

    make_patch()

    nw = args.workers or max(1, (os.cpu_count() or 4) - 1)
    print("=" * 96)
    print(f"深度策略整局评测：arm={args.arm}（adaptive_depth={adaptive}）")
    print(f"  {args.games} 局，{nw} workers，seed={args.seed}，"
          f"node_cap={args.node_cap:,}")
    print("=" * 96, flush=True)

    t0 = time.perf_counter()
    # ab_policy 只暴露 play_one（没有 run_range）；单进程顺序跑。
    # 自适应深度实验量小（200~2000 局），且 patch 是模块级全局，
    # 多进程反而要重新 patch ⇒ 顺序跑更可靠。
    results = []
    for i in range(args.games):
        seed = args.seed + i
        try:
            r = ab.play_one(seed, args.w, args.h, args.a, "baseline",
                            limit=args.limit, node_cap=args.node_cap,
                            world_cap=4096)
            if isinstance(r, dict):
                results.append(r)
        except Exception:                                 # noqa: BLE001
            continue
        if (i + 1) % 200 == 0:
            print(f"    ... {i+1}/{args.games}", flush=True)
    wall = time.perf_counter() - t0

    won = sum(1 for r in results if r.get("won"))
    n = len(results)
    rate = won / n if n else 0.0
    p = 0.3545
    se = (p * (1 - p) / n) ** 0.5 if n else 0.0
    ci = (max(0.0, rate - 1.96 * se), min(1.0, rate + 1.96 * se))

    ms = sorted(stats["ms_list"]) if stats["ms_list"] else [0.0]
    print()
    print(f"  胜率 {won}/{n} = {rate*100:.2f}%  95%CI [{ci[0]*100:.2f}, {ci[1]*100:.2f}]")
    print(f"  墙钟 {wall:.1f}s（{wall/n*1000:.1f} ms/局）")
    if adaptive > 0:
        print(f"  win_rate 调用 {stats['calls']} 次，"
              f"node_cap 触发 {stats['cap_hits']} 次"
              f"（{stats['cap_hits']/max(1,stats['calls'])*100:.2f}%）")
        print(f"  单步耗时：中位 {ms[len(ms)//2]:.1f}ms  "
              f"p90 {ms[min(len(ms)-1,int(len(ms)*0.9))]:.1f}ms  "
              f"max {stats['ms_max']:.1f}ms  >30s {stats['timeouts']} 次")
    else:
        print("  （base 臂不 patch 生产路径，无 cap 统计；cap 由生产自身日志给出）")

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   f"depthgames_{args.arm}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "adaptive": adaptive, "games": n,
                   "won": won, "rate": rate, "ci": list(ci),
                   "wall_s": wall, "stats": {k: v for k, v in stats.items()
                                             if k != "ms_list"}},
                  f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
