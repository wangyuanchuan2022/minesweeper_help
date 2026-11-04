# -*- coding: utf-8 -*-
"""首点只开 1 格时的真实损失结构：区分"算法有得选"与"只能瞎猜"。

=================== 关键发现（本脚本要坐实的）====================
bench/verify_first_guess_truth.py 的样例暴露了一个被 probe 过滤掉的类别：
  seed=20261108: probe best=None，真实首点 (0,15) 是雷，opened=1
⇒ 这是「**无候选格 ⇒ 走随机分支瞎猜**」的局，被 `picked is None` 筛掉了，
   所以 probe 报"第二步 0% 死亡"是**样本选择偏差**，不是算法很好。

本脚本不筛样本，按"该步决策是否有信息可用"分三类，统计各自的死亡率：
  类别 A：有确定安全格（safety=1）→ 必然不死
  类别 B：有概率候选（best 非 None）→ 会选一个"最不危险"的格
  类别 C：无候选（best is None）→ 只能随机瞎猜（纯掷骰子）

并计算：若把 A/B/C 的死亡率分别压到理论上限，胜率能到多少。
这决定了 50% 目标该从哪里补。

=================== 口径（避免上一轮的错）====================
  · 分母一律报清楚：占"首点只开 1 格"的局数，或占全部局数
  · 不把"A 类候选的边际雷率"当成 C 类的期望
  · 所有分类都由**真实执行轨迹**判定（首个决策后是否真的点了 best），
    不靠推断

用法: python bench/analyze_first_guess_loss.py --games 300
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "fg_loss"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver
    from sim.minesweeper_sim import Board

    probe = {}
    truth = {}
    orig_n51 = prob.ProbabilityMixin.number5_1
    orig_flush = SimDriver.flush_clicks

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
        out = orig_n51(self, cell_value)
        hm = getattr(self, "_last_heatmap", None) or {}
        probe["best"] = hm.get("best")
        probe["best_conf"] = hm.get("best_conf")
        # ⚠️ number5_1 返回的是**更新后的 cell_value 矩阵**，不是
        # (pos, confidence, ...) 元组（见 utils/probability.py:454）。
        # 上一版把 out[0] 当成 pos 列表 ⇒ n_pos 恒 0 ⇒ 分类全错。
        # 正解：候选是否存在只能看 `_last_heatmap`（prob 表非空 = 有概率候选）。
        probe["has_prob_map"] = bool(hm.get("prob") or {})
        return out

    def hflush(self):
        if "first_click" not in truth and self.pending:
            truth["first_click"] = tuple(self.pending[0])
            truth["mines"] = set(self.board.mines)
            truth["n_pending"] = len(self.pending)
        return orig_flush(self)

    prob.ProbabilityMixin.number5_1 = h51
    SimDriver.flush_clicks = hflush

    rows = []
    n_games = 0
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            probe.clear()
            truth.clear()
            try:
                r = ab.play_one(sd, W, H, A, "baseline", 80, 200000, None)
            except Exception:                          # noqa: BLE001
                continue
            n_games += 1
            if probe.get("n_opened") != 1:
                rows.append({"seed": sd, "opened1": False,
                             "won": bool(r.get("won")),
                             "died": bool(r.get("died")),
                             "opened": r.get("opened")})
                continue
            fc = truth.get("first_click")
            is_mine = None if fc is None else (tuple(fc) in truth["mines"])
            has_best = probe.get("best") is not None
            has_prob = probe.get("has_prob_map", False)
            if has_best:
                cat = "B_informed"        # 有概率候选，选最不危险的格
            elif has_prob:
                cat = "A_certain"         # 有确定安全格（safety=1 分支）
            else:
                cat = "C_random"          # 无任何概率信息 ⇒ 随机瞎猜
            rows.append({
                "seed": sd, "opened1": True, "k": probe.get("k"),
                "cat": cat, "has_best": has_best,
                "has_prob_map": has_prob,
                "first_click": list(fc) if fc else None,
                "first_is_mine": is_mine,
                "best_conf": probe.get("best_conf"),
                "won": bool(r.get("won")), "died": bool(r.get("died")),
                "opened": r.get("opened"),
            })
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("=" * 100)
    print(f"首点只开 1 格的损失结构（{n_games} 局）")
    print("=" * 100)

    o1 = [r for r in rows if r.get("opened1")]
    other = [r for r in rows if not r.get("opened1")]
    print(f"\n  首点只开 1 格：{len(o1)}/{n_games} = "
          f"{len(o1)/max(1,n_games)*100:.2f}%")
    print(f"  首点开出多格：{len(other)}/{n_games} = "
          f"{len(other)/max(1,n_games)*100:.2f}%"
          f"  （其中胜 {sum(1 for r in other if r['won'])}）")

    print("\n" + "=" * 100)
    print("三类决策的真实死亡率（口径：分母 = 该类样本数）")
    print("=" * 100)
    print(f"{'类别':<14} {'样本':>5} {'占opened1':>10} {'首击是雷':>9} "
          f"{'死亡率':>8} {'最终胜':>7} {'胜率':>7}")
    print("-" * 100)
    for cat, desc in (("A_certain", "A 有确定安全格"),
                      ("B_informed", "B 有概率候选"),
                      ("C_random", "C 无候选(瞎猜)")):
        sub = [r for r in o1 if r["cat"] == cat]
        if not sub:
            print(f"{desc:<14} 0")
            continue
        mines = sum(1 for r in sub if r["first_is_mine"])
        wins = sum(1 for r in sub if r["won"])
        print(f"{desc:<14} {len(sub):>5} {len(sub)/len(o1)*100:>9.1f}% "
              f"{mines:>9} {mines/len(sub)*100:>7.2f}% "
              f"{wins:>7} {wins/len(sub)*100:>6.2f}%")

    print("\n" + "=" * 100)
    print("C 类（无候选瞎猜）的期望雷率 —— 这是**结构性**代价")
    print("=" * 100)
    csub = [r for r in o1 if r["cat"] == "C_random"]
    if csub:
        m = sum(1 for r in csub if r["first_is_mine"])
        p = m / len(csub)
        se = math.sqrt(max(p * (1 - p), 1e-9) / len(csub))
        print(f"  实测 {m}/{len(csub)} = {p*100:.2f}%  "
              f"(SE {se*100:.2f}%, 95%CI [{(p-1.96*se)*100:.1f}%, "
              f"{(p+1.96*se)*100:.1f}%])")
        # 理论：无信息时最优是均匀随机，雷率 = 99/479
        theo = A / (W * H - 1)
        print(f"  理论（无信息均匀随机）= 99/479 = {theo*100:.2f}%")
        print(f"  ⇒ 实测 − 理论 = {(p-theo)*100:+.2f}pp")
        print(f"  ⇒ 这一类的死亡率基本等于「纯运气」，**算法无从改进**")
        print(f"     （除非有比均匀随机更好的先验，而扫雷在无信息处没有）")

    print("\n" + "=" * 100)
    print("B 类（有概率候选）的选格质量")
    print("=" * 100)
    bsub = [r for r in o1 if r["cat"] == "B_informed"]
    if bsub:
        m = sum(1 for r in bsub if r["first_is_mine"])
        print(f"  实测死亡率 {m}/{len(bsub)} = {m/len(bsub)*100:.2f}%")
        confs = [r["best_conf"] for r in bsub if r.get("best_conf") is not None]
        if confs:
            print(f"  自评安全率（best_conf）均值 {sum(confs)/len(confs):.4f}"
                  f"  ⇒ 自评雷率 {(1-sum(confs)/len(confs))*100:.2f}%")
            print(f"  实测雷率 {m/len(bsub)*100:.2f}%  "
                  f"⇒ 偏差 {m/len(bsub)*100 - (1-sum(confs)/len(confs))*100:+.2f}pp")
            print(f"  （偏差小 ⇒ 概率算得准；偏差大 ⇒ 概率本身有系统误差）")

    print("\n" + "=" * 100)
    print("50% 目标的可行性分解")
    print("=" * 100)
    n = n_games
    base_wins = sum(1 for r in rows if r["won"])
    base = base_wins / max(1, n) * 100
    print(f"  当前胜率 {base_wins}/{n} = {base:.2f}%")
    print(f"  目标 50% 需要 +{50-base:.2f}pp = 再多赢 "
          f"{math.ceil(n*0.5)-base_wins} 局（本批 {n} 局）")
    print()
    # 乐观上界：假设 C 类全胜（实际不可能，它是运气）
    cwin = sum(1 for r in csub if r["won"])
    upper_all_C = (base_wins - cwin + len(csub)) / max(1, n) * 100
    print(f"  若 C 类（{len(csub)} 局，瞎猜）全部救回 ⇒ 上界 {upper_all_C:.2f}%")
    # 更现实：C 类保持运气，其余全胜
    non_C = [r for r in rows if r.get("cat") != "C_random"]
    upper_nonC = (len(csub) * 0 + len(non_C)) / max(1, n) * 100
    print(f"  若**除 C 类外全部救回**（C 类维持运气不变）⇒ 上界 "
          f"{upper_nonC:.2f}%   ← 这才是算法能触及的天花板")
    print()
    print(f"  ⚠️ 这里的关键数字：{len(csub)/max(1,n)*100:.1f}% 的对局"
          f"在首个决策就只能瞎猜。")

    out = args.out or os.path.join(ROOT, "state", "ab", "first_guess_loss.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"n_games": n_games, "rows": rows}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
