# -*- coding: utf-8 -*-
"""探针：首点只开出 1 格时，第二步的猜测是否已达理论最优？

=================== 数学判据（advisor 纠正后重写）====================
设首点开出**恰好 1 格**、其数字为 k。此刻：
  · 未开格 = 479 个，其中 8 个是首点的邻域格、471 个是内部格
  · 剩余雷数 = 99（首点已确认非雷）
  · 约束：8 个邻域格里恰有 k 颗雷

精确边际（不是"k/8 近似"，是**由对称性得到的精确值**）：
  · 每个邻域格：由 8 格对称性，各格边际相等且总和为 k
                ⇒ P(邻域格是雷) = **k/8**（精确）
  · 每个内部格：只受总雷数约束 ⇒ P = **(99−k)/471**（精确）

⚠️ 上一版我把"理论最优"写成恒 k/8 = 1/8（12.5%），**这是错的**：
   k 可以 > 1，且 k 大时内部格反而更优。
   比较：k/8 vs (99−k)/471
       k=1 → 0.125  vs 0.208  ⇒ 邻域格更优
       k=2 → 0.250  vs 0.206  ⇒ **内部格更优**
   转折点：471k < 8(99−k) ⇒ 479k < 792 ⇒ k < 1.653
   ⇒ **k=1 时最优是邻域格；k≥2 时最优是内部格。**

因此"理论最优雷率" = min(k/8, (99−k)/471)，且要分 k 层比较。
只有实际踩雷率**显著高于**这个 min，才能说算法选格有缺陷。

=================== 分母必须分清（上一版混用了）====================
  A) 占**全部对局**的比例：opened<=1 就死的局数 / 全部局数
     （2000 局里 295 局 ⇒ 14.75%）
  B) 占**首点只开 1 格**对局的比例：其中第二步踩雷的局数 / 首点只开 1 格的局数
     （60 局里 14 局 ⇒ 23.33%，但分母是"60 局中首点只开 1 格的那些"）
两者分母不同，**不可直接与 k/8 比较**。本脚本一律按 B 口径报，
并按 k 分层。

用法: python bench/probe_first_guess.py --games 400
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
N_UNOPENED_AFTER_FIRST = W * H - 1        # 479


def optimal_rate(k, n_nb=8, n_interior=None):
    """首点数字 k 时，第二步可达到的最小雷率（精确）。

    返回 (min_rate, best_kind)。
    """
    if n_interior is None:
        n_interior = N_UNOPENED_AFTER_FIRST - n_nb      # 471
    nb_rate = k / n_nb
    in_rate = (A - k) / n_interior
    if nb_rate <= in_rate:
        return nb_rate, "neighbor"
    return in_rate, "interior"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--rule", default="deferred",
                    choices=["deferred", "safe3x3"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "firstguess"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.minesweeper_sim import Board

    # 可选：替换首点规则
    orig_generate = Board.generate
    if args.rule != "deferred":
        def gen(self, first_x, first_y):
            safe = {(first_x + dx, first_y + dy)
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                    if 0 <= first_x + dx < W and 0 <= first_y + dy < H}
            pool = [(x, y) for y in range(H) for x in range(W)
                    if (x, y) not in safe]
            self.mines = set(self.rng.sample(pool, A))
            self.generated = True
        Board.generate = gen

    orig_n51 = prob.ProbabilityMixin.number5_1
    state = {}
    cur = {"board": None}
    orig_open = Board.open

    def hooked_open(self, x, y):
        cur["board"] = self
        return orig_open(self, x, y)

    Board.open = hooked_open

    def hooked_n51(self, cell_value):
        # 只观测**整局第一次**概率决策
        if state.get("armed"):
            return orig_n51(self, cell_value)

        state["armed"] = True
        # ⚠️ cell_value 是 (h+2, w+2) 且**外框恒为 0**（0 也是合法数字值），
        # 若直接统计 (0<=v<=8) 会把整圈外框算成"已开格"（实测得到 100+ 格，
        # 导致 n_opened != 1 的过滤把全部样本丢掉）。必须只数内区。
        inner = cell_value[1:H + 1, 1:W + 1]
        opened_mask = (inner >= 0) & (inner <= 8)
        state["n_opened"] = int(np.sum(opened_mask))
        ys, xs = np.where(opened_mask)
        if len(ys):
            state["first_num"] = int(inner[ys[0], xs[0]])
            state["first_xy"] = (int(xs[0]), int(ys[0]))

        # ⚠️ `_last_heatmap` 是**实例属性，会残留上一次决策的值**
        # （bench/verify_probe_chain.py 场景 B 实测：无可选格时调用后 best
        #   仍是旧值）。因此必须先快照调用前的 best，只有**发生变化**才认。
        hm_prev = getattr(self, "_last_heatmap", None) or {}
        prev_best = hm_prev.get("best")

        out = orig_n51(self, cell_value)

        # ⚠️ 不要 hook pyautogui.click：ab_policy.play_one 内部会执行
        # `pyautogui.click = drv.on_click`，把我方的 patch **覆盖掉**
        # （实测 picked 恒为 None）。改为直接读 `_last_heatmap["best"]`。
        hm = getattr(self, "_last_heatmap", None) or {}
        best = hm.get("best")
        changed = (best is not None and tuple(best) != tuple(prev_best)
                   if prev_best is not None else best is not None)
        state["best_changed"] = bool(changed)
        state["best_prev"] = prev_best
        if best is not None:
            state["picked"] = (int(best[0]) - 1, int(best[1]) - 1)
        state["best_conf"] = hm.get("best_conf")
        return out

    prob.ProbabilityMixin.number5_1 = hooked_n51

    records = []
    n_games = 0
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            state.clear()
            state["armed"] = False
            try:
                r = ab.play_one(sd, W, H, A, "baseline", 80, 200000, None)
            except Exception:                          # noqa: BLE001
                continue
            n_games += 1
            b = cur["board"]
            if b is None or state.get("picked") is None:
                continue
            # 残留防护：只认 best 相对调用前**发生了变化**的样本。
            # 依据 bench/verify_probe_chain.py 场景 B：_last_heatmap 会残留，
            # 不校验就会把上一次决策的选格当成这一次。
            if not state.get("best_changed"):
                continue
            if state.get("n_opened") != 1:
                continue
            gx, gy = state["picked"]
            if not (0 <= gx < W and 0 <= gy < H):
                continue
            k = state["first_num"]
            fx, fy = state["first_xy"]
            cheb = max(abs(gx - fx), abs(gy - fy))
            is_mine = (gx, gy) in b.mines
            opt, kind = optimal_rate(k)
            nb_rate = k / 8.0
            in_rate = (A - k) / 471.0
            records.append({
                "seed": sd, "first_num": k, "picked": [gx, gy],
                "cheb": cheb, "is_neighbor": cheb == 1,
                "is_mine": is_mine, "opt_rate": opt, "opt_kind": kind,
                "nb_rate": nb_rate, "in_rate": in_rate,
                "died": bool(r.get("died")),
                "opened": r.get("opened"),
                "won": bool(r.get("won")),
            })
            if (gi + 1) % 100 == 0:
                print(f"  ... {gi+1}/{args.games} 样本 {len(records)}",
                      flush=True)
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        Board.open = orig_open
        Board.generate = orig_generate

    if not records:
        print("未采到样本")
        return

    print("\n" + "=" * 100)
    print(f"首点只开 1 格的第二步猜测（rule={args.rule}）")
    print(f"总对局 {n_games}，其中首点只开 1 格 {len(records)} 局"
          f"（{len(records)/max(1,n_games)*100:.2f}%）")
    print("=" * 100)

    # ---------- 分层：按 k ----------
    print("\n按首点数字 k 分层（口径 B：分母 = 该 k 层样本数）")
    print(f"{'k':>3} {'样本':>5} {'第二步踩雷':>10} {'实际死亡率':>10} "
          f"{'理论最优':>9} {'最优类型':>9} {'差(pp)':>8} {'选邻域':>8}")
    print("-" * 100)
    from collections import Counter
    by_k = {}
    for r in records:
        by_k.setdefault(r["first_num"], []).append(r)
    tot_die = tot_n = 0
    for k in sorted(by_k):
        sub = by_k[k]
        d = sum(1 for r in sub if r["is_mine"])
        opt, kind = optimal_rate(k)
        near = sum(1 for r in sub if r["is_neighbor"])
        tot_die += d
        tot_n += len(sub)
        print(f"{k:>3} {len(sub):>5} {d:>10} {d/len(sub)*100:>9.2f}% "
              f"{opt*100:>8.2f}% {kind:>9} "
              f"{(d/len(sub)-opt)*100:>+7.2f} {near/len(sub)*100:>7.1f}%")

    print("-" * 100)
    opt_w = sum(r["opt_rate"] for r in records) / len(records)
    print(f"{'合计':>3} {tot_n:>5} {tot_die:>10} {tot_die/tot_n*100:>9.2f}% "
          f"{opt_w*100:>8.2f}% {'加权最优':>9} "
          f"{(tot_die/tot_n-opt_w)*100:>+7.2f}")

    print("\n" + "=" * 100)
    print("判定：实际第二步死亡率 是否 显著高于 理论最优加权值")
    print("=" * 100)
    diff = tot_die / tot_n - opt_w
    se = math.sqrt(max(opt_w * (1 - opt_w) / tot_n, 1e-12))
    z = diff / se if se > 0 else 0.0
    print(f"  实际 {tot_die/tot_n*100:.2f}%  vs  理论最优 {opt_w*100:.2f}%")
    print(f"  差 {diff*100:+.2f}pp，二项 SE ≈ {se*100:.2f}pp，z = {z:+.2f}")
    if z > 1.96:
        print(f"  ⇒ 实际**显著高于**理论最优 ⇒ 算法在这些局面上选格有改进空间")
    elif z < -1.96:
        print(f"  ⇒ 实际**显著低于**理论最优（更好）—— 检查判据或采样是否有偏")
    else:
        print(f"  ⇒ 与理论最优无显著差异 ⇒ 这部分不是算法缺陷，")
        print(f"     是 Arbiter 延迟布雷规则带来的结构性代价。")

    # ---------- 选格分布 ----------
    print("\n" + "=" * 100)
    print("生产选格的位置分布（切比雪夫距离 = max(|dx|,|dy|)；1 = 8 邻域）")
    print("=" * 100)
    dc = Counter(r["cheb"] for r in records)
    for c in sorted(dc):
        sub = [r for r in records if r["cheb"] == c]
        dm = sum(1 for r in sub if r["is_mine"])
        print(f"  距离={c:>2}: {dc[c]:>4} 局，踩雷率 {dm/len(sub)*100:>6.2f}%")

    # ---------- 与"最优类型"的一致性 ----------
    print("\n" + "=" * 100)
    print("生产选择是否落在『该 k 下的最优类型』上")
    print("=" * 100)
    agree = sum(1 for r in records
                if (r["opt_kind"] == "neighbor") == r["is_neighbor"])
    print(f"  一致 {agree}/{len(records)} = {agree/len(records)*100:.2f}%"
          f"（k=1 应选邻域；k>=2 应选内部）")

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   f"first_guess_{args.rule}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rule": args.rule, "n_games": n_games,
                   "records": records}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
