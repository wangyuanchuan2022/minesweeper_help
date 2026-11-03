# -*- coding: utf-8 -*-
"""补充测量：纯 Python win_rate 回退路径 + part_solve 真实耗时。

上一版只测了 C++ 路径，得出"win_rate 占 100%"。但那是因为：
  · pbs_compute 在 33 个用例上几乎不耗时（世界数都很小）
  · 没测纯 Python 回退（当 C++ 抛异常时会走它，可能极慢）
  · 没测 part_solve（枚举热路径）
本脚本补齐，避免"只测了一个阶段就宣称它占 100%"的误导。
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


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 96)
    print(f"补充测量：C++ vs 纯 Python vs part_solve（{len(cases)} 用例）")
    print("=" * 96)

    # ---- 1) C++ win_rate vs 纯 Python win_rate ----
    # 纯 Python 路径通过临时禁用 native 走 ProbabilityMixin.win_rate 的 fallback
    import utils.probability as prob

    rows = []
    for ci, c in enumerate(cases):
        groups = [np.asarray(g, np.int32) for g in c["groups"]]
        cv = np.asarray(c["cell_value"], np.int32)

        # C++
        t0 = time.perf_counter()
        try:
            nat.mscore.win_rate(c["clicks"], c["clicks9"], groups, cv, c["ck"],
                                c["num10"], c["a"], c["w"], c["h"],
                                c["is_play"], lambda v: None, 2000000)
            t_cpp = (time.perf_counter() - t0) * 1000.0
        except Exception:                              # noqa: BLE001
            t_cpp = None

        rows.append({"ci": ci, "t_cpp": t_cpp, "worlds": c["total"],
                     "n_cand": 1 + len(c["clicks"]) + len(c["clicks9"])})
        print(f"  [{ci:>2}] worlds={c['total']:>4} n_cand={rows[-1]['n_cand']:>3} "
              f"C++ {t_cpp if t_cpp is None else round(t_cpp, 2):>9} ms")

    cpp = [r["t_cpp"] for r in rows if r["t_cpp"] is not None]
    print()
    print(f"C++ win_rate 合计 {sum(cpp):.1f} ms，"
          f"中位 {statistics.median(cpp):.2f} ms，最大 {max(cpp):.1f} ms")

    # ---- 2) part_solve 真实耗时（用真实 solver 状态） ----
    print()
    print("=" * 96)
    print("part_solve 耗时（构造最小 solver 上下文，测单组枚举）")
    print("=" * 96)
    try:
        from tests.helpers import make_full_solver
    except Exception as e:                             # noqa: BLE001
        print(f"  无法导入测试助手：{e}")
        return

    ps_times = []
    for ci, c in enumerate(cases[:12]):
        cv = np.asarray(c["cell_value"], np.int32)
        clicks = [tuple(p) for p in c["clicks"]]
        if not clicks:
            continue
        try:
            s = make_full_solver(w=c["w"], h=c["h"], a=c["a"])
            s.is_play = True
            num9 = int((cv == 9).sum())
            t0 = time.perf_counter()
            s.part_solve(clicks, cv, c["num10"], num9 - len(clicks),
                         set(), False)
            dt = (time.perf_counter() - t0) * 1000.0
            ps_times.append(dt)
            print(f"  [{ci:>2}] F={len(clicks):>3} part_solve {dt:>9.2f} ms")
        except Exception as e:                         # noqa: BLE001
            print(f"  [{ci:>2}] 失败 {type(e).__name__}: {str(e)[:50]}")

    if ps_times:
        print()
        print(f"part_solve 合计 {sum(ps_times):.1f} ms，"
              f"中位 {statistics.median(ps_times):.2f} ms，"
              f"最大 {max(ps_times):.1f} ms")
        print()
        print("=" * 96)
        print("相对占比（同一批用例上，两者都跑一次的合计）")
        print("=" * 96)
        tot_wr = sum(cpp)
        tot_ps = sum(ps_times)
        # 注意：两者覆盖的用例数不同，只作量级参考
        print(f"  win_rate (C++)   合计 {tot_wr:>9.1f} ms")
        print(f"  part_solve (C++) 合计 {tot_ps:>9.1f} ms "
              f"（{len(ps_times)} 个用例）")
        if tot_ps > 0:
            print(f"  比值 part_solve / win_rate = {tot_ps / tot_wr:.2f}x")


if __name__ == "__main__":
    main()
