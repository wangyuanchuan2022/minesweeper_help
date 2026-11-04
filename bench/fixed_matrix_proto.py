# -*- coding: utf-8 -*-
"""P0-3 修复原型：多终端分支的正确聚合（**新文件，不改原脚本**）。

=================== 缺陷回顾（审计 P0-3，已实测确认）====================
`bench/dump_metric_matrix_ext.py:316-317`：
    terms.sort(key=lambda t: -t[2])
    wk2, op2, _wt = terms[0]        # ← 只保留权重最大的单个终端分支

而 `arms_lookahead.py:298-328` 的 `saturate()` 在多观测分支时返回**多个**终端态
（每个带自己的 weight）。除 top 外的终端被静默丢弃。

实测（`review/第1-3步结项_*.md` 第 3.2 节，前 60 个真实 ok 点）：
  · 12/60 = 20.0% 的点存在多终端分支（最多 192 个）
  · 丢弃概率质量中位 81.5%、均值 76.8%、最大 98.6%

=================== 正确做法 ====================
参考 `ExactEngine.V_exact`（arms_lookahead.py:368-386）的既有正确聚合：
    val = Σ_terms  weight × value(term)
即**按 weight 加权求和全部分支**，而不是取 max。

对指标 F 的聚合：每个终端 t 有自己的候选集 cands(t)（可能不同）。
对某个格 cell，定义：
    F_agg(cell) = Σ_{t : cell ∈ cands(t)}  weight(t) × F_t(cell)
               / Σ_{t : cell ∈ cands(t)}  weight(t)
（对未出现在某终端候选集里的格，用该终端上的覆盖权重做归一化，
 避免"只在少数终端出现的格"被系统性压低或抬高。）

⚠️ 关于归一化的一个重要取舍：
原脚本对**每个指标**在**单终端的候选集内**做 min-max 归一化。
多终端下若先各自归一化再聚合，与"先聚合原始值再归一化"结果不同。
本原型采用**先聚合原始值、再在并集候选集上归一化**（与单终端情形一致，
单终端时退化为原行为 ⇒ 可与原脚本逐位对拍验证）。

=================== 验证要求（必须先通过）====================
1. 单终端点：修复版输出必须与原脚本**逐位一致**（max|Δ| ≤ 1e-12）；
2. 审计的 6×2 构造用例：聚合值 = Σ(weight × 逐终端值)；
3. 多终端真实点：报告修复前后 q/F 差异幅度与额外耗时。

用法:
  python bench/fixed_matrix_proto.py --verify-single     # 单终端等价性
  python bench/fixed_matrix_proto.py --compare-real 12   # 真实多终端点对比
"""
import argparse
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np                                        # noqa: E402
import utils.native as native                             # noqa: E402
from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402
from dump_metric_matrix_ext import (                       # noqa: E402
    METRICS, case_key, count_worlds, compute_metrics, minmax, bdist)

PAD = -1e18


def terminals_of(eng, wk_all, op0):
    """与脚本同口径的终端过滤，但保留**全部**终端（不排序取首个）。"""
    terms = [t for t in eng.saturate(wk_all, op0)
             if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
    return terms


def raw_metrics_union(eng, terms, c):
    """在全部终端上算原始指标，按 weight 聚合到并集候选集。

    返回 (coords, raw_agg, diag)：
      coords   —— 并集候选坐标（按稳定顺序）
      raw_agg  —— {metric: {coord: 聚合原始值}}
      diag     —— 聚合诊断（终端数、覆盖质量等）
    """
    w, h = int(c["w"]), int(c["h"])
    # 1) 每个终端的候选坐标集与原始指标
    per_term = []
    for wk, op, wt in terms:
        cands = eng.candidates(wk, op)
        if not cands:
            continue
        raw = compute_metrics(eng, wk, op, cands, c)
        coord = {cd: eng._coord(cd) for cd in cands}
        per_term.append({"wt": float(wt), "cands": cands,
                         "coord": coord, "raw": raw})

    if not per_term:
        return [], {}, {"n_terms": len(terms), "n_used": 0}

    # 2) 并集候选（稳定顺序：按 (y, x) 排序）
    coord_set = {}
    for t in per_term:
        for cd, xy in t["coord"].items():
            coord_set[xy] = True
    coords = sorted(coord_set.keys(), key=lambda p: (p[1], p[0]))

    # 3) 按 cell 聚合：Σ wt·raw / Σ wt（仅对包含该格的终端）
    agg = {m: {} for m in METRICS}
    cov_w = {}                       # 每个坐标被覆盖的权重合计
    for xy in coords:
        num = {m: 0.0 for m in METRICS}
        den = 0.0
        for t in per_term:
            # 反查该坐标是否在该终端候选内
            cd = None
            for c2, xy2 in t["coord"].items():
                if xy2 == xy:
                    cd = c2
                    break
            if cd is None:
                continue
            wt = t["wt"]
            den += wt
            for m in METRICS:
                num[m] += wt * t["raw"][m][cd]
        if den <= 0:
            continue
        cov_w[xy] = den
        for m in METRICS:
            agg[m][xy] = num[m] / den

    diag = {
        "n_terms": len(terms), "n_used": len(per_term),
        "top_weight": max(t["wt"] for t in per_term),
        "sum_weight": sum(t["wt"] for t in per_term),
        "n_coords_union": len(coords),
    }
    diag["dropped_mass"] = 1.0 - diag["top_weight"] / diag["sum_weight"] \
        if diag["sum_weight"] > 0 else 0.0
    return coords, agg, diag


def normalize(agg, coords):
    """在并集候选集上做 min-max 归一化（R 取反），与脚本同口径。"""
    norm = {}
    for m in METRICS:
        d = {xy: agg[m][xy] for xy in coords if xy in agg[m]}
        nv = minmax(d)
        if m == "R":
            nv = {k: 1.0 - v for k, v in nv.items()}
        norm[m] = nv
    return norm


def q_from_terminals(eng, terms, c, node_cap):
    """q 的真值：在**并集**世界集上调用 C++ exact_eval（与 F 同源）。

    ⚠️ 关键修复点：原脚本用 terms[0] 的世界集（wk2）算 q 与 F；
    修复版应使用**全部终端的并集世界集**，使 q 与 F 覆盖同一个世界分布。

    这里采用加权聚合的替代方案：逐终端调用 exact_eval 得 q_t，
    再按 weight 聚合。这样避免构造并集世界集（并集可能很大）。
    """
    per = []
    for wk, op, wt in terms:
        cands = eng.candidates(wk, op)
        if not cands:
            continue
        try:
            worlds = [format(eng.world_bits[k], "x") for k in wk]
            cv = np.asarray(c["cell_value"], dtype=np.int32)
            ql, co, _nodes, v_opt = native.mscore.exact_eval(
                worlds, [tuple(x) for x in c["clicks"]],
                [tuple(x) for x in c["clicks9"]], cv,
                int(c["a"]), int(c["w"]), int(c["h"]), node_cap, 0)
        except Exception as e:                              # noqa: BLE001
            return None, "exact_eval_fail:" + type(e).__name__, None
        if not ql:
            return None, "empty_q", None
        coord_to_q = {}
        for (x, y), v in zip(co, ql):
            coord_to_q[(int(x), int(y))] = float(v)
        per.append({"wt": float(wt), "q": coord_to_q, "v_opt": float(v_opt)})
    if not per:
        return None, "no_terminals", None

    # 按 weight 聚合 q（只对包含该坐标的终端）
    coords = sorted({xy for t in per for xy in t["q"]}, key=lambda p: (p[1], p[0]))
    q_agg, v_agg = {}, 0.0
    for xy in coords:
        num = 0.0
        den = 0.0
        for t in per:
            if xy in t["q"]:
                num += t["wt"] * t["q"][xy]
                den += t["wt"]
        if den > 0:
            q_agg[xy] = num / den
    tot = sum(t["wt"] for t in per)
    if tot > 0:
        v_agg = sum(t["wt"] * t["v_opt"] for t in per) / tot
    return q_agg, None, v_agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-single", action="store_true",
                    help="单终端点：修复版必须与原脚本逐位一致")
    ap.add_argument("--compare-real", type=int, default=0,
                    help="取 N 个多终端真实点，对比修复前后差异")
    ap.add_argument("--node-cap", type=int, default=20_000_000)
    ap.add_argument("--time-cap", type=float, default=90.0)
    args = ap.parse_args()

    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for c in json.load(f)["cases"]:
                    merged[case_key(c)] = c

    okkeys = []
    jp = os.path.join(ROOT, "state", "ab", "mm_full.jsonl")
    if os.path.exists(jp):
        with open(jp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("status") == "ok":
                    okkeys.append(r["key"])
    import ast
    cases = []
    for k in okkeys:
        try:
            kk = ast.literal_eval(k)
        except Exception:
            continue
        c = merged.get(kk)
        if c is not None:
            cases.append((k, c))
    print("可用 ok 点：%d（来自 mm_full journal）" % len(cases))

    if args.verify_single:
        print()
        print("=" * 100)
        print("验证 1：单终端点 —— 修复版必须与原脚本逐位一致")
        print("=" * 100)
        n_single = 0
        max_abs = 0.0
        n_cmp = 0
        for k, c in cases:
            try:
                eng = ExactEngine(c, node_cap=args.node_cap,
                                  time_cap=args.time_cap, world_cap=None)
                wk = WKey.all(eng.n_worlds)
                terms = terminals_of(eng, wk, eng.opened0)
            except Exception:
                continue
            if len(terms) != 1:
                continue
            n_single += 1
            if n_single > 8:
                break
            # 原路径：直接在该终端上算
            wk2, op2, _ = terms[0]
            cands = eng.candidates(wk2, op2)
            raw_orig = compute_metrics(eng, wk2, op2, cands, c)
            orig = {}
            for m in METRICS:
                nv = minmax(raw_orig[m])
                if m == "R":
                    nv = {k2: 1.0 - v for k2, v in nv.items()}
                orig[m] = {eng._coord(cd): nv[cd] for cd in cands}
            # 修复版
            coords, agg, diag = raw_metrics_union(eng, terms, c)
            norm = normalize(agg, coords)
            # 对比
            for m in METRICS:
                for xy in coords:
                    if xy in orig[m] and xy in norm[m]:
                        d = abs(orig[m][xy] - norm[m][xy])
                        max_abs = max(max_abs, d)
                        n_cmp += 1
            print("    点 %d：单终端，候选 %d，已对比（累计 %d 次比较，max|Δ|=%.3e）"
                  % (n_single, len(coords), n_cmp, max_abs), flush=True)
        print()
        ok = (max_abs <= 1e-12) and n_single > 0
        print("  单终端点数 = %d，比较次数 = %d，max|Δ| = %.3e（门限 1e-12）"
              % (n_single, n_cmp, max_abs))
        print("  ⇒ %s" % ("通过（修复版在单终端下退化为原行为）" if ok
                          else "**失败**（修复版改变了无截断情形的结果）"))
        return 0 if ok else 2

    if args.compare_real:
        print()
        print("=" * 100)
        print("验证 2：多终端真实点 —— 修复前后差异幅度与额外耗时")
        print("=" * 100)
        shown = 0
        rows = []
        for k, c in cases:
            if shown >= args.compare_real:
                break
            t0 = time.perf_counter()
            try:
                eng = ExactEngine(c, node_cap=args.node_cap,
                                  time_cap=args.time_cap, world_cap=None)
                wk = WKey.all(eng.n_worlds)
                terms = terminals_of(eng, wk, eng.opened0)
            except Exception:
                continue
            if len(terms) <= 1:
                continue
            shown += 1
            # 截断版（原脚本行为）
            s = sorted(terms, key=lambda t: -t[2])
            wk2, op2, _ = s[0]
            cands0 = eng.candidates(wk2, op2)
            q0, err0, v0 = q_from_terminals(eng, [s[0]], c, args.node_cap)
            if err0:
                print("    点%d 截断版 q 失败：%s" % (shown, err0)); continue
            # 修复版
            qf, errf, vf = q_from_terminals(eng, terms, c, args.node_cap)
            if errf:
                print("    点%d 修复版 q 失败：%s" % (shown, errf)); continue
            coords, agg, diag = raw_metrics_union(eng, terms, c)
            el = time.perf_counter() - t0
            # q 差异（共同坐标上）
            common = set(q0) & set(qf)
            if common:
                dq = [abs(q0[xy] - qf[xy]) for xy in common]
                mean_dq = sum(dq) / len(dq)
                max_dq = max(dq)
            else:
                mean_dq = max_dq = float("nan")
            # q 的最优值差（最关键的决策指标）
            best0 = max(q0.values())
            bestf = max(qf.values())
            rows.append({
                "n_terms": diag["n_terms"],
                "dropped_mass": diag["dropped_mass"],
                "mean_dq": mean_dq, "max_dq": max_dq,
                "best_q_trunc": best0, "best_q_fixed": bestf,
                "best_delta": bestf - best0,
                "secs": el,
            })
            print("    点%d: 终端 %3d，丢弃质量 %5.1f%%，q 平均Δ %.3e、最大Δ %.3e，"
                  "opt q 截断 %.4f → 修复 %.4f（Δ%+.4f），耗时 %.1fs"
                  % (shown, diag["n_terms"], diag["dropped_mass"] * 100,
                     mean_dq, max_dq, best0, bestf, bestf - best0, el), flush=True)

        if rows:
            print()
            n = len(rows)
            print("  汇总（%d 个多终端真实点）：" % n)
            print("    终端数中位 %.0f" % sorted(r["n_terms"] for r in rows)[n // 2])
            print("    丢弃质量中位 %.1f%%"
                  % (sorted(r["dropped_mass"] for r in rows)[n // 2] * 100))
            print("    q 平均Δ 中位 %.3e"
                  % sorted(r["mean_dq"] for r in rows)[n // 2])
            print("    opt q 变化中位 %+.4f（正=修复后更乐观）"
                  % sorted(r["best_delta"] for r in rows)[n // 2])
            print("    单点耗时中位 %.1fs，合计 %.1fs" % (
                sorted(r["secs"] for r in rows)[n // 2], sum(r["secs"] for r in rows)))
            frac = sum(1 for r in rows if r["best_delta"] > 0) / n
            print("    opt q 上升占比 %.1f%%" % (frac * 100))
            out = os.path.join(ROOT, "state", "ab", "p0_3_fix_impact.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"n_points": n, "rows": rows,
                           "note": "原型影响评估，未写入任何主矩阵"},
                          f, ensure_ascii=False, indent=1)
            print("    明细已写 %s（未污染主矩阵）" % out)
        return 0

    print("请指定 --verify-single 或 --compare-real N")
    return 1


if __name__ == "__main__":
    sys.exit(main())
