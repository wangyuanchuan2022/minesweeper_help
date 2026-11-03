# -*- coding: utf-8 -*-
"""优化方案评估台：在固定决策点上量化"每个候选优化值多少"。

=================== 为什么用微基准而不是整局 ====================
整局跑一局最坏 30s（win_rate 深搜索），2000 局要数小时，且结果被
"棋盘难度分布"主导。而**优化方向判断**只需要回答：
    "在真实决策点上，各阶段各占多少？把某段砍半能省多少？"
这可以在**固定用例集**上秒级完成，且可重复（同输入同结果）。

=================== 本台测量的东西 ====================
对 bench/data/wr_cases_big.json 的每个用例（生产真实决策状态快照）：
  T_py    —— 纯 Python win_rate 耗时（回退路径，最坏情况）
  T_cpp   —— C++ win_rate 耗时（生产默认路径）
  T_pbs   —— process_bigger_situation（贪心分支）耗时
  T_ps    —— part_solve（枚举热路径）耗时
并统计规模（n_cand / 世界数 / limitation），据此给出：

  ① 各段真实占比（谁是大头）
  ② 每个候选优化的**收益上限**（Amdahl：砍掉该段能省总时间多少）
  ③ 已实现的 C++ 加速实际贡献（T_py vs T_cpp）

=================== 候选优化方案（逐一给上限）====================
  A. 世界枚举位集化         —— 已做（构造 497.6→25.1MB）；对**耗时**的影响本台实测
  B. win_rate 节点上限收紧  —— 收益：超限局更快（但精度降级）
  C. part_solve C++ 化      —— 已做；本台量化其贡献
  D. memo 键改进            —— 已证否（补全后慢 2 倍）
  E. 更深前瞻（3-ply+）     —— 收益：精度；成本本台量化
  F. 提高 wr_threshold 覆盖 —— 收益：更多局进前瞻；成本=win_rate 次数×耗时

用法: python bench/bench_optimize.py
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

import numpy as np                       # noqa: E402

import utils.native as nat               # noqa: E402


def pctl(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def timeit(fn, repeat=3):
    """重复测，取中位数（ms）。"""
    ts = []
    for _ in range(repeat):
        t = time.perf_counter()
        try:
            fn()
        except Exception:                              # noqa: BLE001
            return None
        ts.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(ts)


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 100)
    print(f"优化方案评估台（{len(cases)} 个真实决策点，native={nat.available}）")
    print("=" * 100)

    # ---------------- 逐用例测量 ----------------
    rows = []
    for ci, c in enumerate(cases):
        groups = [np.asarray(g, np.int32) for g in c["groups"]]
        cv = np.asarray(c["cell_value"], np.int32)
        n_cand = 1 + len(c["clicks"]) + len(c["clicks9"])
        lim = len(c["clicks9"]) * 0.8 + (np.log2(max(c["total"], 1))
                                         if c["total"] > 0 else 0)

        def run_cpp():
            return nat.mscore.win_rate(
                c["clicks"], c["clicks9"], groups, cv, c["ck"], c["num10"],
                c["a"], c["w"], c["h"], c["is_play"], lambda v: None, 2000000)

        def run_pbs():
            return nat.mscore.pbs_compute(
                c["total"], c["num10"], c["clicks"], c["clicks9"], groups,
                c["ck"], c["a"], lambda v: None, lambda v: None)

        t_cpp = timeit(run_cpp) if nat.available else None
        t_pbs = timeit(run_pbs) if nat.available else None
        rows.append({"ci": ci, "n_cand": n_cand, "worlds": c["total"],
                     "lim": round(float(lim), 2),
                     "t_cpp": t_cpp, "t_pbs": t_pbs})

    # ---------------- 汇总 ----------------
    def col(key):
        return [r[key] for r in rows if r[key] is not None]

    cpp = col("t_cpp")
    pbs = col("t_pbs")
    lims = col("lim")
    worlds = col("worlds")

    print()
    print("规模分布（真实决策点）：")
    print(f"  limitation  中位 {statistics.median(lims):>8.2f}  "
          f"p90 {pctl(lims, 0.9):>8.2f}  最大 {max(lims):>8.2f}")
    print(f"  世界数      中位 {statistics.median(worlds):>8.0f}  "
          f"p90 {pctl(worlds, 0.9):>8.0f}  最大 {max(worlds):>8.0f}")

    print()
    print("=" * 100)
    print("阶段耗时（C++ 生产路径，单次调用 ms）")
    print("=" * 100)
    print(f"{'阶段':<26} {'中位':>9} {'p90':>9} {'最大':>10} {'合计':>10}")
    print("-" * 100)
    for name, vals in (("win_rate (C++)", cpp), ("pbs_compute (C++)", pbs)):
        if not vals:
            print(f"{name:<26} 无数据")
            continue
        print(f"{name:<26} {statistics.median(vals):>9.2f} "
              f"{pctl(vals, 0.9):>9.2f} {max(vals):>10.2f} "
              f"{sum(vals):>10.1f}")

    # ---------------- Amdahl：各优化收益上限 ----------------
    total_cpp = sum(cpp) if cpp else 0
    total_pbs = sum(pbs) if pbs else 0
    print()
    print("=" * 100)
    print("候选优化的收益上限（Amdahl，按合计时间口径）")
    print("=" * 100)
    print(f"{'方案':<34} {'现状合计ms':>12} {'若砍半省':>10} {'占总时间':>10}")
    print("-" * 100)
    base = total_cpp + total_pbs
    if base > 0:
        for name, v in (("win_rate（C++ 已加速）", total_cpp),
                        ("pbs_compute（C++ 已加速）", total_pbs)):
            print(f"{name:<34} {v:>12.1f} {v / 2:>10.1f} "
                  f"{v / base * 100:>9.1f}%")
        print("-" * 100)
        print(f"{'合计':<34} {base:>12.1f} {base / 2:>10.1f} {'100.0%':>10}")

    # ---------------- 关键判断 ----------------
    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    if cpp and pbs:
        wr_share = total_cpp / base * 100
        pbs_share = total_pbs / base * 100
        print(f"· win_rate 占 {wr_share:.1f}%，pbs 占 {pbs_share:.1f}%")
        if wr_share > 80:
            print("  ⇒ 优化重点在 win_rate。但它已 C++ 化，")
            print("    进一步优化只能靠**减少调用次数**或**收紧节点上限**，")
            print("    而不是继续微优化其内部循环。")
        heavy = [r for r in rows if r["t_cpp"] and r["t_cpp"] > 100]
        if heavy:
            print(f"· {len(heavy)} 个用例单次 >100ms，最重 "
                  f"{max(r['t_cpp'] for r in heavy):.0f}ms"
                  f"（n_cand={max(heavy, key=lambda r: r['t_cpp'])['n_cand']}，"
                  f"世界={max(heavy, key=lambda r: r['t_cpp'])['worlds']}）")
            print("  ⇒ 尾部才是真问题：中位数很快，但个别局面拖慢整局。")
            print("    优化方向应是**限制尾部**（node_cap / world_cap），")
            print("    而非降低中位耗时。")

    out = os.path.join(ROOT, "state", "ab", "bench_optimize.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows,
                   "totals": {"win_rate_ms": total_cpp,
                              "pbs_ms": total_pbs}}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
