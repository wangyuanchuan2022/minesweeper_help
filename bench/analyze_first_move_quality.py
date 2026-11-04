# -*- coding: utf-8 -*-
"""首点只开 1 格时的首个决策：生产选格 vs 精确最优（**无结果筛选**）。

=================== 上一版为什么错（必须写清）====================
旧 probe 从 `_last_heatmap["best"]` 读选格，但：
  · `number5_1` 在**函数末尾**才写 `_last_heatmap`
  · 它内部的 `complete_scan` → `flush_clicks` 若踩雷会抛 `GameOver`
  ⇒ 踩雷路径**永远读不到 best**，best 恒为 None
  ⇒ 旧统计里"C 类 35/35 全死"= **按结果筛选**的伪影，不是"随机盲猜局"。

正确捕获点：`SimDriver.flush_clicks` 的 `self.pending` —— 点击在**致死之前**
就已入队，是权威事实源（实测 trace 已确认）。

=================== 本脚本的判据（advisor 要求的无偏口径）====================
对每个"首点只开 1 格"的局，拿到首个决策真正点的格 c，然后：
  · 命中概率：用真值盘面判断 c 是否雷（无偏）
  · 该局首格数字 k
  · 精确最优雷率 = min(k/8, (99−k)/471)   ← 由对称性/全局约束得到，精确
  · 后悔度 = P(c 是雷) − 最优雷率（用类别级频率对比，不是单局）
分层报告（无任何结果筛选），并给出配对结论。

用法: python bench/analyze_first_move_quality.py --games 400
"""
import argparse
import json
import math
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
N_INTERIOR = W * H - 1 - 8          # 471


def optimal_rate(k):
    """精确最优雷率与最优类型（k = 首格数字）。"""
    nb = k / 8.0
    it = (A - k) / N_INTERIOR
    return (nb, "neighbor") if nb <= it else (it, "interior")



def _atomic_json_dump(obj, path):
    """原子写 JSON：先写 .tmp，fsync 后再 replace。

    ⚠️ 为什么必须原子：本次实跑出现过"日志含 KeyboardInterrupt、
    但正式 JSON 已存在"的并存状态。文件存在不能证明实验完整，
    所以写入必须原子，并带 complete 标记供核对。
    """
    import json as _json
    import os as _os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(obj, f, ensure_ascii=False)
        f.flush()
        try:
            _os.fsync(f.fileno())
        except OSError:
            pass
    _os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "fmq"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver
    from sim.minesweeper_sim import Board

    st = {}
    orig_n51 = prob.ProbabilityMixin.number5_1
    orig_flush = SimDriver.flush_clicks

    def h_n51(self, cell_value):
        # 记录**首个**概率决策的 n_opened 与首格数字（用内区，外框恒 0）
        if not st.get("n51_seen"):
            st["n51_seen"] = True
            inner = cell_value[1:H + 1, 1:W + 1]
            mask = (inner >= 0) & (inner <= 8)
            st["n_opened"] = int(np.sum(mask))
            ys, xs = np.where(mask)
            if len(ys):
                st["k"] = int(inner[ys[0], xs[0]])
                st["first_xy"] = (int(xs[0]), int(ys[0]))
        return orig_n51(self, cell_value)

    def h_flush(self):
        # 权威捕获：本决策真正点的格（致死前已在 pending 里）
        if st.get("n51_seen") and "pick" not in st and self.pending:
            st["pick"] = tuple(self.pending[0])
            st["mines"] = set(self.board.mines)
        return orig_flush(self)

    prob.ProbabilityMixin.number5_1 = h_n51
    SimDriver.flush_clicks = h_flush

    rows = []
    n_games = 0
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            st.clear()
            try:
                r = ab.play_one(sd, W, H, A, "baseline", 80, 200000, None)
            except Exception:                          # noqa: BLE001
                continue
            n_games += 1
            rows.append({
                "seed": sd, "won": bool(r.get("won")),
                "died": bool(r.get("died")), "opened": r.get("opened"),
                "n_opened": st.get("n_opened"), "k": st.get("k"),
                "first_xy": list(st["first_xy"]) if st.get("first_xy") else None,
                "pick": list(st["pick"]) if st.get("pick") else None,
                "pick_is_mine": (None if "pick" not in st
                                 else tuple(st["pick"]) in st["mines"]),
            })
            if (gi + 1) % 100 == 0:
                print(f"  ... {gi+1}/{args.games}", flush=True)
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("\n" + "=" * 100)
    print(f"首点只开 1 格的首个决策质量（{n_games} 局，无结果筛选）")
    print("=" * 100)

    o1 = [r for r in rows if r.get("n_opened") == 1]
    print(f"\n  首点只开 1 格：{len(o1)}/{n_games} = "
          f"{len(o1)/max(1,n_games)*100:.2f}%")
    err = [r for r in rows if r.get("n_opened") is None]
    print(f"  未观测到决策（无 number5_1 调用）：{len(err)}")

    have_pick = [r for r in o1 if r.get("pick") is not None]
    print(f"  其中捕获到首点击的：{len(have_pick)}/{len(o1)} "
          f"= {len(have_pick)/max(1,len(o1))*100:.2f}%   ← 应接近 100%")

    print("\n" + "=" * 100)
    print("按首格数字 k 分层：真实雷率 vs 精确最优（口径 = 该层样本数）")
    print("=" * 100)
    print(f"{'k':>3} {'样本':>5} {'实测雷率':>9} {'精确最优':>9} {'最优类型':>9} "
          f"{'差(pp)':>8} {'选邻域':>7} {'其中死':>6}")
    print("-" * 100)

    by_k = {}
    for r in have_pick:
        by_k.setdefault(r["k"], []).append(r)

    tot_m = tot_n = 0
    opt_w = 0.0
    for k in sorted(by_k):
        sub = by_k[k]
        m = sum(1 for r in sub if r["pick_is_mine"])
        opt, kind = optimal_rate(k)
        near = sum(1 for r in sub
                   if r["first_xy"] and r["pick"] and
                   max(abs(r["pick"][0] - r["first_xy"][0]),
                       abs(r["pick"][1] - r["first_xy"][1])) == 1)
        tot_m += m
        tot_n += len(sub)
        opt_w += opt * len(sub)
        print(f"{k:>3} {len(sub):>5} {m/len(sub)*100:>8.2f}% "
              f"{opt*100:>8.2f}% {kind:>9} "
              f"{(m/len(sub)-opt)*100:>+7.2f} {near/len(sub)*100:>6.1f}% "
              f"{m:>6}")
    print("-" * 100)
    opt_w /= max(1, tot_n)
    print(f"{'合计':>3} {tot_n:>5} {tot_m/tot_n*100:>8.2f}% "
          f"{opt_w*100:>8.2f}% {'加权':>9} "
          f"{(tot_m/tot_n-opt_w)*100:>+7.2f}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    p_act = tot_m / max(1, tot_n)
    diff = p_act - opt_w
    se = math.sqrt(max(p_act * (1 - p_act) / max(1, tot_n), 1e-12))
    z = diff / se if se > 0 else 0.0
    print(f"  实测 {p_act*100:.2f}%  vs  精确最优 {opt_w*100:.2f}%")
    print(f"  差 {diff*100:+.2f}pp（二项 SE ≈ {se*100:.2f}pp, z={z:+.2f}）")
    if z > 1.96:
        print("  ⇒ 实测**显著差于**最优 ⇒ 首个决策有可观改进空间")
    elif z < -1.96:
        print("  ⇒ 实测显著优于最优（不可能，除非口径有错）⇒ 需复查")
    else:
        print("  ⇒ 与最优无显著差异 ⇒ 首个决策已接近最优，")
        print("     这部分损失是 Arbiter 首点规则的**结构性代价**")

    out = args.out or os.path.join(ROOT, "state", "ab", "first_move_quality.json")
    _atomic_json_dump({"n_games": n_games, "rows": rows, "complete": True}, out)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
