# -*- coding: utf-8 -*-
"""B 线：adaptive_depth 的整局策略评测（胜率 / cap 率 / 超时率）。

例外授权声明（用户已批准，必须保留）
====================================
用户要求「以后的实验全都用 C++ 写」。本脚本是**显式批准的例外**：
B 线整局对局引擎仍是 Python（sim/minesweeper_sim.py + utils/solver.py +
utils/probability.py 约 2300 行未移植）。

批准条件：B 线暂用 Python，且**所有耗时读数标为不可用，只用胜率/cap 率
作为判据**。本脚本输出显式标注 USABLE_FOR_RANKING = False。

为什么耗时必须废弃
==================
bench/report_repeat_slowdown.py 实测：同进程内连续调用 win_rate，
节点数恒定但耗时漂移 5.44x（G10）/ 2.36x（G11），新进程才稳定（2.1%）。
整局评测必然同进程跑几百局 => 耗时被漂移污染，不可用于策略比较。

评测设计
========
  base : adaptive_depth = 0（生产现状，完全不 patch 生产路径）
  ad1  : adaptive_depth = 1（只削最贵的约 8% 局面）
  ad2  : adaptive_depth = 2（只削最贵的约 3%）
同 seed 逐局配对。判据：胜率 + McNemar + 95%CI + MDE；cap 率；超时率。

用法
====
  python bench/eval_depth_policy_games.py --arm ad1 --games 2000
  python bench/eval_depth_policy_games.py --compare base ad1 --games 2000
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

USABLE_FOR_RANKING = False
TIME_CAVEAT = ("耗时读数不可用于策略排序（同进程 win_rate 调用存在 2.4~5.4x "
               "可复现漂移，见 bench/report_repeat_slowdown.py）")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def mcnemar_p(a_only, b_only):
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def mde80(p, n):
    if n <= 0:
        return None
    se = math.sqrt(2 * p * (1 - p) / n)
    return (1.959964 + 0.841621) * se


def run_arm(arm, games, seed, w, h, a, limit, node_cap):
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "depthgames"), True)

    adaptive = int(arm[2:]) if arm.startswith("ad") else 0
    # ⚠️ 关键区分（Advisor 纠正）：
    #   cap_hits   = node_cap 触发次数（异常路径）
    #   shrunk     = adaptive 规则**实际触发**并改变了 depth_limit 的次数
    #   pick_changed = 触发后**最终选格**是否真的改变
    # cap 率 != 触发率；600 局 0 差异不能推出"规则从未触发"。
    stats = {"calls": 0, "cap_hits": 0, "over_budget": 0,
             "ms_max": 0.0, "ms_sum": 0.0,
             "shrunk": 0, "pick_changed": 0, "base_picks": {}}

    if adaptive > 0:
        import utils.probability as prob
        import utils.native as native
        from utils.probability import _wr_node_cap, _WrNodeCapExceeded

        def wr_patched(self, clicks, clicks9, res_list, cell_value, ck, num10):
            t0 = time.perf_counter()
            stats["calls"] += 1
            try:
                r = native.mscore.win_rate(
                    clicks, clicks9, native.as_groups(res_list), cell_value, ck,
                    num10, self.a, self.w, self.h, self.is_play,
                    self._pv_mapped_emit, _wr_node_cap(), -1.0, adaptive)
            except Exception as e:
                if "node cap" in str(e):
                    stats["cap_hits"] += 1
                    self._wr_nodecap_hits += 1
                    raise _WrNodeCapExceeded(str(e)) from e
                raise
            ms = (time.perf_counter() - t0) * 1000.0
            stats["ms_sum"] += ms
            stats["ms_max"] = max(stats["ms_max"], ms)
            if ms > 30000.0:
                stats["over_budget"] += 1
            # 读 C++ 侧 phase 快照：shrunk=1 表示规则真的改变了 depth_limit
            try:
                ph = native.mscore.last_phase()
                if int(ph[4]) == 1:
                    stats["shrunk"] += 1
            except Exception:
                pass
            # 记录选格（用于与 base 臂逐决策比较）
            key = (tuple(map(int, clicks[0])) if clicks else None,
                   len(clicks), len(clicks9), int(num10))
            try:
                arr = list(r[0])
                if arr:
                    bi = max(range(len(arr)), key=lambda i: arr[i])
                    stats["base_picks"][key] = bi
            except Exception:
                pass
            return r

        prob.ProbabilityMixin.win_rate = wr_patched

    import ab_policy as ab
    results = []
    t0 = time.perf_counter()
    for i in range(games):
        sd = seed + i
        try:
            r = ab.play_one(sd, w, h, a, "baseline", limit=limit,
                            node_cap=node_cap, world_cap=4096)
        except Exception:
            continue
        if isinstance(r, dict):
            results.append({"seed": sd, "won": bool(r.get("won")),
                            "died": bool(r.get("died")),
                            "steps": r.get("steps"),
                            "opened": r.get("opened")})
        if (i + 1) % 200 == 0:
            print("    %s: %d/%d" % (arm, i + 1, games), flush=True)
    wall = time.perf_counter() - t0
    return results, stats, wall


def summarize(results, stats, arm, wall):
    n = len(results)
    won = sum(1 for r in results if r["won"])
    rate = won / n if n else 0.0
    lo, hi = wilson(won, n)
    out = {
        "arm": arm, "n": n, "won": won, "rate": rate,
        "ci95": [lo, hi], "wall_s": wall,
        "USABLE_FOR_RANKING": USABLE_FOR_RANKING,
        "time_caveat": TIME_CAVEAT,
    }
    if arm.startswith("ad"):
        out["cap_hits"] = stats["cap_hits"]
        out["win_rate_calls"] = stats["calls"]
        out["cap_rate_pct"] = (stats["cap_hits"] / max(1, stats["calls"]) * 100.0)
        out["over_budget_calls"] = stats["over_budget"]
        out["shrunk"] = stats["shrunk"]
        out["shrunk_rate_pct"] = (stats["shrunk"] / max(1, stats["calls"]) * 100.0)
        out["cap_rate_pct"] = (stats["cap_hits"] / max(1, stats["calls"]) * 100.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="ad1")
    ap.add_argument("--compare", nargs="*", default=None)
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--node-cap", type=int, default=200000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    arms = args.compare if args.compare else [args.arm]
    print("=" * 100)
    print("B 线：adaptive_depth 整局策略评测")
    print("=" * 100)
    print("  [!] 例外授权：B 线对局引擎为 Python（用户已批准）")
    print("  [!] " + TIME_CAVEAT)
    print("  => 本报告只用胜率 / cap 率 / 超时率作判据，耗时不参与排序")
    print()
    print("  臂=%s  局数=%d  seed=%d+" % (arms, args.games, args.seed))
    print()

    all_res = {}
    summaries = []
    for arm in arms:
        print("--- 跑 %s ---" % arm, flush=True)
        res, stats, wall = run_arm(arm, args.games, args.seed,
                                   args.w, args.h, args.a,
                                   args.limit, args.node_cap)
        all_res[arm] = res
        s = summarize(res, stats, arm, wall)
        summaries.append(s)
        print("  %s: %d/%d = %.2f%%  95%%CI [%.2f, %.2f]"
              % (arm, s["won"], s["n"], s["rate"] * 100,
                 s["ci95"][0] * 100, s["ci95"][1] * 100))
        if "cap_rate_pct" in s:
            print("        win_rate 调用 %d：cap 触发 %d（%.2f%%），"
                  "**规则实际触发 %d（%.2f%%）**，>30s %d 次"
                  % (s["win_rate_calls"], s["cap_hits"], s["cap_rate_pct"],
                     s.get("shrunk", 0), s.get("shrunk_rate_pct", 0.0),
                     s["over_budget_calls"]))
            print("        ⚠️ cap 率 != 触发率：cap 是异常降级，shrunk 才是规则生效")
        print()

    if len(arms) >= 2:
        print("=" * 100)
        print("配对比较（同 seed）")
        print("=" * 100)
        base = arms[0]
        base_map = {r["seed"]: r["won"] for r in all_res[base]}
        for arm in arms[1:]:
            amap = {r["seed"]: r["won"] for r in all_res[arm]}
            common = sorted(set(base_map) & set(amap))
            if not common:
                print("  %s vs %s: 无共同 seed" % (arm, base))
                continue
            a_only = sum(1 for s in common if amap[s] and not base_map[s])
            b_only = sum(1 for s in common if base_map[s] and not amap[s])
            n = len(common)
            ra = sum(1 for s in common if amap[s]) / n
            rb = sum(1 for s in common if base_map[s]) / n
            p = mcnemar_p(a_only, b_only)
            mde = mde80(rb, n)
            print()
            print("  %s vs %s（n=%d）" % (arm, base, n))
            print("    %s 胜率 %.2f%%   %s 胜率 %.2f%%   Delta=%+.2fpp"
                  % (base, rb * 100, arm, ra * 100, (ra - rb) * 100))
            print("    配对：%s 独赢 %d，%s 独赢 %d，McNemar p=%.4f"
                  % (arm, a_only, base, b_only, p))
            if mde:
                verdict = ("可检出该效应" if abs(ra - rb) >= mde
                           else "低于 MDE，不可判定")
                print("    MDE(80%%) ~ %.2fpp => %s" % (mde * 100, verdict))
            print("    判定：" + ("显著" if p < 0.05
                                else "不显著（不等于无效应，受 MDE 限制）"))

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   "depthgames_%s.json" % "_".join(arms))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summaries": summaries,
                   "USABLE_FOR_RANKING": USABLE_FOR_RANKING,
                   "time_caveat": TIME_CAVEAT,
                   "args": vars(args)}, f, ensure_ascii=False, indent=1)
    print()
    print("明细已写 %s" % out)
    print()
    print("[!] 再次声明：" + TIME_CAVEAT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
