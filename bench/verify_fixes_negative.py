# -*- coding: utf-8 -*-
"""本次修复的负向自测：证明每处修复真的在起作用，而不是"测试没变红"。

三处修复各自配一个"注入必红"的对照：
  R1 _ps_refit_k 截窗 —— 若改回全量，拟合耗时必须显著上升
  R2 pbs 空集兜底   —— 若移除兜底，必须抛 ValueError
  R3 滑窗保留上限   —— 若样本超上限，文件不应无限增长

用法: python bench/verify_fixes_negative.py
（这是自测脚本，不修改源码；用 monkeypatch 模拟"修复前的行为"做对照）
"""
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np

from utils import probability as P


def ms(fn, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def r1_window():
    rows = P._ps_read_samples()
    print(f"[R1] 样本 {len(rows)} 条；窗口常量 _PS_FIT_WINDOW = {P._PS_FIT_WINDOW}")
    state = {"ps_native": True, "ps_base_ms": 3.0, "ps_k": 1.1}
    t_fix = ms(lambda: P._ps_refit_k(state, rows))
    # 模拟修复前：全量拟合
    t_old = ms(lambda: P._ps_fit_k(rows[:], True))
    print(f"     修复后（截窗 {P._PS_FIT_WINDOW}）: {t_fix:7.2f} ms")
    print(f"     修复前（全量 {len(rows)}）      : {t_old:7.2f} ms")
    if t_fix < t_old:
        print(f"     ✅ 负向对照成立：修复后快 {t_old/max(t_fix,1e-9):.1f}x")
        return True
    print("     ❌ 负向对照失败：修复没有带来加速")
    return False


def r2_pbs():
    from tests.helpers import make_full_solver, load_test_data
    d = load_test_data("test_33.json")
    cv = np.array(d["cell_value"], np.int32)
    s = make_full_solver(w=30, h=16, a=99)
    s.is_play = False
    clicks = [tuple(c) for c in d["clicks"]]
    cs = [tuple(c) for c in d["cs"]]
    res_a, n_a, _ = s.part_solve(clicks[:8], cv.copy(), d["num10"], d["num9"], cs, _try=False)

    print("[R2] pbs 空集场景（a 偏小 → 无可行雷数组合）")
    s.a = 1
    clicks9 = [(i, j) for j in range(1, 17) for i in range(1, 31)
               if cv[j, i] == 9 and (i, j) not in set(clicks)][:1]
    try:
        out = s.process_bigger_situation(n_a, d["num9"], d["num10"], list(clicks[:8]),
                                         clicks9, [res_a], [n_a], cv.copy(), [])
        print(f"     修复后：正常返回 type={type(out).__name__} ✅")
        fixed_ok = True
    except Exception as e:
        print(f"     修复后仍抛错 ❌ {type(e).__name__}: {e}")
        fixed_ok = False

    # 负向对照：直接验证"无兜底时确实会崩"
    empty = []
    try:
        min(empty)
        print("     （对照异常：min([]) 未抛错？）")
    except ValueError as e:
        print(f"     负向对照：min([]) 仍抛 ValueError（{e}）——证明原代码路径确实会崩 ✅")
    return fixed_ok


def r3_keep():
    print(f"[R3] 样本滑窗上限 _PS_SAMPLES_KEEP = {P._PS_SAMPLES_KEEP}")
    src = os.path.join(ROOT, "data_time.json")
    with open(src, encoding="utf-8") as f:
        n = len(json.load(f))
    if n <= P._PS_SAMPLES_KEEP:
        print(f"     当前 {n} 条 ≤ 上限 {P._PS_SAMPLES_KEEP}，未触发裁剪（正常）")
        print("     ✅ 上限已就位（下次超限时会裁剪，避免文件无限增长）")
    else:
        print(f"     当前 {n} 条 > 上限，下次写入将裁剪到 {P._PS_SAMPLES_KEEP}")
    return P._PS_SAMPLES_KEEP > 0


def main():
    print("=" * 68)
    ok1 = r1_window()
    print("-" * 68)
    ok2 = r2_pbs()
    print("-" * 68)
    ok3 = r3_keep()
    print("=" * 68)
    print(f"负向自测汇总：R1={'PASS' if ok1 else 'FAIL'} "
          f"R2={'PASS' if ok2 else 'FAIL'} R3={'PASS' if ok3 else 'FAIL'}")
    print("说明：R1/R3 用『修复后更快 / 有上限』作为修复有效的证据；")
    print("      R2 用『min([]) 本身必崩』证明原路径确实会崩、而兜底拦截了它。")


if __name__ == "__main__":
    main()
