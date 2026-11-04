# -*- coding: utf-8 -*-
"""破平局规则的空间：生产 `open_num5x5` 到底选了什么、换规则能改多少。

=================== 独立问题：不需要精确 V* ====================
中局 82% 的决策 world 数达 1e24 量级 ⇒ 胜率算不出来。但有一个问题
**完全不依赖胜率**就能回答，而且它决定了"优化空间有多大"：

    在这些决策里，安全率最高的格有几个？生产最终落在几个格上随机选？
    生产的破平局打分函数 `open_num5x5` 在平局集内选了哪一个？

本脚本只读已采集的决策点快照，纯确定性计算（无采样、无递归）：
  1. 用生产 DP 精确算每格安全率（`_pbs_compute_dp`，数学精确）
  2. 平局集 = 安全率等于最大值的所有候选
  3. 生产破平局打分 = 逐行照抄 `process_bigger_situation`
  4. 各替代规则在同一平局集里的选格，以及它们与生产的分歧率

=================== 本脚本发现的核心缺陷 ====================
`open_num5x5`（utils/probability.py:1555）对 **5x5 窗口里越出棋盘的位置**
也 `res += 1`（当作"已开格"）⇒ 贴边/角落格分数虚高（角落最多 +5）。
而它的设计意图是"附近已开格越多 ⇒ 越可能是 0 ⇒ 连锁展开越多"。
⇒ 边角偏好是这个实现**副作用**，不是设计意图。本脚本量化这个副作用。

用法: python bench/analyze_tiebreak_room.py
"""
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


def open5x5_raw(w, h, cv, pos):
    """生产原实现：越界计为已开（照抄，不修）。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                res += 1
                continue
            if 0 <= cv[j][i] <= 8:
                res += 1
    return res


def open5x5_fixed(w, h, cv, pos):
    """修正版：只数**真实存在**的已开格（越界跳过，不计分）。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if 0 <= cv[j][i] <= 8:
                res += 1
    return res


def frontier_adj(w, h, cv, pos):
    """5x5 内未开格数（含标记雷）。'周围还有多少未知' —— 与已开数互补。"""
    x, y = pos
    res = 0
    for i in range(x - 2, x + 3):
        for j in range(y - 2, y + 3):
            if i == x and j == y:
                continue
            if i < 1 or i > w or j < 1 or j > h:
                continue
            if cv[j][i] == 9 or cv[j][i] == 10:
                res += 1
    return res


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def bucket(lim):
    if lim <= 20:
        return "(8,20]"
    if lim <= 60:
        return "(20,60]"
    return "(60,inf]"


def main():
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "tiebreak"), True)
    import numpy as np
    from utils.solver import Solver

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    s = Solver()
    rows = []
    skipped = {"dp_zero": 0, "err": 0}

    for c in cases:
        try:
            F, U = len(c["clicks"]), len(c["clicks9"])
            clicks = [tuple(p) for p in c["clicks"]]
            clicks9 = [tuple(p) for p in c["clicks9"]]
            res_list = [np.asarray(g, dtype=np.int32) for g in c["groups"]]
            s.a, s.w, s.h = int(c["a"]), int(c["w"]), int(c["h"])
            dp_res, mine_num, denom = s._pbs_compute_dp(
                int(c["num10"]), clicks, clicks9, res_list)
            if denom == 0.0 or len(dp_res) != F:
                skipped["dp_zero"] += 1
                continue
            fs = [float(v) for v in dp_res]

            mine9 = s.a - mine_num - int(c["num10"])
            p_int = 1.0 - (mine9 / U) if U else 0.0
            coords = clicks + clicks9
            safety = fs + [p_int] * U
            smax = max(safety)
            tie = [i for i, v in enumerate(safety) if v >= smax - 1e-9]
            cv = c["cell_value"]
            w, h = int(c["w"]), int(c["h"])

            has_certain = smax >= 0.99 - 1e-12
            rec = {
                "limitation": c["limitation"], "bucket": bucket(c["limitation"]),
                "F": F, "U": U, "total": c["total"],
                "n_cand": F + U, "n_tie": len(tie),
                "tie_frontier": sum(1 for i in tie if i < F),
                "smax": round(smax, 6), "has_certain": bool(has_certain),
            }
            if has_certain or len(tie) == 1:
                # 没有破平局自由度：所有规则选同一组格
                rows.append(rec)
                continue

            scores_raw = [open5x5_raw(w, h, cv, coords[i]) for i in tie]
            scores_fix = [open5x5_fixed(w, h, cv, coords[i]) for i in tie]
            scores_fa = [frontier_adj(w, h, cv, coords[i]) for i in tie]
            # 生产只在**前沿格**里破平局（内部格仅当 p_int 反超时改选）
            tie_f = [i for i in tie if i < F]
            tie_u = [i for i in tie if i >= F]
            if tie_f and not (tie_u and p_int > smax + 1e-12):
                pool = tie_f
            else:
                pool = tie

            def pick(scorer):
                vals = {i: scorer(w, h, cv, coords[i]) for i in pool}
                m = max(vals.values())
                return {i for i, v in vals.items() if v == m}

            p_raw = pick(open5x5_raw)
            p_fix = pick(open5x5_fixed)
            p_fa = pick(frontier_adj)
            rec.update({
                "pool": len(pool),
                "pick_raw_n": len(p_raw),
                "pick_fix_n": len(p_fix),
                "same_raw_fix": bool(p_raw == p_fix),
                "disjoint_raw_fix": bool(p_raw.isdisjoint(p_fix)),
                # 平局集内贴边距离：raw 选的 vs 修正选的 vs 平局集最优
                "bdist_raw": sum(bdist(w, h, coords[i]) for i in p_raw) / len(p_raw),
                "bdist_fix": sum(bdist(w, h, coords[i]) for i in p_fix) / len(p_fix),
                "bdist_best": max(bdist(w, h, coords[i]) for i in pool),
                "open_raw_of_raw": sum(open5x5_raw(w, h, cv, coords[i])
                                       for i in p_raw) / len(p_raw),
                "fix_of_fix": sum(open5x5_fixed(w, h, cv, coords[i])
                                  for i in p_fix) / len(p_fix),
                "fix_of_raw": sum(open5x5_fixed(w, h, cv, coords[i])
                                  for i in p_raw) / len(p_raw),
                "fa_of_fix": sum(frontier_adj(w, h, cv, coords[i])
                                 for i in p_fix) / len(p_fix),
                "fa_of_raw": sum(frontier_adj(w, h, cv, coords[i])
                                 for i in p_raw) / len(p_raw),
                "same_raw_fa": bool(p_raw == p_fa),
            })
            rows.append(rec)
        except Exception:                                   # noqa: BLE001
            skipped["err"] += 1

    print("=" * 98)
    print(f"破平局规则空间分析：{len(rows)}/{len(cases)} 个决策点"
          f"（跳过 {skipped}）")
    print("=" * 98)

    for b in ("(8,20]", "(20,60]", "(60,inf]"):
        sub = [r for r in rows if r["bucket"] == b]
        if not sub:
            continue
        nt = [r for r in sub if r["n_tie"] > 1 and not r["has_certain"]]
        print(f"\n[{b}] {len(sub)} 点；其中有破平局自由度 {len(nt)} 点"
              f" = {len(nt)/len(sub)*100:.1f}%")
        if not nt:
            continue
        k = sorted(r["n_tie"] for r in nt)
        print(f"  平局集大小：中位 {k[len(k)//2]}  max {k[-1]}")
        rn = sorted(r["pick_raw_n"] for r in nt)
        print(f"  生产破平后仍在 ≥2 格随机选："
              f"{sum(1 for x in rn if x > 1)}/{len(nt)}"
              f"（中位抽奖集 {rn[len(rn)//2]} 格）")
        same = sum(1 for r in nt if r["same_raw_fix"])
        dis = sum(1 for r in nt if r["disjoint_raw_fix"])
        print(f"  改用『不数越界』修正版：选格完全相同 {same}/{len(nt)}"
              f" = {same/len(nt)*100:.1f}%；完全不同 {dis}/{len(nt)}"
              f" = {dis/len(nt)*100:.1f}%")
        better = sum(1 for r in nt if r["fix_of_fix"] > r["fix_of_raw"] + 1e-12)
        worse = sum(1 for r in nt if r["fix_of_fix"] < r["fix_of_raw"] - 1e-12)
        print(f"  修正版选中的格『真实已开邻居数』更高 {better}／更低 {worse}"
              f"／相同 {len(nt)-better-worse}")

    print("\n" + "=" * 98)
    print("`open_num5x5` 的边角副作用（只在有破平局自由度的决策上统计）")
    print("=" * 98)
    nt = [r for r in rows if r["n_tie"] > 1 and not r["has_certain"]]
    if nt:
        edge_pick = sum(1 for r in nt if r["bdist_raw"] == 0)
        print(f"  生产选中格含贴边(bdist=0)的决策：{edge_pick}/{len(nt)}"
              f" = {edge_pick/len(nt)*100:.1f}%")
        tie_has_center = [r for r in nt if r["bdist_best"] > r["bdist_raw"]]
        print(f"  平局集里存在更靠中心的格、但生产选了更贴边的："
              f"{len(tie_has_center)}/{len(nt)} = "
              f"{len(tie_has_center)/len(nt)*100:.1f}%")
        d = [r["bdist_best"] - r["bdist_raw"] for r in nt]
        d.sort()
        print(f"  贴边距离差（平局集最优 − 生产实选）："
              f"中位 {d[len(d)//2]:.1f}  p90 {d[int(0.9*len(d))]:.1f}  max {d[-1]:.1f}")

    out = os.path.join(ROOT, "state", "ab", "tiebreak_room.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "skipped": skipped}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
