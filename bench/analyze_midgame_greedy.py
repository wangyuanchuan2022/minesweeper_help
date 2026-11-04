# -*- coding: utf-8 -*-
"""中局「贪心」决策的**结构性弱项**测量：平局占多大比例、生产靠什么破平局。

=================== 这个脚本回答什么 ====================
用户问题："中局增加若干步前瞻是否有优势？如何优化这个贪心算法，
使得更容易找到胜率更高的解。"

在能做任何"前瞻"之前，必须先回答一个更便宜、更基础的问题：
**中局决策里，到底有多少是"多格安全率完全相等"的平局？**

理由（这是全套实验的骨架）：
  · 若某决策存在唯一最高安全率格，则贪心（max safety）在该步与任何
    "safety 相同、只看续玩价值"的前瞻**不可能分歧**——前瞻只可能在
    安全率**相等**的那些格里改选。
  · 若绝大部分中局决策都是大面积平局，则贪心的主要弱点不是"缺深度"，
    而是"缺破平局的好准则"；改进方向应是**换破平局准则**（便宜、可A/B），
    而不是加深搜索（昂贵、尾部爆炸）。

=================== 精确性（本脚本不用任何采样/近似）====================
  · 前沿格安全率：直接调用生产的 `_pbs_compute_dp`（生成函数 DP，数学精确）
  · 内部格安全率：生产侧的闭式 `1 - (a - mine_num - num10) / U`
    ⇒ 所有内部格安全率**恒等**（它们只受总雷数约束，无局部约束）
  · 平局判定：|safety - max| <= 1e-9（用 DP 精确值，不用生产 0.0005 容差）
  · 生产破平局规则：逐行照抄 `process_bigger_situation`（含 `open_num5x5`）

=================== 输出的关键量 ====================
  1. 决策分类：有确定安全格 / 唯一最高安全率 / 平局
  2. 平局集合大小与构成（前沿 vs 内部）
  3. **生产规则破平局后仍完全随机的那批格有多大**（真正的"抽奖集"）
  4. `open_num5x5` 的口径缺陷：它把**棋盘外**当"已开格"计数
     ⇒ 边/角格分数虚高 ⇒ 生产系统性偏好在边角开格。量化这个偏好强度。
  5. 平局集内"信息量代理"的离散度（有多大空间可供改进）

用法:
  python bench/analyze_midgame_greedy.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

TOL = 1e-9


def open_num5x5(w, h, cell_value, pos):
    """逐行复刻 utils/probability.py:1555 的 open_num5x5。

    ⚠️ 注意：它把**棋盘外**的位置也计为"已开"（i<1 or i>w ⇒ res+=1）。
    这是原实现的语义，本函数照抄以复现生产行为（并不代表它是对的）。
    """
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                res += 1
                continue
            if 0 <= cell_value[j][i] <= 8:
                res += 1
    return res


def frontier_adj(w, h, cell_value, pos):
    """5x5 窗口内"前线格数"（尚未打开的格）。与 open_num5x5 互补，
    且**不**把棋盘外计为已开 ⇒ 用来对比生产口径的偏差。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i == x and j == y:
                continue
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if cell_value[j][i] == 9 or cell_value[j][i] == 10:
                res += 1
    return res


def boundary_dist(w, h, pos):
    """到棋盘边界的最小距离（0=贴边，1=次边，…）。用于检验"边角偏好"。"""
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def main():
    path = os.path.join(ROOT, "bench", "data", "midgame_cases.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "midgame_an"), True)

    from utils.solver import Solver
    import numpy as np

    s = Solver()

    def bucket(lim):
        if lim <= 20:
            return "(8,20]"
        if lim <= 60:
            return "(20,60]"
        return "(60,inf]"

    rows = []
    skipped = {"no_world": 0, "dp_zero": 0, "err": 0}

    for ci, c in enumerate(cases):
        try:
            F = len(c["clicks"])
            U = len(c["clicks9"])
            total = int(c["total"])
            clicks = [tuple(p) for p in c["clicks"]]
            clicks9 = [tuple(p) for p in c["clicks9"]]
            res_list = [np.asarray(g, dtype=np.int32) for g in c["groups"]]
            ck = c["ck"]
            num10 = c["num10"]
            a = c["a"]
            w, h = c["w"], c["h"]
            cv = c["cell_value"]

            # ---- 精确前沿安全率（生产 DP，数学精确）----
            s.a = a
            s.w, s.h = w, h
            dp_res, mine_num, denom = s._pbs_compute_dp(
                num10, clicks, clicks9, res_list)
            if denom == 0.0 or len(dp_res) != F:
                skipped["dp_zero"] += 1
                continue
            front_safety = [float(v) for v in dp_res]

            # ---- 内部安全率（生产闭式，所有内部格完全相同）----
            mine9 = a - mine_num - num10
            p_int = 1.0 - (mine9 / U) if U else 0.0

            coords = clicks + clicks9
            safety = front_safety + [p_int] * U
            kinds = ["F"] * F + ["U"] * U
            smax = max(safety)
            tie = [i for i, v in enumerate(safety) if v >= smax - TOL]

            # ---- 生产会怎么选（逐行复刻 process_bigger_situation）----
            has_certain = any(v >= 0.99 for v in front_safety)
            if has_certain:
                prod_pick_set = [i for i, v in enumerate(front_safety) if v >= 0.99]
                prod_rule = "certain"
            else:
                prod_pick_set = []
                prod_rule = "greedy"
                max_val = max(front_safety)
                poses = [i for i, v in enumerate(front_safety)
                         if abs(max_val - v) <= 0.0005]
                if poses:
                    scores = [open_num5x5(w, h, cv, coords[i]) for i in poses]
                    m = max(scores)
                    prod_pick_set = [i for i, sc in zip(poses, scores) if sc == m]
                # 内部格反超时改选内部格
                if U and p_int > max_val:
                    scores = [open_num5x5(w, h, cv, coords[i])
                              for i in range(F, F + U)]
                    m = max(scores)
                    prod_pick_set = [i for i, sc in enumerate(scores) if sc == m]
                    prod_rule = "greedy_u"

            # ---- 平局集内的信息量代理离散度 ----
            tie_open = [open_num5x5(w, h, cv, coords[i]) for i in tie]
            tie_adj = [frontier_adj(w, h, cv, coords[i]) for i in tie]
            tie_bdist = [boundary_dist(w, h, coords[i]) for i in tie]

            # 生产最终"抽奖集"内：真正被随机均匀选中的格数
            residual = len(prod_pick_set)

            rows.append({
                "ci": ci, "bucket": bucket(c["limitation"]),
                "F": F, "U": U, "total": total,
                "limitation": c["limitation"], "n_cand": F + U,
                "smax": round(smax, 6),
                "n_tie": len(tie),
                "tie_kind": "F" if all(kinds[i] == "F" for i in tie)
                            else ("U" if all(kinds[i] == "U" for i in tie) else "mix"),
                "tie_frontier_n": sum(1 for i in tie if kinds[i] == "F"),
                "prod_rule": prod_rule,
                "residual_random_n": residual,
                "tie_open_min": min(tie_open), "tie_open_max": max(tie_open),
                "tie_adj_min": min(tie_adj), "tie_adj_max": max(tie_adj),
                "tie_bdist_min": min(tie_bdist), "tie_bdist_max": max(tie_bdist),
                # 生产破平局方案（open_num5x5 最大）的平均贴边距离
                "prod_pick_bdist": (
                    sum(boundary_dist(w, h, coords[i]) for i in prod_pick_set)
                    / max(1, len(prod_pick_set))),
                # 同样的抽奖集若按"靠近中心"挑，贴边距离能多大
                "best_center_bdist": max(
                    (boundary_dist(w, h, coords[i]) for i in tie), default=0),
                "prod_pick_open": (
                    sum(open_num5x5(w, h, cv, coords[i]) for i in prod_pick_set)
                    / max(1, len(prod_pick_set))),
                "tie_open_mean": sum(tie_open) / max(1, len(tie)),
                "prod_pick_is_tie": bool(set(prod_pick_set) <= set(tie)),
            })
        except Exception as e:                              # noqa: BLE001
            skipped["err"] += 1
            if skipped["err"] <= 3:
                print(f"  [{ci}] err {type(e).__name__}: {e}")

    print("=" * 96)
    print(f"中局决策结构分析：{len(rows)}/{len(cases)} 个决策点可分析"
          f"（跳过 {skipped}）")
    print("=" * 96)
    if not rows:
        return

    for b in ("(8,20]", "(20,60]", "(60,inf]"):
        sub = [r for r in rows if r["bucket"] == b]
        if not sub:
            continue
        print(f"\n[{b}] {len(sub)} 个决策点")
        print(f"  候选数 n_cand    中位 {sorted(r['n_cand'] for r in sub)[len(sub)//2]}")
        n_certain = sum(1 for r in sub if r["prod_rule"] == "certain")
        n_unique = sum(1 for r in sub if r["n_tie"] == 1)
        n_tie = sum(1 for r in sub if r["n_tie"] > 1)
        print(f"  有确定安全格        {n_certain}/{len(sub)}")
        print(f"  唯一最高安全率格    {n_unique}/{len(sub)}"
              f"  ← 这类决策**前瞻不可能分歧**（唯一最优安全率）")
        print(f"  平局(>1 格并列最高) {n_tie}/{len(sub)}"
              f"  = {n_tie/len(sub)*100:.1f}%")
        ties = [r for r in sub if r["n_tie"] > 1]
        if ties:
            nt = sorted(r["n_tie"] for r in ties)
            print(f"  平局集大小         中位 {nt[len(nt)//2]}  max {nt[-1]}"
                  f"（占候选 {sum(nt)/len(nt)/max(1,sum(r['n_cand'] for r in ties)/len(ties))*100:.1f}%）")
            kc = {}
            for r in ties:
                kc[r["tie_kind"]] = kc.get(r["tie_kind"], 0) + 1
            print(f"  平局构成           {kc}")
            resid = sorted(r["residual_random_n"] for r in ties)
            print(f"  生产破平后仍随机的格数 中位 {resid[len(resid)//2]}  max {resid[-1]}")
            same = sum(1 for r in ties if r["residual_random_n"] > 1)
            print(f"  → 生产实际在 ≥2 格里均匀随机选：{same}/{len(ties)}"
                  f" = {same/len(ties)*100:.1f}% 的平局决策")

    print("\n" + "=" * 96)
    print("生产破平局准则 open_num5x5 的**贴边偏好**检验")
    print("（open_num5x5 把棋盘外计为「已开」⇒ 边/角格分数虚高）")
    print("=" * 96)
    ties_all = [r for r in rows if r["n_tie"] > 1]
    if ties_all:
        gaps = [r["prod_pick_bdist"] - (sum(r["tie_bdist_min"] for _ in [0]) * 0)
                for r in ties_all]
        # 生产选中格的平均贴边距离 vs 平局集内可选的最近中心格
        lose = sum(1 for r in ties_all
                   if r["best_center_bdist"] > r["prod_pick_bdist"])
        print(f"  平局决策数 {len(ties_all)}")
        print(f"  其中『平局集里存在明显更靠中心的格，但生产选了更贴边的格』："
              f"{lose}/{len(ties_all)} = {lose/len(ties_all)*100:.1f}%")
        d = [r["best_center_bdist"] - r["prod_pick_bdist"] for r in ties_all]
        d.sort()
        print(f"  贴边距离差（可选最优 − 生产实选）："
              f"中位 {d[len(d)//2]}  max {d[-1]}")

    print("\n" + "=" * 96)
    print("平局集内『信息量代理』的离散度（改进空间大小）")
    print("=" * 96)
    if ties_all:
        span_open = [r["tie_open_max"] - r["tie_open_min"] for r in ties_all]
        span_adj = [r["tie_adj_max"] - r["tie_adj_min"] for r in ties_all]
        print(f"  open_num5x5 跨度（max−min）：中位 {sorted(span_open)[len(span_open)//2]}"
              f"  p90 {sorted(span_open)[int(0.9*len(span_open))]}")
        print(f"  前线邻近数跨度（max−min）：中位 {sorted(span_adj)[len(span_adj)//2]}"
              f"  p90 {sorted(span_adj)[int(0.9*len(span_adj))]}")

    out = os.path.join(ROOT, "state", "ab", "midgame_greedy_struct.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "skipped": skipped}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
