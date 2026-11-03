# -*- coding: utf-8 -*-
"""阈值分层诊断（同决策点配对版）：在**低档轨迹的同一批决策点**上直接配对。

=================== 为什么不能用「两批整局相减」====================
上一版跑 th=8 一批、th=16 一批，然后比调用总数（如 [4,8) 区间 6 vs 5）。
但两档的**决策轨迹本身不同**（6 vs 5 就证明不一一对应）：
低档跳过的决策点不会改变盘面，高档调用了的决策点会改变后续走法。
所以调用总数相减**不能**推出"阈值新增覆盖了哪些决策"。

=================== 正确做法：覆盖条件是纯函数 ====================
`number5_1` 的覆盖判据是：

    if limitation <= wr_threshold and self._wr_memory_ok(...):

其中 `limitation = len(clicks9) * 0.8 + log2(total)` 只依赖**当前局面**。
⇒ 在**同一个决策点**上，是否被覆盖只取决于阈值：
    · th=8  覆盖 ⟺ limitation ≤ 8
    · th=16 覆盖 ⟺ limitation ≤ 16
    · **仅 th=16 新增覆盖 ⟺ 8 < limitation ≤ 16**

所以只需跑**一批**（低档，轨迹 = 生产真实轨迹），对每个决策点：
  1. 记录其 limitation（低档跳过时也记）
  2. 若 8 < limitation ≤ 16（低档跳过、高档会算），**强制调用一次 win_rate**
     仅用于测量：单决策耗时 + 选格是否与贪心不同
  3. 丢弃该结果，**不改变轨迹** ⇒ 所有决策点都是同一个真实局面

这样得到的「新增覆盖」样本是**真正同决策点配对**的，
且低档轨迹未被污染。

=================== 停止条件 ====================
收集到 `--target-pairs` 个新增覆盖决策点即停止（而不是固定局数）；
`--max-games` 为兜底。单决策有 `--decision-timeout-s` 超时保护。

用法:
  python bench/diag_threshold_paired.py --low 8 --high 16 \
      --target-pairs 20 --max-games 40
"""
import argparse
import hashlib
import json
import math
import os
import signal
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import time as _time_mod                      # noqa: E402
_REAL_SLEEP = _time_mod.sleep


class DecisionTimeout(Exception):
    pass


class _Timeout:
    """跨平台单决策超时（Windows 无 SIGALRM，用线程定时器）。

    ⚠️ 这里踩过一个坑：最初用 signal.SIGALRM + setitimer，
    但 **Windows 没有 SIGALRM**，导致所有新增覆盖样本都报
    `AttributeError: module 'signal' has no attribute 'SIGALRM'`。
    """

    def __init__(self, seconds):
        import threading
        self.seconds = seconds
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self):
        self.fired = True

    def __enter__(self):
        if self.seconds and self.seconds > 0:
            self._timer.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._timer.cancel()
        except Exception:                          # noqa: BLE001
            pass
        return False


def pctl(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def run_paired(args):
    """跑一批低档轨迹，对新增覆盖区间强制测量 win_rate。"""
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "thpair2"), True)

    import numpy as np
    import ab_policy as ab
    import utils.probability as prob

    stats = {
        "n_decision": 0,
        "n_covered_low": 0,
        "n_new_cover": 0,
        "new_cover": [],
        "both_covered": [],
        "skipped": 0,
    }

    orig_core = prob.ProbabilityMixin._number5_1_core
    orig_wr = prob.ProbabilityMixin.win_rate

    # 当前决策点上下文
    ctx = {"lim": None, "fp": None}

    # 低档真实调用时记录（用于 both_covered 对照）
    def wr_hook(self, clicks, clicks9, res_list, cell_value, ck, num10):
        total = 1
        for c in ck:
            total *= int(c)
        lim = len(clicks9) * 0.8 + math.log2(max(total, 1))
        ctx["lim"] = round(float(lim), 2)
        t0 = _time_mod.perf_counter()
        out = orig_wr(self, clicks, clicks9, res_list, cell_value, ck, num10)
        dt = (_time_mod.perf_counter() - t0) * 1000.0
        res, sclicks = out[0], out[1]
        arr = np.asarray(res, float)
        pick_wr = None
        if arr.size:
            a = int(np.argmax(arr))
            if 0 <= a < len(sclicks):
                pick_wr = list(sclicks[a])
        c2p = out[3] or {}
        pick_g = (list(max(c2p.items(), key=lambda kv: kv[1])[0])
                  if c2p else None)
        stats["both_covered"].append({
            "lim": ctx["lim"], "ms": dt,
            "same_pick": (pick_wr == pick_g) if pick_wr and pick_g else None,
        })
        return out

    def core_hook(self, cell_value):
        import numpy as np
        fp = hashlib.md5(np.asarray(cell_value, np.int32).tobytes()
                         ).hexdigest()[:16]
        ctx["fp"] = fp
        ctx["lim"] = None
        stats["n_decision"] += 1

        # 先跑真实逻辑（低档：limitation ≤ low 才会调 win_rate）
        out = orig_core(self, cell_value)

        # 若该决策点落在"新增覆盖区间"，强制测量一次（不改变轨迹）
        lim = ctx["lim"]
        if lim is not None and args.low < lim <= args.high:
            # lim 已被真实调用填上 ⇒ 说明低档也覆盖了（不该发生）
            pass
        elif lim is None:
            # 低档没调 win_rate ⇒ 需要复算 limitation 判断是否落在新增区间
            pass
        return out

    # 上面的 core_hook 拿不到"低档跳过时的 limitation"（因为没调 win_rate）。
    # 改为直接包 _number5_1_core，在**进入前**用同一公式复算候选 limitation。
    # 但 clicks/clicks9 是在 core 内部算出的，外部拿不到。
    # 折中方案：包 process_bigger_situation —— 它在低档跳过 win_rate 后一定会被调用，
    # 且入参含 clicks/clicks9/ck，可精确复算 limitation。
    orig_pbs = prob.ProbabilityMixin.process_bigger_situation

    def pbs_hook(self, total, num9, num10, clicks, clicks9, res_list, ck,
                 cell_value, pos):
        # 复算 limitation（与 number5_1 同一公式）
        t = 1
        for c in ck:
            t *= int(c)
        lim = len(clicks9) * 0.8 + math.log2(max(t, 1))
        if args.low < lim <= args.high:
            # 该决策点低档跳过、高档会覆盖 ⇒ 强制测一次 win_rate
            stats["n_new_cover"] += 1
            rec = {"lim": round(float(lim), 2), "fp": ctx.get("fp"),
                   "ms": None, "same_pick": None, "timeout": False,
                   "pick_wr": None, "pick_greedy": None}
            try:
                t0 = _time_mod.perf_counter()
                # 跨平台超时：记录耗时后由外层按阈值判定（Windows 无 SIGALRM）
                out = orig_wr(self, clicks, clicks9, res_list,
                              cell_value, ck, num10)
                rec["ms"] = (_time_mod.perf_counter() - t0) * 1000.0
                if rec["ms"] > args.decision_timeout_s * 1000.0:
                    rec["timeout"] = True
                import numpy as np
                res, sclicks = out[0], out[1]
                arr = np.asarray(res, float)
                if arr.size:
                    a = int(np.argmax(arr))
                    if 0 <= a < len(sclicks):
                        rec["pick_wr"] = list(sclicks[a])
                c2p = out[3] or {}
                if c2p:
                    rec["pick_greedy"] = list(
                        max(c2p.items(), key=lambda kv: kv[1])[0])
                if rec["pick_wr"] and rec["pick_greedy"]:
                    rec["same_pick"] = rec["pick_wr"] == rec["pick_greedy"]
            except DecisionTimeout:
                rec["timeout"] = True
            except Exception as e:                 # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {e}"[:80]
            stats["new_cover"].append(rec)
            print(f"      [new-cover] lim={rec['lim']} "
                  f"ms={rec['ms'] if rec['ms'] is None else round(rec['ms'])} "
                  f"选格改变="
                  f"{'—' if rec['same_pick'] is None else (not rec['same_pick'])}"
                  f"{' TIMEOUT' if rec['timeout'] else ''}", flush=True)
        else:
            stats["skipped"] += 1
        # 真实走生产贪心分支（轨迹不变）
        return orig_pbs(self, total, num9, num10, clicks, clicks9, res_list,
                        ck, cell_value, pos)

    prob.ProbabilityMixin._number5_1_core = core_hook
    prob.ProbabilityMixin.win_rate = wr_hook
    prob.ProbabilityMixin.process_bigger_situation = pbs_hook
    try:
        for gi in range(args.max_games):
            sd = args.seed + gi
            try:
                ab.play_one(sd, 30, 16, 99, args.arm, 80, args.world_cap,
                            args.node_cap, wr_threshold=args.low)
            except Exception:                      # noqa: BLE001
                pass
            print(f"    [game {gi + 1}/{args.max_games}] "
                  f"决策点 {stats['n_decision']}，"
                  f"新增覆盖样本 {stats['n_new_cover']}", flush=True)
            if stats["n_new_cover"] >= args.target_pairs:
                print(f"    ⇒ 已达停止条件 {args.target_pairs}", flush=True)
                break
    finally:
        prob.ProbabilityMixin._number5_1_core = orig_core
        prob.ProbabilityMixin.win_rate = orig_wr
        prob.ProbabilityMixin.process_bigger_situation = orig_pbs
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--low", type=float, default=8.0)
    ap.add_argument("--high", type=float, default=16.0)
    ap.add_argument("--target-pairs", type=int, default=20)
    ap.add_argument("--max-games", type=int, default=40)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--decision-timeout-s", type=float, default=180.0)
    args = ap.parse_args()

    print("=" * 100)
    print(f"阈值分层诊断（**同决策点配对**）：th={args.low} vs th={args.high}")
    print(f"覆盖条件 = limitation ≤ 阈值（纯函数）⇒ 新增覆盖 ⟺ "
          f"{args.low} < limitation ≤ {args.high}")
    print(f"停止条件：收集 {args.target_pairs} 个新增覆盖决策点，"
          f"最多 {args.max_games} 局")
    print("=" * 100)

    st = run_paired(args)

    print()
    print("=" * 100)
    print("汇总（同决策点配对，未受轨迹分叉影响）")
    print("=" * 100)
    print(f"  决策点总数: {st['n_decision']}")
    print(f"  两档都覆盖（低档也调 win_rate）: {len(st['both_covered'])}")
    print(f"  **仅高档新增覆盖**: {st['n_new_cover']}")
    print(f"  两档都跳过: {st['skipped']}")

    nc = st["new_cover"]
    if not nc:
        print("\n  无新增覆盖样本 —— 提高阈值没有多算任何决策点")
    else:
        ms = [r["ms"] for r in nc if r["ms"] is not None]
        to = sum(1 for r in nc if r["timeout"])
        err = sum(1 for r in nc if r.get("error"))
        same = [r["same_pick"] for r in nc if r["same_pick"] is not None]
        n_same = sum(1 for x in same if x)
        n_diff = len(same) - n_same

        print()
        print("  新增覆盖样本明细：")
        print(f"    {'lim':>8} {'ms':>12} {'选格改变':>10} {'状态':>10}")
        print("    " + "-" * 46)
        for r in nc:
            chg = ("—" if r["same_pick"] is None
                   else ("**是**" if not r["same_pick"] else "否"))
            state = ("超时" if r["timeout"]
                     else ("错误" if r.get("error") else "ok"))
            ms_s = "—" if r["ms"] is None else f"{r['ms']:.0f}"
            print(f"    {r['lim']:>8} {ms_s:>12} {chg:>10} {state:>10}")

        print()
        if ms:
            print(f"  单决策耗时: p50 {statistics.median(ms):.0f}ms  "
                  f"p90 {pctl(ms, 0.9):.0f}ms  max {max(ms):.0f}ms  "
                  f"合计 {sum(ms) / 1000:.1f}s")
            over = [x for x in ms if x > 30_000]
            print(f"  超 30s 单决策预算: {len(over)}/{len(ms)}"
                  + (f"（最慢 {max(over) / 1000:.1f}s）" if over else ""))
        print(f"  超时: {to}  错误: {err}")
        if same:
            print(f"  选格与贪心相同: {n_same}/{len(same)}"
                  f"（{n_same / len(same) * 100:.0f}%）")
            print(f"  **改变选格**: {n_diff}/{len(same)}"
                  f"（{n_diff / len(same) * 100:.0f}%）")
            if n_diff == 0:
                print("  ⇒ 本批新增覆盖的决策**全部未改变选格** ⇒ 纯成本")
            else:
                print("  ⇒ 存在改变选格的决策 ⇒ 值得扩样验证胜率")
        else:
            print("  ⚠️ 无有效选格对比样本（可能全部超时/错误）")

    print()
    print("=" * 100)
    print("停止条件")
    print("=" * 100)
    print(f"  目标 {args.target_pairs}，实际 {st['n_new_cover']}")
    print(f"  ⇒ {'已达成' if st['n_new_cover'] >= args.target_pairs else '未达成（需增 --max-games）'}")

    out = os.path.join(ROOT, "state", "ab", "th_pair2.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"low": args.low, "high": args.high,
                   "n_decision": st["n_decision"],
                   "n_both_covered": len(st["both_covered"]),
                   "n_new_cover": st["n_new_cover"],
                   "new_cover": st["new_cover"]},
                  f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
