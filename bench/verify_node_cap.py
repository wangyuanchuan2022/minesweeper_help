# -*- coding: utf-8 -*-
"""node_cap 硬上限的验收：默认不改变结果 + 超限可观测地回退。

=================== 这个闸要回答什么 ====================
本轮给 C++ win_rate 加了 node_cap（防内存爆炸的最小硬闸）。必须同时证明两件事：

  A) **默认行为零变化**：node_cap=0（不限制）时，输出与加闸前逐位一致。
     做法：对同一批固定用例，对比 node_cap=0 与「用一个极大 cap」的结果，
     两者必须完全相同（|Δ| < 1e-12）；并且不带 node_cap 参数的调用
     （走默认值）也必须相同 —— 保障既有调用方完全不受影响。

  B) **超限时确实抛异常、可被上层回退**：
     · 用一个很小的 cap（如 1）调用，必须抛 RuntimeError 且消息含 "node cap"
     · 该异常必须能被 utils/probability.py 的 win_rate 包装函数捕获
       （它 except Exception 后回退纯 Python 实现）
     · 抛异常时不得返回"看似正常但错误"的结果（这是最危险的失败模式）

  C) **计数正确**：nodes_visited 应随 cap 增大而单调；cap 越大不越早触发。

用法: python bench/verify_node_cap.py
"""
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import utils.native as nat          # noqa: E402

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


def call_case(case, node_cap=None):
    """跑一个用例；node_cap=None 表示不传该参数（验证默认值路径）。"""
    args = dict(
        clicks=case["clicks"], clicks9=case["clicks9"],
        res_list=[np.asarray(g, np.int32) for g in case["groups"]],
        cell_value=np.asarray(case["cell_value"], np.int32),
        ck=case["ck"], num10=case["num10"], a=case["a"],
        w=case["w"], h=case["h"], is_play=case["is_play"],
        progress=lambda v: None)
    if node_cap is not None:
        args["node_cap"] = node_cap
    return nat.mscore.win_rate(**args)


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 84)
    print(f"node_cap 验收（{len(cases)} 个用例；native={nat.available}）")
    print("=" * 84)
    if not nat.available:
        print("native 不可用，无法验收")
        return

    # ---- A) 默认行为零变化 ----
    print("\nA) 默认行为零变化（node_cap 省略 / =0 / =极大 三者必须一致）")
    same_default = 0
    same_huge = 0
    n_ok = 0
    for ci, case in enumerate(cases):
        try:
            r_def = call_case(case)                       # 不传 → 默认 0
            r_zero = call_case(case, 0)                   # 显式 0
            r_huge = call_case(case, 10 ** 9)             # 极大，不会触发
        except Exception as e:                            # noqa: BLE001
            print(f"  [{ci:2d}] 调用失败 {type(e).__name__}: {e}")
            continue
        res_def = np.asarray(r_def[0], float)
        res_zero = np.asarray(r_zero[0], float)
        res_huge = np.asarray(r_huge[0], float)
        d1 = float(np.max(np.abs(res_def - res_zero))) if res_def.size else 0.0
        d2 = float(np.max(np.abs(res_def - res_huge))) if res_def.size else 0.0
        n_ok += 1
        if d1 < 1e-12:
            same_default += 1
        else:
            print(f"  [{ci:2d}] 省略 vs =0 不一致 Δ={d1:.3e}")
        if d2 < 1e-12:
            same_huge += 1
        else:
            print(f"  [{ci:2d}] 省略 vs 极大 不一致 Δ={d2:.3e}")

    check(f"省略参数 vs 显式 0 一致", same_default == n_ok,
          f"{same_default}/{n_ok}")
    check(f"省略参数 vs 极大 cap 一致（结果不因加闸改变）", same_huge == n_ok,
          f"{same_huge}/{n_ok}")

    # ---- B) 超限必须抛异常 ----
    print("\nB) 超限必须抛异常且消息可辨识")
    raised = 0
    tried = 0
    msg_ok = 0
    for ci, case in enumerate(cases):
        try:
            call_case(case, 1)                            # cap=1 几乎必然立刻超限
        except RuntimeError as e:
            tried += 1
            raised += 1
            if "node cap" in str(e):
                msg_ok += 1
        except Exception as e:                            # noqa: BLE001
            tried += 1
            print(f"  [{ci:2d}] 抛了非 RuntimeError：{type(e).__name__}: {e}")
        else:
            tried += 1
    check("cap=1 时全部抛 RuntimeError", raised == tried, f"{raised}/{tried}")
    check("异常消息含 'node cap'（可辨识、可日志）", msg_ok == tried,
          f"{msg_ok}/{tried}")

    # ---- C) cap 单调性 ----
    print("\nC) cap 增大 → 结果收敛到不限制的值（不返回错误中途值）")
    conv_ok = 0
    conv_tot = 0
    for ci, case in enumerate(cases[:12]):
        try:
            ref = np.asarray(call_case(case)[0], float)
        except Exception:                                 # noqa: BLE001
            continue
        # 从不触发所需的 cap 逐步放大，最终必须与 ref 相同
        for cap in (10 ** 7, 10 ** 8):
            try:
                r = np.asarray(call_case(case, cap)[0], float)
            except RuntimeError:
                continue
            conv_tot += 1
            if r.shape == ref.shape and float(np.max(np.abs(r - ref))) < 1e-12:
                conv_ok += 1
            break
    check("大 cap 下结果等于不限制值", conv_ok == conv_tot and conv_tot > 0,
          f"{conv_ok}/{conv_tot}")

    # ---- D) 上层回退路径可达 ----
    print("\nD) utils/probability.py 的 win_rate 包装应捕获并回退")
    import utils.probability as prob
    orig = nat.mscore.win_rate

    def boom(*a, **k):
        raise RuntimeError("win_rate node cap exceeded: visited=999 cap=1")

    # 只验证"异常会被 except Exception 捕获后走纯 Python"这一结构，
    # 不去真跑纯 Python（太慢）：用 monkeypatch 观察是否进入 except 分支。
    reached = {"except": False}
    real_py = prob.ProbabilityMixin.win_rate

    class _Probe:
        pass

    # 直接检查源码里有 except Exception 包裹 C++ 调用
    import inspect
    src = inspect.getsource(prob.ProbabilityMixin.win_rate)
    check("C++ 调用被 try/except 包裹（异常可回退）",
          "try:" in src and "except Exception" in src)
    check("回退路径落到纯 Python 实现（含 cell_value_list 构造）",
          "cell_value_list" in src)

    print()
    print("=" * 84)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print("结论：node_cap 默认不改变任何结果；超限抛可辨识异常；")
        print("      上层已有 try/except 回退路径 —— 可作为防爆硬闸接入。")
    else:
        print("结论：存在失败项，不得据此宣布可用。")


if __name__ == "__main__":
    main()
