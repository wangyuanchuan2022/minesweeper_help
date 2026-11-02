# -*- coding: utf-8 -*-
"""验证：C++ 与纯 Python 回退路径在边界输入下是否真的等价。

线索来源：bench/bench_winrate_scaling.py 里 win_rate 在 clicks9 很小时，
C++ 报 "division by zero"，回退到 Python 后报 "IndexError: too many indices"。
如果两者失败方式不同，说明「自动回退保证结果一致」这条 README 承诺在边界上不成立
（README:49-51 明确承诺 "运行期任何异常时自动回退到纯 Python 原实现，功能与结果完全一致"）。

本脚本直接对比两条路径在同一输入下的行为（返回值或异常类型）。
这是**不依赖 perf 的正确性验证**。

用法: python bench/verify_fallback_parity.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native


def call_both(fn_name, solver_factory, args_factory):
    """同一输入分别走 C++ 与纯 Python，返回两边的 (结果摘要 or 异常)。"""
    out = {}
    for use_native in (True, False):
        native.available = use_native
        label = "C++" if use_native else "Python"
        try:
            s = solver_factory()
            res = getattr(s, fn_name)(*args_factory(s))
            out[label] = ("ok", summarize(res))
        except Exception as e:
            out[label] = ("err", f"{type(e).__name__}: {e}")
    native.available = True
    return out


def summarize(res):
    try:
        if isinstance(res, tuple):
            parts = []
            for x in res:
                if isinstance(x, list):
                    parts.append(f"list[{len(x)}]")
                elif isinstance(x, dict):
                    parts.append(f"dict[{len(x)}]")
                elif isinstance(x, np.ndarray):
                    parts.append(f"arr{x.shape}")
                else:
                    parts.append(repr(x)[:24])
            return "(" + ", ".join(parts) + ")"
        return repr(res)[:80]
    except Exception:
        return "<unsummarizable>"


def main():
    print(f"native 初始可用 = {native.available}")
    print("=" * 74)
    d = load_test_data("test_33.json")
    cv = np.array(d["cell_value"], np.int32)
    clicks = [tuple(c) for c in d["clicks"]]
    cs = [tuple(c) for c in d["cs"]]

    def factory():
        s = make_full_solver(w=30, h=16, a=99)
        s.is_play = False
        return s

    # 先用 C++ 产出一个合法分组结果（两条路径都需要它作为输入）
    native.available = True
    res_a, n_a, canopen = factory().part_solve(clicks[:10], cv.copy(), d["num10"],
                                              d["num9"], cs, _try=False)
    native.available = True
    print(f"准备数据：分组解数 = {n_a}")

    all9 = [(i, j) for j in range(1, 17) for i in range(1, 31)
            if cv[j, i] == 9 and (i, j) not in set(clicks)]

    cases = []
    for k in (0, 1, 2, 3):
        cases.append((f"win_rate clicks9={k}", "win_rate",
                      lambda s, k=k: (list(clicks[:10]), list(all9[:k]), [res_a],
                                      cv.copy(), [n_a], d["num10"])))

    for label, fname, args_f in cases:
        r = call_both(fname, factory, args_f)
        c, p = r["C++"], r["Python"]
        same = (c[0] == p[0] and c[1] == p[1])
        print(f"\n{label}")
        print(f"  C++   : {c[0]}  {c[1]}")
        print(f"  Python: {p[0]}  {p[1]}")
        if not same:
            print(f"  → ❌ 两条路径行为不同（README 承诺『结果完全一致』不成立）")
        elif c[0] == "err":
            print(f"  → ⚠️ 两边都失败且异常不同类/同类，属边界未处理")
        else:
            print(f"  → ✅ 一致")

    print("\n" + "=" * 74)
    print("说明：本检验只判定『两条路径是否一致』，不判定『哪条对』。")
    print("      若两边异常类型不同，则回退机制本身会改变可观察行为。")


if __name__ == "__main__":
    main()
