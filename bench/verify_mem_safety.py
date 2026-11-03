# -*- coding: utf-8 -*-
"""最终内存验收：用真实 RSS（不是 tracemalloc）逐局判定。

=================== 为什么不用 tracemalloc ====================
tracemalloc 统计的是"Python 分配器记账量"，有两个问题：
  · 跨决策点会把**历史累计分配**也算进 current，
    导致"峰值"读数虚高（实测同一 case 用 tracemalloc 报 485MB，
    而真实 RSS 增量只有几十 MB）
  · 不包含 C 扩展（numpy/cv2）用 malloc 直接要的内存
判据必须用**进程 RSS**，这也是"会不会把机器顶爆"的唯一真实指标。

=================== 判定标准 ====================
  · 跨局泄漏：局末 RSS 是否随局数单调上升（且 Solver 对象数增长）
  · 单局峰值：局内峰值 RSS 是否超过阈值（默认 1500MB = 与生产预算同口径）
  · 外推：按前 N 局的斜率外推 2000 局是否安全

用法:
  python bench/verify_mem_safety.py --games 30 --arm safety
  python bench/verify_mem_safety.py --games 30 --arm baseline
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

BUDGET_MB = 1536.0
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class Peak(threading.Thread):
    def __init__(self, interval=0.004):
        super().__init__(daemon=True)
        self.p = psutil.Process()
        self.interval = interval
        self.peak = 0
        self.run_ = True

    def run(self):
        self.peak = self.p.memory_info().rss
        while self.run_:
            try:
                r = self.p.memory_info().rss
                if r > self.peak:
                    self.peak = r
            except Exception:                              # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)

    def stop(self):
        self.run_ = False
        self.join(timeout=1)
        return self.peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--arm", default="safety")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "memsafe"), True)

    import ab_policy as ab

    print("=" * 100)
    print(f"内存安全验收（真实 RSS）：arm={args.arm}，{args.games} 局")
    print("=" * 100)
    print(f"{'局':>3} {'峰值MB':>9} {'局末MB':>9} {'gc后MB':>8} {'Solver':>7} "
          f"{'耗时s':>7} {'结果':>6}")
    print("-" * 100)

    rows = []
    for i in range(args.games):
        sd = args.seed + i
        pk = Peak()
        pk.start()
        t0 = time.time()
        r = ab.play_one(sd, 30, 16, 99, args.arm, 80, args.world_cap,
                        args.node_cap)
        dt = time.time() - t0
        peak = pk.stop()
        gc.collect()
        end = psutil.Process().memory_info().rss
        sov = sum(1 for o in gc.get_objects()
                  if type(o).__name__ == "Solver")
        rows.append({"peak": peak, "end": end, "sov": sov, "sec": dt,
                     "err": bool(r.get("error"))})
        print(f"{i:>3} {peak/2**20:>9.1f} {end/2**20:>9.1f} "
              f"{end/2**20:>8.1f} {sov:>7} {dt:>7.2f} "
              f"{'ERR' if r.get('error') else 'ok':>6}")

    print("-" * 100)
    peaks = [r["peak"] / 2 ** 20 for r in rows]
    ends = [r["end"] / 2 ** 20 for r in rows]
    sovs = [r["sov"] for r in rows]
    peak_max = max(peaks)
    # 用后半段判断趋势（前半段含预热）
    half = max(1, len(ends) // 2)
    trend = (ends[-1] - ends[half]) / max(1, len(ends) - half - 1)

    print(f"单局峰值最大：{peak_max:.1f} MB（预算 {BUDGET_MB:.0f} MB）")
    print(f"局末 RSS：首 {ends[0]:.1f} → 末 {ends[-1]:.1f} MB，"
          f"后半段每局趋势 {trend:+.3f} MB")
    print(f"存活 Solver：{sovs[0]} → {sovs[-1]}（差 {sovs[-1]-sovs[0]:+d}）")
    print()

    check(f"单局峰值不超预算（{peak_max:.1f} < {BUDGET_MB:.0f} MB）",
          peak_max < BUDGET_MB)
    check("无跨局泄漏（后半段局末趋势 ≤ +0.5 MB/局）", trend <= 0.5,
          f"trend={trend:+.3f} MB/局")
    check("Solver 对象不累积（差 ≤ 2）", sovs[-1] - sovs[0] <= 2,
          f"{sovs[0]} → {sovs[-1]}")
    if trend > 0:
        print(f"  外推 2000 局：{ends[-1] + trend * 2000:.0f} MB")
    else:
        print(f"  外推 2000 局：稳定在约 {ends[-1]:.0f} MB（无增长）")

    print()
    print("=" * 100)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print("结论：单局峰值在预算内，无跨局累积 —— 2000 局内存安全。")
    else:
        print("结论：仍不安全，见上方失败项。")


if __name__ == "__main__":
    main()
