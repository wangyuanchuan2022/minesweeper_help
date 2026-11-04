# -*- coding: utf-8 -*-
"""核查：残局里出现 best_safety=1.0 却没点该格，是真的漏了还是记录错位？

=================== 疑点 ====================
bench/analyze_endgame_quality.py 报：53 个残局决策点里 17 个 regret>0.05，
其中多个出现 `best_safety = 1.0`（即存在**确定安全格**），而算法实际选了
安全率 0.5~0.75 的格。但生产设计上 `mine_clear1/mine_clear3_1` 免费推理
应当先点掉所有确定安全格 —— 两者矛盾。

三种可能：
  H1 记录错位：`steps[-1]["pick"]` 不是与 `steps[-1]["pm"]` 同一次决策的点击
     （`flush_clicks` 可能由**下一步**的 complete_scan 触发）
  H2 `pm`（prob_map）与 `pick` 的坐标系不一致
  H3 真的漏了确定安全格（算法缺陷）

判定方式：对单个决策点，打印
  · prob_map 全部内容（哪些格 safety=1.0）
  · 实际 pending 点击
  · 该决策点前后 number5_1 调用序列
  · 真值盘面上 selected bestsafety=1.0 的格是否真的不是雷
若 best=1.0 的格真的不是雷，而算法没点它 ⇒ H3 或 H1/H2 必须有一者为真。

用法: python bench/verify_endgame_safety.py --seed 20261127
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
    ap.add_argument("--seed", type=int, default=20261127)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "vfy_eg"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver

    log = []
    orig_n51 = prob.ProbabilityMixin.number5_1
    orig_flush = SimDriver.flush_clicks

    def h_n51(self, cell_value):
        inner = cell_value[1:H + 1, 1:W + 1]
        n_op = int(np.sum((inner >= 0) & (inner <= 8)))
        out = orig_n51(self, cell_value)
        hm = getattr(self, "_last_heatmap", None) or {}
        pm = dict(hm.get("prob") or {})
        safe_cells = [k for k, v in pm.items() if v >= 1.0 - 1e-9]
        log.append({
            "ev": "N51", "n_opened": n_op,
            "pm_size": len(pm), "best": hm.get("best"),
            "best_conf": hm.get("best_conf"),
            "n_safe_in_pm": len(safe_cells),
            "safe_cells": safe_cells[:10],
        })
        return out

    def h_flush(self):
        if self.pending:
            log.append({"ev": "FLUSH", "pending": list(self.pending),
                        "in_decision": self.in_decision,
                        "mines_preview": [p in self.board.mines
                                          for p in self.pending]})
        return orig_flush(self)

    prob.ProbabilityMixin.number5_1 = h_n51
    SimDriver.flush_clicks = h_flush
    try:
        r = ab.play_one(args.seed, W, H, A, "baseline", 80, 200000, None)
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("=" * 100)
    print(f"seed={args.seed}  won={r.get('won')} died={r.get('died')} "
          f"opened={r.get('opened')}")
    print("=" * 100)

    # 只打印残局附近的事件（n_opened >= 340）
    started = False
    for e in log:
        if e["ev"] == "N51" and e["n_opened"] >= 340:
            started = True
        if started:
            if e["ev"] == "N51":
                print(f"N51  n_opened={e['n_opened']:>3} pm={e['pm_size']:>3} "
                      f"best={e['best']} conf={e['best_conf']} "
                      f"确定安全格数={e['n_safe_in_pm']} "
                      f"{e['safe_cells']}")
            else:
                print(f"     FLUSH pending={e['pending']} "
                      f"in_decision={e['in_decision']} "
                      f"实际是雷={e['mines_preview']}")

    # 关键判定：是否存在"有确定安全格却点了非安全格"
    print("\n" + "=" * 100)
    print("判定：存在『确定安全格』时，算法是否点了它")
    print("=" * 100)
    pending_n51 = None
    violations = 0
    checks = 0
    for e in log:
        if e["ev"] == "N51":
            pending_n51 = e
        elif e["ev"] == "FLUSH" and pending_n51 is not None:
            # 仅当该 N51 是残局且 prob_map 里有确定安全格
            if (pending_n51["n_opened"] >= 340 and
                    pending_n51["n_safe_in_pm"] > 0 and
                    pending_n51["best"] is not None):
                checks += 1
                bc = pending_n51["best_conf"]
                is_safe_pick = (bc is not None and bc >= 1.0 - 1e-9)
                if not is_safe_pick:
                    violations += 1
                    print(f"  ⚠️ n_opened={pending_n51['n_opened']} "
                          f"有 {pending_n51['n_safe_in_pm']} 个确定安全格，"
                          f"但 best_conf={bc}（<1）")
            pending_n51 = None
    print(f"\n  可判定次数 {checks}，其中『有安全格但没点』{violations}")

    if checks == 0:
        print("  ⇒ 本局残局阶段未出现『prob_map 含确定安全格』的情形，")
        print("     需换 seed 或扩大样本再判。")
    elif violations == 0:
        print("  ⇒ ✅ 有确定安全格时算法总是点它 ⇒ H1/H2（记录错位）更可能，")
        print("     即 analyze_endgame_quality 的 pick/pm 配对有错位。")
    else:
        print(f"  ⇒ ⚠️ H3 可能成立：{violations} 次在存在确定安全格时未点它。")
        print("     但需先排除坐标系不一致（H2）。")


if __name__ == "__main__":
    main()
