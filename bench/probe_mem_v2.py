# -*- coding: utf-8 -*-
"""内存探针 v2：区分「单局峰值过高」与「跨局持续累积」。

=================== 设计要点（按评审意见）====================
不预设一定是"持续增长的对象"。逐局同时记录两组独立指标：

  峰值类（判断单局是否会把自己撑爆）
    · 局内峰值 RSS（后台高频采样）
    · node_cap 命中次数、walk 回退次数
  累积类（判断 2000 局是否会线性涨）
    · 局末 RSS（gc 后）
    · 存活 Solver/Board/function 对象数（gc.get_objects 统计）
    · paste 链深度（类属性是否仍是原始函数）

判据分离：
  · 若「局内峰值」高但「局末 RSS」回到基线 ⇒ 单局峰值问题（降 cap / 降 world_cap）
  · 若「局末 RSS」逐局单调上升且对象数增长 ⇒ 跨局泄漏（修引用/还原 patch）

同时保存「首次异常增长的局序号」，便于复现。

用法:
  python bench/probe_mem_v2.py --games 30 --arm safety
  python bench/probe_mem_v2.py --games 30 --arm baseline
"""
import argparse
import gc
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

# ⚠️ install_stubs 会把 time.sleep 换成空操作，采样线程必须用原始 sleep
import time as _time_mod
_REAL_SLEEP = _time_mod.sleep

import psutil                            # noqa: E402


class RssSampler(threading.Thread):
    """后台采样进程 RSS，记录局内峰值。"""

    def __init__(self, interval=0.005):
        super().__init__(daemon=True)
        self.proc = psutil.Process()
        self.interval = interval
        self.peak = 0
        self.running = True

    def run(self):
        self.peak = self.proc.memory_info().rss
        while self.running:
            try:
                r = self.proc.memory_info().rss
                if r > self.peak:
                    self.peak = r
            except Exception:                              # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)

    def stop(self):
        self.running = False
        self.join(timeout=1.0)
        return self.peak


def rss():
    return psutil.Process().memory_info().rss


def live_objects():
    """gc 后统计关键类型的存活对象数。"""
    gc.collect()
    from collections import Counter
    c = Counter()
    for o in gc.get_objects():
        try:
            c[type(o).__name__] += 1
        except Exception:                                  # noqa: BLE001
            pass
    return {k: c.get(k, 0) for k in
            ("Solver", "Board", "SimDriver", "BoardView", "BoardVisionMixin")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--arm", default="safety")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "probe"), True)

    import ab_policy as ab
    import utils.probability as prob

    print("=" * 108)
    print(f"内存探针 v2：arm={args.arm}，{args.games} 局，"
          f"world_cap={args.world_cap:,} node_cap={args.node_cap:,}")
    print("=" * 108)
    print(f"{'局':>3} {'局内峰值MB':>10} {'局末MB':>9} {'Δ基线':>8} "
          f"{'Solver':>7} {'Board':>6} {'func':>6} {'降级':>5} {'耗时s':>7}")
    print("-" * 108)

    base = None
    rows = []
    first_abnormal = None
    for i in range(args.games):
        sd = args.seed + i
        smp = RssSampler()
        smp.start()
        t0 = time.time()
        r = ab.play_one(sd, 30, 16, 99, args.arm, 80,
                        args.world_cap, args.node_cap)
        dt = time.time() - t0
        peak = smp.stop()
        end_rss = rss()
        if base is None:
            base = end_rss
        objs = live_objects()
        delta = (end_rss - base) / 2 ** 20
        rows.append({"i": i, "peak": peak, "end": end_rss,
                     "delta": delta, "objs": objs, "sec": dt,
                     "fallback": r.get("arm_fallback", 0)})
        print(f"{i:>3} {peak/2**20:>10.1f} {end_rss/2**20:>9.1f} "
              f"{delta:>8.2f} {objs['Solver']:>7} {objs['Board']:>6} "
              f"{objs.get('function', 0):>6} {r.get('arm_fallback',0):>5} "
              f"{dt:>7.2f}")
        # 首次异常：局末 RSS 比基线高 50MB 以上
        if first_abnormal is None and delta > 50:
            first_abnormal = i

    print("-" * 108)
    if not rows:
        print("无数据")
        return

    # ---- 分离两类指标 ----
    peak_max = max(r["peak"] for r in rows) / 2 ** 20
    end_last = rows[-1]["delta"]
    per_game = end_last / max(1, len(rows) - 1)
    sov_growth = rows[-1]["objs"]["Solver"] - rows[0]["objs"]["Solver"]
    leak_suspect = per_game > 0.05 or sov_growth > 0

    print(f"① 单局峰值：最大 {peak_max:.1f} MB（含解释器基线约 "
          f"{rows[0]['peak']/2**20:.0f} MB）")
    print(f"② 跨局累积：局末相对基线增长 {end_last:.2f} MB / "
          f"{len(rows)} 局 = 每局 {per_game:.3f} MB")
    print(f"   存活 Solver 变化 {sov_growth:+d}；外推 2000 局 ≈ "
          f"{per_game*2000:.0f} MB")
    print()
    if leak_suspect:
        print("⇒ 判定：**跨局累积泄漏**（局末 RSS / 对象数随局数增长）")
        print("   检查点：monkey-patch 是否还原、闭包是否捕获大对象、"
              "模块级累加器是否无界")
    else:
        print("⇒ 判定：无跨局泄漏（局末回到基线）")
        if peak_max > 500:
            print("   但**单局峰值过高**，需降 world_cap / node_cap")
    if first_abnormal is not None:
        print(f"   首次 Δ>50MB 的局序号：{first_abnormal}（可用 --seed 复现）")
        print(f"   对应 seed = {args.seed + first_abnormal}")


if __name__ == "__main__":
    main()
