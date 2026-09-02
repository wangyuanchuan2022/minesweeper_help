# -*- coding: utf-8 -*-
"""C++ 原生实现与纯 Python 实现的一致性对比（同进程 A/B）。

通过 utils.native.available 开关切换路径，在相同输入下逐位对比四个热点的输出：
  part_solve / part_solve_single / win_rate / pbs_compute（精确+近似两分支），
另加 number5_1 端到端对比（固定随机种子）。

用法：  python bench/compare_native.py
"""
import os
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_full_solver, load_test_data
from utils import native

FAILURES = []
A = 99  # 30x16 专家局


def check(name, ok, detail=""):
    print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def rows_equal(a, b):
    """解矩阵逐行比较（顺序敏感）。"""
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if not np.array_equal(np.asarray(ra), np.asarray(rb)):
            return False
    return True


def float_list_equal(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if float(x) != float(y):
            return False
    return True


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def run_both(name, fn_native, fn_python, compare, extra=""):
    """同输入跑 C++ 与纯 Python 路径并对比。返回 (native耗时, python耗时)。"""
    native.available = True
    try:
        out_n, dt_n = timed(fn_native)
    except Exception as e:
        native.available = False
        try:
            fn_python()
            check(name, False, f"C++ 抛异常而 Python 正常：{e!r}")
            return 0.0, 0.0
        except Exception as e2:
            check(name, type(e) is type(e2), f"异常类型不一致 {e!r} vs {e2!r}")
            return 0.0, 0.0
    native.available = False
    try:
        out_p, dt_p = timed(fn_python)
    except Exception as e:
        check(name, False, f"Python 抛异常而 C++ 正常：{e!r}")
        return dt_n, 0.0
    ok, detail = compare(out_n, out_p)
    check(name, ok, detail)
    return dt_n, dt_p


def main():
    assert native.available, "native 模块未加载，无法对比"
    print("native:", native.mscore.__file__)
    total_n = total_p = 0.0

    for ds in ("test_33.json", "test_4.json", "test_6.json"):
        data = load_test_data(ds)
        tag = ds.split(".")[0]
        cv = np.array(data["cell_value"], dtype=np.int32)
        clicks_all = [tuple(c) for c in data["clicks"]]
        cs = [tuple(c) for c in data["cs"]]
        num10, num9 = data["num10"], data["num9"]
        solver = make_full_solver(w=cv.shape[1] - 2, h=cv.shape[0] - 2, a=A)

        # ---------------- part_solve：子集 + 全量 ----------------
        for n_clicks in (5, 8, len(clicks_all)):
            clicks = clicks_all[:n_clicks]
            dn, dp = run_both(
                f"{tag} part_solve[{n_clicks}]",
                lambda: solver.part_solve(list(clicks), cv.copy(), num10, num9, cs, _try=False),
                lambda: solver.part_solve(list(clicks), cv.copy(), num10, num9, cs, _try=False),
                lambda o1, o2: (
                    rows_equal(o1[0], o2[0]) and o1[1] == o2[1]
                    and np.array_equal(o1[2], o2[2]),
                    f"num_solve {o1[1]} vs {o2[1]}，行数 {len(o1[0])} vs {len(o2[0])}",
                ),
            )
            total_n += dn
            total_p += dp

        # ---------------- part_solve_single ----------------
        for n_clicks in (5, 8):
            clicks = clicks_all[:n_clicks]
            dn, dp = run_both(
                f"{tag} part_solve_single[{n_clicks}]",
                lambda: solver.part_solve_single(list(clicks), cv.copy(), num10, num9, cs, _try=False),
                lambda: solver.part_solve_single(list(clicks), cv.copy(), num10, num9, cs, _try=False),
                lambda o1, o2: (
                    rows_equal(o1[0], o2[0]) and o1[1] == o2[1]
                    and np.array_equal(o1[2], o2[2]),
                    f"num {o1[1]} vs {o2[1]}，行数 {len(o1[0])} vs {len(o2[0])}",
                ),
            )
            total_n += dn
            total_p += dp

        # 构造 clicks9：未开且不在 clicks 中的格子
        clicks9 = [
            (i, j)
            for j in range(1, solver.h + 1)
            for i in range(1, solver.w + 1)
            if cv[j, i] == 9 and (i, j) not in set(clicks_all)
        ][:8]

        # ---------------- win_rate：单组 + 双组（自定义 a 保证枚举存活） ----------------
        native.available = True
        resA, nA, _ = solver.part_solve(clicks_all[:5], cv.copy(), num10, num9, cs, _try=False)
        resB, nB, _ = solver.part_solve(clicks_all[5:10] if len(clicks_all) >= 10 else clicks_all[:5],
                                        cv.copy(), num10, num9, cs, _try=False)
        if nA == 0 or nB == 0:
            print(f"[SKIP] {tag} win_rate/pbs：分组解数为 0（{nA},{nB}）")
            continue

        mines_min = min(int(r.sum()) for r in resA)
        a_custom = num10 + mines_min + min(3, len(clicks9))
        solver.a = a_custom

        dn, dp = run_both(
            f"{tag} win_rate 单组",
            lambda: solver.win_rate(list(clicks_all[:5]), list(clicks9), [resA], cv.copy(), [nA], num10),
            lambda: solver.win_rate(list(clicks_all[:5]), list(clicks9), [resA], cv.copy(), [nA], num10),
            lambda o1, o2: (
                float_list_equal(o1[0], o2[0])
                and list(map(tuple, map(tuple, o1[1]))) == list(map(tuple, map(tuple, o2[1])))
                and o1[2] == o2[2] and dict(o1[3]) == dict(o2[3]),
                f"res {o1[0][:3]}... vs {o2[0][:3]}...，total {o1[2]} vs {o2[2]}",
            ),
        )
        total_n += dn
        total_p += dp

        cA, cB = clicks_all[:5], (clicks_all[5:10] if len(clicks_all) >= 10 else clicks_all[:5])
        dn, dp = run_both(
            f"{tag} win_rate 双组",
            lambda: solver.win_rate(list(cA + cB), list(clicks9), [resA, resB], cv.copy(), [nA, nB], num10),
            lambda: solver.win_rate(list(cA + cB), list(clicks9), [resA, resB], cv.copy(), [nA, nB], num10),
            lambda o1, o2: (
                float_list_equal(o1[0], o2[0])
                and list(map(tuple, map(tuple, o1[1]))) == list(map(tuple, map(tuple, o2[1])))
                and o1[2] == o2[2] and dict(o1[3]) == dict(o2[3]),
                f"res {o1[0][:3]}... vs {o2[0][:3]}...，total {o1[2]} vs {o2[2]}",
            ),
        )
        total_n += dn
        total_p += dp

        # ---------------- pbs_compute：真实 total（自动分支） + 强制近似 ----------------
        solver.a = A
        prod_total = nA * nB

        def pbs_call(total_override=None):
            total = total_override if total_override is not None else prod_total
            def _run():
                return solver._pbs_compute_python(
                    total, num10, list(cA + cB), list(clicks9), [resA, resB], [nA, nB])
            def _native():
                return native.mscore.pbs_compute(
                    total, num10, list(cA + cB), list(clicks9),
                    native.as_groups([resA, resB]), [nA, nB], solver.a,
                    solver._throttled_pv_signal_emit, solver.Visible_signal.emit)
            return _native, _run

        def cmp_pbs(o1, o2):
            r1, m1, t1 = o1
            r2, m2, t2 = o2
            arr1, arr2 = np.asarray(r1), np.asarray(r2)
            ok = (
                np.array_equal(arr1, arr2)
                and arr1.dtype == arr2.dtype
                and float(m1) == float(m2)
                and float(t1) == float(t2)
                and type(m1) is type(m2)
                and (type(t1) is type(t2) or (isinstance(t1, float) and isinstance(t2, float)))
            )
            return ok, f"res {arr1[:4]} vs {arr2[:4]}，mine {m1} vs {m2}，total {t1} vs {t2}"

        nfn, pfn = pbs_call()
        dn, dp = run_both(f"{tag} pbs_compute 真实total={prod_total}", nfn, pfn, cmp_pbs)
        total_n += dn
        total_p += dp

        nfn, pfn = pbs_call(total_override=10001)
        dn, dp = run_both(f"{tag} pbs_compute 近似分支", nfn, pfn, cmp_pbs)
        total_n += dn
        total_p += dp

    print(f"\n累计耗时（本脚本各场景）：C++ {total_n:.3f}s  Python {total_p:.3f}s")

    # ---------------- number5_1 端到端（固定种子；关阈值放宽保证决策可比） ----------------
    if "--skip-e2e" not in sys.argv:
        for ds in ("test_33.json",):
            data = load_test_data(ds)
            cv = np.array(data["cell_value"], dtype=np.int32)

            def run_e2e(use_native):
                native.available = use_native
                np.random.seed(20240707)
                s = make_full_solver(w=cv.shape[1] - 2, h=cv.shape[0] - 2, a=A)
                t0 = time.perf_counter()
                out = s.number5_1(cv.copy())
                dt = time.perf_counter() - t0
                if isinstance(out, tuple):
                    pos = out[0]
                    norm = ("tuple", [tuple(p) for p in pos], round(float(out[1]), 12),
                            round(float(out[2]), 12))
                else:
                    norm = ("board", np.asarray(out).tolist())
                return norm, dt

            try:
                os.environ["MSW_NATIVE_TUNE"] = "0"  # 阈值保持原值 → 决策路径一致
                (out_n, dt_n), (out_p, dt_p) = run_e2e(True), run_e2e(False)
                check(f"{ds} number5_1 端到端", out_n == out_p,
                      f"{str(out_n)[:120]} vs {str(out_p)[:120]}")
                print(f"    number5_1 耗时：C++ {dt_n:.2f}s  Python {dt_p:.2f}s")

                # 放宽阈值后的冒烟（win_rate 覆盖更多局面，只验证可正常完成）
                os.environ.pop("MSW_NATIVE_TUNE")
                np.random.seed(20240707)
                s = make_full_solver(w=cv.shape[1] - 2, h=cv.shape[0] - 2, a=A)
                t0 = time.perf_counter()
                s.number5_1(cv.copy())
                dt_t = time.perf_counter() - t0
                print(f"    number5_1（阈值放宽后）：{dt_t:.2f}s，无异常")
            except Exception:
                traceback.print_exc()
                check(f"{ds} number5_1 端到端", False, "执行异常")
            finally:
                os.environ.pop("MSW_NATIVE_TUNE", None)
                native.available = True

    print()
    if FAILURES:
        print("FAILED:", len(FAILURES))
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL CONSISTENT")


if __name__ == "__main__":
    main()
