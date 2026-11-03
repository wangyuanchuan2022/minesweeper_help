# -*- coding: utf-8 -*-
"""各算法臂的成本测量：单决策耗时、节点数、内存估算。

=================== 为什么必须测 ====================
胜率提升若以 100 倍代价换来，在生产（每步有 3s 预算）里就不可用。
本脚本在同一批固定用例上测各臂的**单决策成本**，与 bench/summarize_arms.py
的胜率读数配对使用，才能判断"值不值得接入"。

口径：
  · 单决策耗时 = 从"给定状态"到"选出候选格"的墙钟时间（含免费推理饱和）
  · 节点数 = 引擎内部递归节点计数（衡量计算量，与机器无关）
  · 内存 = 状态数 × 每状态近似字节（memo 条目）估算
  · 生产 win_rate 的耗时直接取 gold 里记录的 ms（真实 C++ 实现）

用法: python bench/cost_arms.py
"""
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from arms_lookahead import ExactEngine        # noqa: E402
from oracle_bruteforce import Budget          # noqa: E402

ARMS = [("safety", 0, "纯 safety"),
        ("expand", 0, "期望展开"),
        ("ply", 2, "2-ply 前瞻"),
        ("ply", 3, "3-ply 前瞻"),
        ("exact", 0, "精确最优(oracle)")]


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(ROOT, "bench", "data", "wr_before_big.json"),
              encoding="utf-8") as f:
        gold = json.load(f)["results"]

    print("=" * 96)
    print(f"各算法臂成本测量（{len(cases)} 个用例，单决策口径）")
    print("=" * 96)

    res = {label: {"ms": [], "nodes": [], "fail": 0} for _, _, label in ARMS}
    wr_ms = []
    for r in gold:
        if "ms_median" in r:
            wr_ms.append(float(r["ms_median"]))

    for ci, case in enumerate(cases):
        try:
            base = ExactEngine(case, node_cap=600000, time_cap=120)
            if base.n_worlds == 0:
                continue
            wk = frozenset(range(base.n_worlds))
            op = base.opened0
            terms = base.saturate(wk, op)
            # 只在第一个非胜利终端态上计时（模拟一次真实决策）
            target = None
            for wk2, op2, wt in terms:
                if not base.won(op2) and base.candidates(wk2, op2):
                    target = (wk2, op2)
                    break
            if target is None:
                continue
            wk2, op2 = target
            cands = base.candidates(wk2, op2)

            for arm, ply, label in ARMS:
                t0 = time.perf_counter()
                try:
                    if arm == "exact":
                        v = max(base.after_click(c, wk2, op2) for c in cands)
                    else:
                        base.pick(cands, wk2, op2, arm, ply)
                    ms = (time.perf_counter() - t0) * 1000.0
                    res[label]["ms"].append(ms)
                    res[label]["nodes"].append(base.nodes)
                except Budget:
                    res[label]["fail"] += 1

            print(f"[{ci:2d}] worlds={base.n_worlds:3d} 候选={len(cands):2d} "
                  f"节点={base.nodes:6d}  " +
                  "  ".join(f"{lb}={res[lb]['ms'][-1]:.2f}ms"
                            for _, _, lb in ARMS if res[lb]["ms"]))
        except Budget as e:
            print(f"[{ci:2d}] 预算淘汰：{e}")
        except Exception as e:                          # noqa: BLE001
            print(f"[{ci:2d}] 错误 {type(e).__name__}: {e}")

    print()
    print("=" * 96)
    print(f"{'臂':<18} {'n':>4} {'中位数ms':>10} {'p90 ms':>9} {'最大ms':>9} "
          f"{'相对safety倍率':>14}")
    print("-" * 96)
    base_med = None
    for _, _, label in ARMS:
        v = sorted(res[label]["ms"])
        if not v:
            print(f"{label:<18} 无数据")
            continue
        med = statistics.median(v)
        if label == "纯 safety":
            base_med = med
        p90 = v[min(len(v) - 1, int(len(v) * 0.9))]
        ratio = (med / base_med) if base_med and base_med > 0 else float("nan")
        print(f"{label:<18} {len(v):>4} {med:>10.2f} {p90:>9.2f} {max(v):>9.2f} "
              f"{ratio:>14.1f}x")
    if wr_ms:
        s = sorted(wr_ms)
        print(f"{'生产 win_rate(C++)':<18} {len(s):>4} "
              f"{statistics.median(s):>10.2f} {s[min(len(s)-1, int(len(s)*0.9))]:>9.2f} "
              f"{max(s):>9.2f}  （gold 实测，含 C++ 加速）")
    print()
    print("注：前瞻臂是纯 Python 原型，未做任何优化；生产 win_rate 是 C++ 实现。")
    print("    因此倍率只能说明'算法本身的计算量量级'，不代表接入后的真实耗时。")


if __name__ == "__main__":
    main()
