# -*- coding: utf-8 -*-
"""采集 win_rate 入参快照（**生产配置**，不执行任何真实计算）。

安全性设计：
  · 用生产阈值（默认 8），不强制所有局面都进 win_rate —— 这正是之前把内存
    顶爆的原因（强制 999 会让每个局面都跑完整递归）。
  · capture 在**真正计算之前**就 raise，所以零计算开销、零内存放大。
  · 只落盘人类可见信息（cell_value 的 9/10/0..8 + clicks/clicks9/groups），不含雷位。

产出用于：
  · 改前后逐位对拍（同一输入 → 同一输出）
  · 性能基准（同一输入，多次测量取中位数）

用法:
  python bench/collect_wr_cases.py --games 40 --out bench/data/wr_cases.json
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

UNOPENED = 9


class _Captured(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--max-cases", type=int, default=40)
    ap.add_argument("--max-cand", type=int, default=200,
                    help="候选格数上限（生产实测 p90≈18，200 已很宽松）")
    ap.add_argument("--out", default=os.path.join(ROOT, "bench", "data", "wr_cases.json"))
    args = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import random

    import numpy as np

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "eval_run", "cases"), True)

    import pyautogui

    import utils.probability as prob
    from sim.minesweeper_sim import Board
    from utils.solver import Solver

    cases = []
    skipped = {"cand": 0, "err": 0}

    orig_wr = prob.ProbabilityMixin.win_rate

    def capture(self, clicks, clicks9, res_list, cell_value, ck, num10):
        total = 1
        for c in ck:
            total *= int(c)
        n_cand = 1 + len(clicks) + len(clicks9)
        if n_cand > args.max_cand:
            skipped["cand"] += 1
            raise _Captured()
        if len(cases) < args.max_cases:
            cases.append({
                "F": len(clicks), "U": len(clicks9), "total": int(total),
                "n_cand": n_cand,
                "limitation": 0.8 * len(clicks9) + math.log2(max(total, 1)),
                "clicks": [list(p) for p in clicks],
                "clicks9": [list(p) for p in clicks9],
                "ck": [int(c) for c in ck],
                "num10": int(num10),
                "a": int(self.a), "w": int(self.w), "h": int(self.h),
                "is_play": bool(self.is_play),
                "groups": [[[int(v) for v in row] for row in np.asarray(g).tolist()]
                           for g in res_list],
                "cell_value": np.asarray(cell_value).astype(int).tolist(),
            })
        raise _Captured()

    prob.ProbabilityMixin.win_rate = capture

    for g in range(args.games):
        if len(cases) >= args.max_cases:
            break
        seed = 20261102 + g
        np.random.seed(seed)
        rng = random.Random(seed)
        board = Board(30, 16, 99, rng)
        board.open(15, 8)

        s = Solver()
        s.w, s.h, s.a = 30, 16, 99
        s.limit = args.limit
        s.is_play = True
        s.num, s.checked, s.pos_dict_list, s.appended_pos = 0, {}, [], set()
        s.stats_data, s._stats_updates = {}, 0
        s.till_now_winrate = 1.0
        s.count = 0
        s.bx, s.by, s.cell_width = 0.0, 0.0, 24
        s._time_budget = None

        pend = []

        def on_click(px, py, _cw=24):
            pend.append((int(round(px / _cw)) - 1, int(round(py / _cw)) - 1))

        def flush():
            for (x, y) in pend:
                if board.cells[y][x] == UNOPENED:
                    board.open(x, y)
                    if board.dead:
                        break
            pend.clear()

        def complete_scan(cv, no_10=True):
            flush()
            if board.dead:
                raise RuntimeError("dead")
            for y in range(1, 17):
                for x in range(1, 31):
                    if cv[y, x] == UNOPENED:
                        v = board.cells[y - 1][x - 1]
                        cv[y, x] = UNOPENED if v == UNOPENED else int(v)
            return cv

        s.complete_scan = complete_scan
        s.rescan_after_click = complete_scan
        pyautogui.click = on_click

        cv = np.full((18, 32), UNOPENED, dtype="int32")
        for _step in range(400):
            if board.dead or board.won or len(cases) >= args.max_cases:
                break
            try:
                cv = s.complete_scan(cv, True)
                sum2 = int(np.sum(cv))
                cv = s.mine_clear1(cv, 0, 0)
                cv = s.mine_clear3_1(cv, 0, 0)
                if board.dead or board.won:
                    break
                if int(np.sum(cv)) == sum2:
                    cv = s.number5_1(cv)
                    flush()
            except _Captured:
                # ⚠️ 捕获后必须**推进一步**，否则 number5_1 内部会把它当成
                # "求解失败"重试 → 同一局面反复采集（曾出现 40 个用例全同）。
                # 这里直接随便开一格，把局面推下去。
                import random as _r
                u = [(x, y) for y in range(16) for x in range(30)
                     if board.cells[y][x] == UNOPENED]
                if not u:
                    break
                board.open(*_r.choice(u))
                continue
            except Exception:  # noqa: BLE001
                skipped["err"] += 1
                break

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"cases": cases, "skipped": skipped}, f, ensure_ascii=False)

    print("=" * 74)
    print(f"采集 {len(cases)} 个 win_rate 用例 → {args.out}")
    print(f"候选过多跳过 {skipped['cand']} · 其它异常 {skipped['err']}")
    if cases:
        print(f"  n_cand   min {min(c['n_cand'] for c in cases):3d}  "
              f"max {max(c['n_cand'] for c in cases):3d}")
        print(f"  total    min {min(c['total'] for c in cases):5d}  "
              f"max {max(c['total'] for c in cases):5d}")
        print(f"  U(内部)  min {min(c['U'] for c in cases):3d}  "
              f"max {max(c['U'] for c in cases):3d}")


if __name__ == "__main__":
    main()
