# -*- coding: utf-8 -*-
"""直接对拍：探针记录的"首个 number5_1 选格"是否就是真实踩雷的那一步。

=================== 为什么必须做 ====================
probe_first_guess 在 76 个"首点只开 1 格"样本上报告 **第二步踩雷 0 次**，
而 2000 局真实数据里有 **295 局（14.75%）在 opened==1 时死亡**。
两者不可能同时为真 ⇒ 至少一方错。

本脚本用**最直白的方式**判定：逐局跑 play_one，同时记录
  A) 探针口径：首个 number5_1 的 `_last_heatmap["best"]`
  B) 真值口径：该局是否 died 且 opened<=1
  C) 权威口径：SimDriver 实际施加的**第一个点击**坐标与其真实雷性
然后三方对拍。只要 A 与 C 不一致，就是探针的坐标/残留问题。

用法: python bench/verify_first_guess_truth.py --games 120
"""
import argparse
import json
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
SAFE_TOTAL = W * H - A          # 381


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "verify_fg"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver
    from sim.minesweeper_sim import Board

    # ---- 我方探针：首个 number5_1 的 best + n_opened ----
    probe = {}
    orig_n51 = prob.ProbabilityMixin.number5_1

    def h51(self, cell_value):
        if probe.get("done"):
            return orig_n51(self, cell_value)
        probe["done"] = True
        inner = cell_value[1:H + 1, 1:W + 1]
        mask = (inner >= 0) & (inner <= 8)
        probe["n_opened"] = int(np.sum(mask))
        ys, xs = np.where(mask)
        if len(ys):
            probe["k"] = int(inner[ys[0], xs[0]])
            probe["first_xy"] = (int(xs[0]), int(ys[0]))
        hm_prev = getattr(self, "_last_heatmap", None) or {}
        probe["prev_best"] = hm_prev.get("best")
        out = orig_n51(self, cell_value)
        hm = getattr(self, "_last_heatmap", None) or {}
        probe["best"] = hm.get("best")
        probe["out_pos0"] = (tuple(out[0][0]) if isinstance(out, tuple) and out
                             and out[0] else None) if isinstance(out, tuple) else None
        return out

    # ---- 权威口径：SimDriver 实际施加的第一个点击 ----
    truth = {}
    orig_flush = SimDriver.flush_clicks

    def hflush(self):
        if "first_click" not in truth and self.pending:
            truth["first_click"] = tuple(self.pending[0])    # 0-based (x,y)
            truth["board_mines"] = set(self.board.mines)
            truth["board_ref"] = self.board
        return orig_flush(self)

    prob.ProbabilityMixin.number5_1 = h51
    SimDriver.flush_clicks = hflush

    rows = []
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            probe.clear()
            truth.clear()
            try:
                r = ab.play_one(sd, W, H, A, "baseline", 80, 200000, None)
            except Exception:                          # noqa: BLE001
                continue
            if probe.get("n_opened") != 1:
                continue
            rows.append({
                "seed": sd,
                "probe_n_opened": probe.get("n_opened"),
                "probe_k": probe.get("k"),
                "probe_best_1based": probe.get("best"),
                "probe_out_pos0": probe.get("out_pos0"),
                "truth_first_click_0based": truth.get("first_click"),
                "truth_first_is_mine": (
                    None if truth.get("first_click") is None
                    else tuple(truth["first_click"]) in truth["board_mines"]),
                "died": bool(r.get("died")),
                "opened": r.get("opened"),
                "died_step1": bool(r.get("died") and (r.get("opened") or 0) <= 1),
            })
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("=" * 100)
    print(f"三方对拍：{len(rows)} 个『首点只开 1 格』样本")
    print("=" * 100)

    # A vs C：探针 best(1-based) 减 1 后 是否等于权威首点击(0-based)
    n_mismatch = 0
    n_checked = 0
    for r in rows:
        b = r["probe_best_1based"]
        c = r["truth_first_click_0based"]
        if b is None or c is None:
            continue
        n_checked += 1
        if (int(b[0]) - 1, int(b[1]) - 1) != tuple(c):
            n_mismatch += 1

    print(f"\nA vs C（探针 best 与真实首次点击是否同格）：")
    print(f"  可比样本 {n_checked}，不一致 {n_mismatch} "
          f"= {n_mismatch/max(1,n_checked)*100:.2f}%")

    # 死亡统计（三种口径）
    n = len(rows)
    dead_step1 = sum(1 for r in rows if r["died_step1"])
    probe_mine = sum(1 for r in rows if r["truth_first_is_mine"])
    print(f"\n死亡统计（同一批样本，三种口径）：")
    print(f"  ① probe 判 died_step1（died 且 opened<=1）：{dead_step1}/{n} "
          f"= {dead_step1/max(1,n)*100:.2f}%")
    print(f"  ② 首个真实点击是雷：{probe_mine}/{n} "
          f"= {probe_mine/max(1,n)*100:.2f}%")
    print(f"  ③ probe 记录的 best 是雷：", end="")
    nb = 0
    for r in rows:
        b = r["probe_best_1based"]
        if b is None:
            continue
        # 用该局 board 判断
        nb += 0     # 由下面统一算
    print("（见下）")

    # 用 truth board 判断 probe best 是否雷（需重放，故此处改用 truth_first_is_mine 对照）
    print(f"\n【关键判定】")
    if n_mismatch == 0 and probe_mine == 0:
        print("  探针 best 与真实点击完全一致，且**首个点击从不是雷**")
        print("  ⇒ 探针没错，但『首点只开 1 格』的样本里，第一步猜测确实没踩雷。")
        print("     那 295 局 opened==1 的死亡必然发生在**别的地方**（需另查）。")
    elif n_mismatch == 0 and probe_mine > 0:
        print(f"  探针 best 与真实点击一致，但首点击有 {probe_mine} 次是雷")
        print("  ⇒ 探针的 is_mine 判定口径与 died_step1 口径不一致，需修。")
    else:
        print(f"  ✘ 探针 best 与真实点击有 {n_mismatch} 处不一致 "
              f"⇒ 探针坐标/残留仍有问题，其结论作废。")

    # 交叉：died_step1 与 truth_first_is_mine 应高度一致
    both = [(r["died_step1"], r["truth_first_is_mine"]) for r in rows
            if r["truth_first_is_mine"] is not None]
    agree = sum(1 for a, b in both if a == b)
    print(f"\n  口径一致性：died_step1 == first_click_is_mine 的样本 "
          f"{agree}/{len(both)} = {agree/max(1,len(both))*100:.2f}%")

    # 打印几个样例帮助人工核对
    print(f"\n样例（前 8 个）：")
    print(f"{'seed':>10} {'k':>2} {'probe_best(1b)':>16} {'truth_click(0b)':>16} "
          f"{'is_mine':>8} {'died':>6} {'opened':>7}")
    for r in rows[:8]:
        print(f"{r['seed']:>10} {str(r['probe_k']):>2} "
              f"{str(r['probe_best_1based']):>16} "
              f"{str(r['truth_first_click_0based']):>16} "
              f"{str(r['truth_first_is_mine']):>8} "
              f"{str(r['died']):>6} {str(r['opened']):>7}")

    out = args.out or os.path.join(ROOT, "state", "ab", "verify_fg.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
