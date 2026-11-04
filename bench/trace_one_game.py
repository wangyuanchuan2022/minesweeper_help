# -*- coding: utf-8 -*-
"""单局全轨迹诊断：把 20261108 这一局每一步都打出来。

=================== 要查的矛盾 ====================
分类统计里 C 类的"首次点击"坐标出现 (29,0)、(0,15)、(14,7) 等
角落/边缘位置，且 35/35 全部踩雷（100%），而理论无信息雷率是 20.67%。
这在统计上不可能是一类真实行为 ⇒ 说明**我捕获的不是我以为的那个点击**。

本脚本打印单局的完整事件序列：
  · Board.open 的每次调用（谁调的、坐标、是否踩雷）
  · number5_1 的每次调用（n_opened、k、best、prob 表大小）
  · SimDriver.flush_clicks 每次结算的 pending 内容
以此判定 "first_click" 到底对应哪一步。

用法: python bench/trace_one_game.py --seed 20261108
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261108)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "trace"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver
    from sim.minesweeper_sim import Board

    log = []
    depth = {"open": 0}

    # ---- 1) Board.open：记录谁调的 ----
    orig_open = Board.open

    def h_open(self, x, y):
        import traceback
        st = traceback.extract_stack()
        # 找最近的非本文件调用者
        caller = ""
        for fr in reversed(st[:-1]):
            if "trace_one_game" not in fr.filename:
                caller = f"{os.path.basename(fr.filename)}:{fr.lineno}@{fr.name}"
                break
        before_dead = self.dead
        was_unopened = (self.cells[y][x] == 9) if self.generated else True
        r = orig_open(self, x, y)
        log.append({
            "ev": "open", "xy": (x, y), "caller": caller,
            "was_unopened": was_unopened,
            "is_mine": (x, y) in self.mines,
            "dead_after": self.dead,
            "opened_count": self.opened_count,
            "generated_before": before_dead is not None,
        })
        return r

    Board.open = h_open

    # ---- 2) number5_1 ----
    orig_n51 = prob.ProbabilityMixin.number5_1

    def h_n51(self, cell_value):
        inner = cell_value[1:H + 1, 1:W + 1]
        mask = (inner >= 0) & (inner <= 8)
        n_op = int(np.sum(mask))
        ys, xs = np.where(mask)
        k = int(inner[ys[0], xs[0]]) if len(ys) else None
        out = orig_n51(self, cell_value)
        hm = getattr(self, "_last_heatmap", None) or {}
        log.append({
            "ev": "number5_1", "n_opened": n_op, "k": k,
            "best": hm.get("best"), "best_conf": hm.get("best_conf"),
            "prob_size": len(hm.get("prob") or {}),
            "total": hm.get("total"),
        })
        return out

    prob.ProbabilityMixin.number5_1 = h_n51

    # ---- 3) flush_clicks：pending 结算 ----
    orig_flush = SimDriver.flush_clicks

    def h_flush(self):
        if self.pending:
            log.append({"ev": "flush", "pending": list(self.pending),
                        "in_decision": self.in_decision})
        return orig_flush(self)

    SimDriver.flush_clicks = h_flush

    try:
        r = ab.play_one(args.seed, W, H, A, "baseline", 80, 200000, None)
    finally:
        Board.open = orig_open
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("=" * 100)
    print(f"单局轨迹 seed={args.seed}  结果 won={r.get('won')} "
          f"died={r.get('died')} opened={r.get('opened')}")
    print("=" * 100)
    for i, e in enumerate(log):
        if e["ev"] == "open":
            mark = "💥踩雷" if e["is_mine"] else ""
            print(f"[{i:>3}] OPEN  xy={e['xy']} by={e['caller']:<34} "
                  f"was_unopened={e['was_unopened']} "
                  f"opened_count={e['opened_count']:>3} {mark}")
        elif e["ev"] == "number5_1":
            print(f"[{i:>3}] N51   n_opened={e['n_opened']:>3} k={e['k']} "
                  f"best={e['best']} conf={e['best_conf']} "
                  f"prob_size={e['prob_size']} total={e['total']}")
        else:
            print(f"[{i:>3}] FLUSH pending={e['pending']} "
                  f"in_decision={e['in_decision']}")


if __name__ == "__main__":
    main()
