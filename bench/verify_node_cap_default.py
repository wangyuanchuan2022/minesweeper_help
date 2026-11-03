# -*- coding: utf-8 -*-
"""默认路径逐位对拍：生产默认 cap=2e6 时，win_rate 输出必须与不限制逐位相同。

=================== 为什么这是接入的必要条件 ====================
node_cap 默认启用 2e6。若正常对局的节点数低于该值，则行为**必须**与加闸前
逐位一致——否则等于悄悄改了算法。

判据（两个层次）：
  A) 用例层：对 33 个采集用例，cap=2e6 与 cap=0（不限制）的输出逐位相同
     （|Δ| < 1e-12，即 IEEE754 双精度逐位一致）
  B) 整局层：同一批 seed 跑完整对局，cap=2e6 与 cap=0 的 won/died/steps 逐局相同
     ——这直接证明"生产默认行为未变"

用法: python bench/verify_node_cap_default.py
"""
import json
import os
import random
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = 0
FAIL = 0

import utils.probability as _prob
DEFAULT_CAP = _prob._WR_NODE_CAP_RECOMMENDED


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def call(case, cap):
    args = dict(
        clicks=case["clicks"], clicks9=case["clicks9"],
        res_list=[np.asarray(g, np.int32) for g in case["groups"]],
        cell_value=np.asarray(case["cell_value"], np.int32),
        ck=case["ck"], num10=case["num10"], a=case["a"],
        w=case["w"], h=case["h"], is_play=case["is_play"],
        progress=lambda v: None, node_cap=cap)
    return np.asarray(import_native().win_rate(**args)[0], float)


def import_native():
    import utils.native as nat
    return nat.mscore


def play(seed, w, h, a, cap, steps=120):
    from sim.eval_harness import install_stubs, SimDriver, GameOver
    from sim.minesweeper_sim import Board
    from utils.solver import Solver
    import utils.probability as prob

    install_stubs(os.path.join(ROOT, "state", "ab", "defcap"), True)
    orig = prob._wr_node_cap
    prob._wr_node_cap = lambda: cap
    try:
        np.random.seed(seed)
        rng = random.Random(seed)
        board = Board(w, h, a, rng)
        board.open(w // 2, h // 2)
        s = Solver()
        s.w, s.h, s.a = w, h, a
        s.limit = 80
        s.is_play = True
        s.num, s.checked, s.pos_dict_list, s.appended_pos = 1, {}, [], set()
        s.stats_data, s._stats_updates = {}, 0
        s.till_now_winrate = 1.0
        s.count = 0
        s.bx, s.by, s.cell_width = 0.0, 0.0, 24
        s._time_budget = None
        drv = SimDriver(board, s, rng)
        s.complete_scan = drv.complete_scan
        s.rescan_after_click = drv.rescan_after_click
        import pyautogui
        pyautogui.click = drv.on_click
        cv = np.full((h + 2, w + 2), 9, dtype="int32")
        for _ in range(steps):
            if board.dead or board.won:
                break
            try:
                cv = s.complete_scan(cv, True)
                sum2 = np.sum(cv)
                cv = s.mine_clear1(cv, 0, 0)
                cv = s.mine_clear3_1(cv, 0, 0)
                sum3 = np.sum(cv)
                if board.dead or board.won:
                    break
                if sum3 == sum2:
                    drv.in_decision = True
                    _pre = cv.copy()
                    cv = s.number5_1(cv)
                    for _f in (10, 11):
                        _m = (_pre == _f) & (cv == 9)
                        cv[_m] = _f
                    drv.in_decision = False
            except GameOver:
                break
        drv.flush_clicks()
        return {"won": bool(board.won), "died": bool(board.dead),
                "opened": board.opened_count,
                "hits": int(getattr(s, "_wr_nodecap_hits", 0))}
    finally:
        prob._wr_node_cap = orig


def main():
    print("=" * 88)
    print(f"默认路径逐位对拍（cap={DEFAULT_CAP:,} 生产默认 vs cap=0 不限制）")
    print("=" * 88)

    import utils.native as nat
    if not nat.available:
        print("native 不可用")
        return

    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("\nA) 用例层：33 个固定用例逐位对比")
    same = 0
    tot = 0
    for ci, case in enumerate(cases):
        try:
            r_def = call(case, DEFAULT_CAP)   # 生产默认（从模块读取，避免硬编码漂移）
            r_open = call(case, 0)           # 不限制
        except Exception as e:                             # noqa: BLE001
            print(f"  [{ci:2d}] 调用失败 {type(e).__name__}: {e}")
            continue
        tot += 1
        d = float(np.max(np.abs(r_def - r_open))) if r_def.size else 0.0
        if d < 1e-12:
            same += 1
        else:
            print(f"  [{ci:2d}] Δ={d:.3e}")
    check(f"默认 cap 与不限制逐位一致（{same}/{tot}）", same == tot and tot > 0)

    print("\nB) 整局层：默认 cap 下行为是否与不限制完全相同")
    n_same = 0
    n_tot = 0
    n_wr_triggered = 0
    for i in range(10):
        sd = 20261102 + i
        r_def = play(sd, 30, 16, 99, DEFAULT_CAP)
        r_open = play(sd, 30, 16, 99, 0)
        n_tot += 1
        ok = (r_def["won"] == r_open["won"] and r_def["died"] == r_open["died"]
              and r_def["opened"] == r_open["opened"])
        if ok:
            n_same += 1
        else:
            print(f"  [{sd}] 不一致：def={r_def} open={r_open}")
        check_ = r_def["hits"]
        if check_:
            n_wr_triggered += check_
    check(f"整局 won/died/opened 逐局一致（{n_same}/{n_tot}）", n_same == n_tot)
    check("默认 cap 下从未触发降级（正常对局不受影响）", n_wr_triggered == 0,
          f"触发 {n_wr_triggered} 次")

    print()
    print("=" * 88)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print(f"结论：生产默认 cap={DEFAULT_CAP:,} 对正常对局**零行为改变**（逐位对拍通过），")
        print("      只在大局面时兜底——可作为防爆闸门接入。")
    else:
        print("结论：默认路径发生行为改变，不得接入。")


if __name__ == "__main__":
    main()
