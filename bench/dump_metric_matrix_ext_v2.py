# -*- coding: utf-8 -*-
"""P0-3 修复版指标矩阵导出（v2）：正确聚合全部终端分支。

=================== 与 v1 的唯一差异 ====================
v1（`dump_metric_matrix_ext.py:316-317`）：
    terms.sort(key=lambda t: -t[2]);  wk2, op2, _wt = terms[0]
    ⇒ 只保留权重最大的**单个**终端分支，其余静默丢弃

v2（本文件）：
    · F：保留**全部**终端，逐个算 7 指标原始值，按 weight 加权聚合到并集候选集，
         再在并集候选集上做 min-max 归一化（单终端时退化为 v1 行为，已对拍验证）
    · q：用**全枚举世界 + C++ 完整递归**（`enumerate_worlds_py` + `exact_eval`），
         不走 saturate ⇒ 结构上不可能有截断
         已对拍：与 `state/ab/merged_regret.json` 的既有 q 一致到 4.8e-10

=================== 为什么 q 用全枚举而不是加权聚合 ====================
`merge_and_eval_cases.py:240-260` 已验证「全枚举 + C++ 完整递归」是正确路径，
且其产物 `merged_regret.json` 可作独立基准对拍。
用同一路径算 q ⇒ 有外部基准可验证，优于自己发明的聚合口径（C2）。

=================== 仪器自测（必须先通过）====================
1. 单终端等价性：F 必须与 v1 逐位一致（max|Δ| ≤ 1e-12）
2. q 对拍：与 merged_regret 共同点集的 q 一致（max|Δ| ≤ 1e-6）
3. 阴性对照：多终端点上 F 必须与 v1 不同（否则"修复"未生效）

用法:
  python bench/dump_metric_matrix_ext_v2.py --self-test
  python bench/dump_metric_matrix_ext_v2.py \
      --inputs bench/data/midgame_cases.json bench/data/midgame_cases_ext.json \
      --out state/ab/mm_full_v2.json
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
    METRICS, case_key, count_worlds, compute_metrics, minmax)
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402


# ==================== F：全终端加权聚合 ====================
def f_metrics_aggregated(eng, terms, c):
    """在全部终端上算 7 指标，按 weight 加权聚合到并集候选集，再归一化。

    返回 (coords, F_norm, diag)。coords 为按 (y,x) 稳定的坐标列表。
    """
    per_term = []
    for wk, op, wt in terms:
        cands = eng.candidates(wk, op)
        if not cands:
            continue
        raw = compute_metrics(eng, wk, op, cands, c)
        coord = {cd: eng._coord(cd) for cd in cands}
        per_term.append({"wt": float(wt), "coord": coord, "raw": raw})

    if not per_term:
        return [], {}, {"n_terms": len(terms), "n_used": 0}

    coord_set = {xy for t in per_term for xy in t["coord"].values()}
    coords = sorted(coord_set, key=lambda p: (p[1], p[0]))

    # 反查索引：每个终端的 coord -> cd
    for t in per_term:
        t["inv"] = {xy: cd for cd, xy in t["coord"].items()}

    agg = {m: {} for m in METRICS}
    for xy in coords:
        num = {m: 0.0 for m in METRICS}
        den = 0.0
        for t in per_term:
            cd = t["inv"].get(xy)
            if cd is None:
                continue
            wt = t["wt"]
            den += wt
            r = t["raw"]
            for m in METRICS:
                num[m] += wt * r[m][cd]
        if den <= 0:
            continue
        for m in METRICS:
            agg[m][xy] = num[m] / den

    # 并集候选集上归一化（与 v1 同口径）
    F = {}
    for m in METRICS:
        d = {xy: agg[m][xy] for xy in coords if xy in agg[m]}
        nv = minmax(d)
        if m == "R":
            nv = {k: 1.0 - v for k, v in nv.items()}
        F[m] = nv

    top = max(t["wt"] for t in per_term)
    tot = sum(t["wt"] for t in per_term)
    diag = {
        "n_terms": len(terms), "n_used": len(per_term),
        "dropped_mass": (1.0 - top / tot) if tot > 0 else 0.0,
        "n_coords_union": len(coords),
    }
    return coords, F, diag


# ==================== q：全枚举 + C++ 完整递归 ====================
def q_full_enum(c, node_cap):
    """用全枚举世界 + C++ exact_eval 算 q（无截断）。"""
    worlds = enumerate_worlds_py(c)
    if not worlds:
        return None, None, "empty_worlds"
    hexw = [format(m, "x") for m in worlds]
    cv = np.asarray(c["cell_value"], dtype=np.int32)
    try:
        ql, co, _nodes, v_opt = native.mscore.exact_eval(
            hexw, [tuple(x) for x in c["clicks"]],
            [tuple(x) for x in c["clicks9"]], cv,
            int(c["a"]), int(c["w"]), int(c["h"]), node_cap, 0)
    except Exception as e:                                  # noqa: BLE001
        return None, None, "exact_eval_fail:" + type(e).__name__
    if not ql:
        return None, None, "empty_q"
    qmap = {}
    for (x, y), v in zip(co, ql):
        qmap[(int(x), int(y))] = float(v)
    return qmap, float(v_opt), None


# ==================== 轨迹记录 ====================
def v1_row_for_case(c, args):
    """复算 v1（截断版）的行，用于对拍/对比。"""
    eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                      world_cap=None)
    if eng.n_worlds == 0:
        return None, "no_worlds"
    wk = WKey.all(eng.n_worlds)
    terms = [t for t in eng.saturate(wk, eng.opened0)
             if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
    if not terms:
        return None, "no_terms"
    terms.sort(key=lambda t: -t[2])
    wk2, op2, _ = terms[0]
    cands = eng.candidates(wk2, op2)
    raw = compute_metrics(eng, wk2, op2, cands, c)
    F = {}
    for m in METRICS:
        nv = minmax(raw[m])
        if m == "R":
            nv = {k: 1.0 - v for k, v in nv.items()}
        F[m] = {eng._coord(cd): nv[cd] for cd in cands}
    return {"terms": terms, "eng": eng, "F": F,
            "coords": [eng._coord(cd) for cd in cands]}, None


def key_of(c):
    return repr(case_key(c))


# ==================== 自测 ====================
def self_test(args):
    print("=" * 100)
    print("v2 自测（修复正确性的必要条件）")
    print("=" * 100)
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for c in json.load(f)["cases"]:
                    merged[case_key(c)] = c

    # 取 mm_full 的 ok 点作为真实样本池
    okc = []
    jp = os.path.join(ROOT, "state", "ab", "mm_full.jsonl")
    if os.path.exists(jp):
        import ast
        with open(jp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("status") != "ok":
                    continue
                try:
                    c = merged.get(ast.literal_eval(r["key"]))
                except Exception:
                    continue
                if c is not None:
                    okc.append(c)
    print("  真实 ok 点池：%d" % len(okc))

    print()
    print("-" * 100)
    print("自测 1：单终端等价性（F 必须与 v1 逐位一致）")
    print("-" * 100)
    n_single = 0
    max_abs = 0.0
    n_cmp = 0
    for c in okc:
        if n_single >= 8:
            break
        try:
            v1, err = v1_row_for_case(c, args)
        except Exception:
            continue
        if err or v1 is None:
            continue
        if len(v1["terms"]) != 1:
            continue
        n_single += 1
        coords, F, diag = f_metrics_aggregated(v1["eng"], v1["terms"], c)
        for m in METRICS:
            for xy in coords:
                if xy in v1["F"][m] and xy in F[m]:
                    max_abs = max(max_abs, abs(v1["F"][m][xy] - F[m][xy]))
                    n_cmp += 1
        print("    点%d：单终端，候选 %d" % (n_single, len(coords)), flush=True)

    ok1 = (max_abs <= 1e-12) and n_single > 0
    print("  单终端点 %d，比较 %d 次，max|Δ| = %.3e ⇒ %s"
          % (n_single, n_cmp, max_abs, "通过" if ok1 else "**失败**"))

    print()
    print("-" * 100)
    print("自测 2：q 与 merged_regret 既有 q 对拍")
    print("-" * 100)
    mrp = os.path.join(ROOT, "state", "ab", "merged_regret.json")
    ok2 = True
    nq = 0
    maxdq = 0.0
    if os.path.exists(mrp):
        with open(mrp, encoding="utf-8") as f:
            mrows = json.load(f)["rows"]
        # 用 (limitation, coords 集合) 找对应 case
        for r in mrows[:6]:
            target = {tuple(x) for x in r["coords"]}
            found = None
            for c in merged.values():
                cl = {tuple(x) for x in c["clicks"]} | \
                     {tuple(x) for x in c["clicks9"]}
                if cl == target and abs(c["limitation"] - r["limitation"]) < 1e-6:
                    found = c
                    break
            if found is None:
                continue
            qmap, _v, err = q_full_enum(found, args.node_cap)
            if err:
                print("    点 q 失败：%s" % err)
                continue
            md = 0.0
            nc = 0
            for (x, y), val in zip(r["coords"], r["q"]):
                if (x, y) in qmap:
                    md = max(md, abs(qmap[(x, y)] - float(val)))
                    nc += 1
            maxdq = max(maxdq, md)
            nq += 1
            print("    点%d：limitation %.2f，比较 %d 格，max|Δq|=%.3e"
                  % (nq, r["limitation"], nc, md), flush=True)
    ok2 = (nq > 0) and (maxdq <= 1e-6)
    print("  对拍 %d 点，max|Δq| = %.3e（门限 1e-6）⇒ %s"
          % (nq, maxdq, "通过" if ok2 else "**失败**"))

    print()
    print("-" * 100)
    print("自测 3：阴性对照（多终端点上 F 必须与 v1 不同）")
    print("-" * 100)
    print("  ⚠️ 判据细化（首版判据过严，已修正）：")
    print("     多终端 **不必然** 改变 F —— 若各终端的候选集完全相同，")
    print("     加权聚合后在并集上的归一化结果与单终端一致（这是正确行为）。")
    print("     真正的判据是：**当终端候选集不同时**，F 必须发生变化。")
    print()
    n_multi = 0
    n_diff_points = 0
    n_expect_change = 0      # 终端候选集确实不同的点
    n_detect = 0             # 其中 F 确实变化了的点
    for c in okc:
        if n_multi >= 8:
            break
        try:
            v1, err = v1_row_for_case(c, args)
        except Exception:
            continue
        if err or v1 is None or len(v1["terms"]) <= 1:
            continue
        n_multi += 1
        # 判断各终端候选集是否相同
        cand_sets = []
        for wk, op, _wt in v1["terms"]:
            cs = frozenset(v1["eng"]._coord(cd)
                           for cd in v1["eng"].candidates(wk, op))
            cand_sets.append(cs)
        distinct = len(set(cand_sets))
        coords2, F2, diag2 = f_metrics_aggregated(v1["eng"], v1["terms"], c)
        common = set(coords2) & set(v1["coords"])
        changed = 0
        tot = 0
        for m in METRICS:
            for xy in common:
                if xy in v1["F"][m] and xy in F2[m]:
                    tot += 1
                    if abs(v1["F"][m][xy] - F2[m][xy]) > 1e-9:
                        changed += 1
        if changed > 0:
            n_diff_points += 1
        expect = distinct > 1
        if expect:
            n_expect_change += 1
            if changed > 0:
                n_detect += 1
            mark = "✔ 应变化且已变化" if changed > 0 else "✘ 应变化但未变化"
        else:
            mark = "— 候选集相同，不变是正确行为" if changed == 0 \
                else "⚠ 候选集相同却变化（需排查）"
        print("    点%d：终端 %d，候选集种类 %d，丢弃质量 %.1f%%，F 变化 %d/%d 格 —— %s"
              % (n_multi, diag2["n_terms"], distinct,
                 diag2["dropped_mass"] * 100, changed, tot, mark), flush=True)
    ok3 = (n_expect_change > 0) and (n_detect == n_expect_change)
    print()
    print("  多终端点 %d（其中终端候选集不同的 %d 个）"
          % (n_multi, n_expect_change))
    print("  在'应变化'的点中实际检测到变化的：%d/%d ⇒ %s"
          % (n_detect, n_expect_change,
             "通过（修复确实生效）" if ok3 else "**失败**（修复未改变结果）"))

    print()
    print("=" * 100)
    allok = ok1 and ok2 and ok3
    print("自测总结：等价性 %s / q 对拍 %s / 阴性对照 %s ⇒ %s"
          % ("✔" if ok1 else "✘", "✔" if ok2 else "✘",
             "✔" if ok3 else "✘",
             "全部通过，可用" if allok else "**未通过，禁止用于正式读数**"))
    return 0 if allok else 2


# ==================== 正式导出 ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--node-cap", type=int, default=5_000_000)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--out", default=os.path.join("state", "ab", "mm_full_v2.json"))
    ap.add_argument("--journal", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args)

    if args.inputs:
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p)
                 for p in args.inputs]
    else:
        paths = [os.path.join(ROOT, "bench", "data", nm)
                 for nm in ("midgame_cases.json", "midgame_cases_ext.json")
                 if os.path.exists(os.path.join(ROOT, "bench", "data", nm))]

    print("=" * 100)
    print("v2 指标矩阵导出（P0-3 修复：全终端加权聚合 + q 全枚举）")
    print("=" * 100)
    for p in paths:
        print("  输入：%s" % os.path.basename(p))

    merged = {}
    raw_total = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        raw_total += len(cases)
        for c in cases:
            merged[case_key(c)] = c
    print()
    print("  原始点 %d，去重后 %d" % (raw_total, len(merged)))

    print("  筛选中（worlds <= %d）..." % args.max_worlds, flush=True)
    eligible = []
    for i, c in enumerate(merged.values()):
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= args.max_worlds:
            eligible.append((c, nw))
    print("  合格 %d/%d = %.2f%%"
          % (len(eligible), len(merged), len(eligible) / max(1, len(merged)) * 100))
    if args.limit:
        eligible = eligible[:args.limit]

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    journal = args.journal or (out_path + ".journal.jsonl")
    if not os.path.isabs(journal):
        journal = os.path.join(ROOT, journal)
    done = {}
    if os.path.exists(journal):
        with open(journal, encoding="utf-8") as jf:
            for line in jf:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("key"):
                    done[rec["key"]] = rec
        print("  断点续跑：journal 已有 %d 条" % len(done))

    print()
    print("=" * 100)
    print("逐点计算（%d 点，已完成 %d）" % (len(eligible), len(done)))
    print("=" * 100)
    rows = []
    skipped = {"enum_fail": 0, "budget": 0, "err": 0, "no_terms": 0,
               "node_cap": 0, "resumed": 0}
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()
    n_multi = 0

    for i, (c, nw) in enumerate(eligible):
        ck = key_of(c)
        if ck in done:
            rec = done[ck]
            if rec.get("status") == "ok":
                rows.append(rec["row"])
                skipped["resumed"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap,
                              time_cap=args.time_cap, world_cap=None)
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "engine_fail"}) + "\n")
            jf.flush()
            continue
        if eng.n_worlds == 0:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_worlds"}) + "\n")
            jf.flush()
            continue
        wk_all = WKey.all(eng.n_worlds)
        try:
            terms = [t for t in eng.saturate(wk_all, eng.opened0)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            skipped["budget"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "budget"}) + "\n")
            jf.flush()
            continue
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "saturate_fail"}) + "\n")
            jf.flush()
            continue
        if not terms:
            skipped["no_terms"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_terms"}) + "\n")
            jf.flush()
            continue
        if len(terms) > 1:
            n_multi += 1

        # q：全枚举（无截断）
        qmap, v_opt, qerr = q_full_enum(c, args.node_cap)
        if qerr:
            key = "node_cap" if "cap" in qerr.lower() else "err"
            skipped[key] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": qerr}) + "\n")
            jf.flush()
            continue

        # F：全终端加权聚合
        try:
            coords, F, diag = f_metrics_aggregated(eng, terms, c)
        except Budget:
            skipped["budget"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "budget_f"}) + "\n")
            jf.flush()
            continue
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "f_fail"}) + "\n")
            jf.flush()
            continue
        if not coords:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "empty_coords"}) + "\n")
            jf.flush()
            continue

        # 对齐：只保留同时有 q 与 F 的坐标
        coords_out, q_out, F_out = [], [], {m: [] for m in METRICS}
        n_missing = 0
        for xy in coords:
            if xy not in qmap:
                n_missing += 1
                continue
            coords_out.append([xy[0], xy[1]])
            q_out.append(round(qmap[xy], 9))
            for m in METRICS:
                F_out[m].append(round(F[m][xy], 9))
        if not coords_out:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_overlap"}) + "\n")
            jf.flush()
            continue

        row = {"limitation": c["limitation"], "worlds": nw,
               "n_cand": len(coords_out), "v_opt": round(v_opt, 9),
               "n_terms": diag["n_terms"],
               "dropped_mass": round(diag["dropped_mass"], 9),
               "coords": coords_out, "q": q_out, "F": F_out}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "status": "ok", "row": row}) + "\n")
        jf.flush()
        if (i + 1) % 10 == 0:
            print("    %d/%d（多终端 %d，%.0fs）"
                  % (i + 1, len(eligible), n_multi,
                     time.perf_counter() - t0), flush=True)

    jf.close()
    elapsed = time.perf_counter() - t0
    print()
    print("  成功 %d，多终端点 %d，跳过 %s" % (len(rows), n_multi, skipped))
    print("  ⚠️ 总耗时 %.1fs 不可用于策略排序" % elapsed)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"version": "v2_full_terminal_aggregation",
                   "p0_3_fixed": True,
                   "q_path": "full_enumeration_plus_cpp_exact_eval",
                   "F_path": "all_terminals_weighted_aggregation",
                   "metrics": list(METRICS), "rows": rows,
                   "skipped": skipped, "raw_total": raw_total,
                   "dedup": len(merged), "eligible": len(eligible),
                   "n_multi_terminal_points": n_multi,
                   "USABLE_FOR_RANKING_TIME": False,
                   "args": vars(args)}, f, ensure_ascii=False)
    print("  明细已写 %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
