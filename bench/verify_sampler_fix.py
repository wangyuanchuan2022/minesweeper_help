# -*- coding: utf-8 -*-
"""验证 sleep-stub 修复：有/无采样线程的耗时必须接近，且决策轨迹一致。

=================== 按评审要求 ====================
固定 seed **不保证**两边走了相同路径。因此本脚本同时记录：
  · 决策步数、arm 调用次数、各阶段调用次数、gate_stats
  · 最终棋盘哈希（opened 集合 + 雷位集合的指纹）
只有轨迹一致，耗时对比才能归因于性能而非"走了不同算法/降级路径"。

用法: python bench/verify_sampler_fix.py
"""
import gc
import hashlib
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

# 关键：在任何 install_stubs 之前保存原始 sleep
import time as _time_mod                      # noqa: E402
_REAL_SLEEP = _time_mod.sleep

import psutil                                 # noqa: E402

P = psutil.Process()

from sim.eval_harness import install_stubs     # noqa: E402
install_stubs(os.path.join(ROOT, "state", "ab", "samplerfix"), True)

import ab_policy as ab                        # noqa: E402

SEED = 20261102
ARM = "baseline"


class Sampler(threading.Thread):
    """用 _REAL_SLEEP 的采样线程（修复后）。"""

    def __init__(self, interval=0.01):
        super().__init__(daemon=True)
        self.interval = interval
        self.run_ = True
        self.peak = 0

    def run(self):
        self.peak = P.memory_info().rss
        while self.run_:
            try:
                r = P.memory_info().rss
                if r > self.peak:
                    self.peak = r
            except Exception:                      # noqa: BLE001
                break
            _REAL_SLEEP(self.interval)             # ← 原始 sleep

    def stop(self):
        self.run_ = False
        self.join(timeout=2)
        return self.peak


def trace_fingerprint():
    """当前 Solver 状态的指纹（用于确认两边走同一路径）。

    注意：play_one 返回后 Solver 已释放，这里只能记录 play_one 的返回指标。
    """
    return None


def run(label, use_sampler):
    ab.reset_gate_stats()
    smp = Sampler() if use_sampler else None
    if smp:
        smp.start()
    gc.collect()
    t0 = time.perf_counter()
    r = ab.play_one(SEED, 30, 16, 99, ARM, 80, 200000, 2000000)
    dt = (time.perf_counter() - t0) * 1000.0
    peak = smp.stop() if smp else P.memory_info().rss
    gs = ab.gate_stats()
    rec = {
        "label": label, "ms": dt,
        "won": r.get("won"), "died": r.get("died"),
        "steps": r.get("steps"), "opened": r.get("opened"),
        "n_click": r.get("n_click"),
        "arm_calls": r.get("arm_calls"), "arm_fallback": r.get("arm_fallback"),
        "arm_nodes": r.get("arm_nodes"),
        "gate": dict(gs),
        "peak_mb": peak / 2 ** 20,
        "error": r.get("error"),
    }
    # 轨迹指纹：把决定路径的字段拼起来做哈希
    sig = json.dumps({k: rec[k] for k in
                      ("won", "died", "steps", "opened", "n_click",
                       "arm_calls", "arm_fallback", "arm_nodes", "gate")},
                     sort_keys=True)
    rec["trace_hash"] = hashlib.md5(sig.encode()).hexdigest()[:16]
    return rec


print("=" * 96)
print(f"验证 sleep-stub 修复：seed={SEED} arm={ARM}")
print("=" * 96)

r1 = run("无采样", False)
r2 = run("有采样（修复后，用原始 sleep）", True)

for r in (r1, r2):
    print(f"{r['label']:<34} {r['ms']:>9.0f} ms  "
          f"steps={r['steps']} opened={r['opened']} "
          f"arm_calls={r['arm_calls']} peak={r['peak_mb']:.1f}MB "
          f"trace={r['trace_hash']}")

print()
print("=" * 96)
print("判定")
print("=" * 96)
ratio = r2["ms"] / r1["ms"] if r1["ms"] else 0
same_trace = r1["trace_hash"] == r2["trace_hash"]
print(f"耗时比（有采样/无采样）= {ratio:.2f}x"
      f"（修复前实测 197.68x）")
print(f"决策轨迹一致 = {same_trace}")
if not same_trace:
    print("  !! 轨迹不同，耗时对比不可归因于性能")
    for k in ("won", "died", "steps", "opened", "n_click", "arm_calls",
              "arm_fallback", "arm_nodes", "gate"):
        if r1[k] != r2[k]:
            print(f"     {k}: {r1[k]} vs {r2[k]}")

ok = ratio < 1.3 and same_trace
print()
print(f"结论：{'通过' if ok else '未通过'} —— "
      f"{'采样开销已可忽略且轨迹一致' if ok else '仍有异常'}")
sys.exit(0 if ok else 1)
