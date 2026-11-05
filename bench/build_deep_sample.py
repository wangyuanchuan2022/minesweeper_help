# -*- coding: utf-8 -*-
"""补深局面样本：从 worlds > 2500 的 120 点中按层等量抽取，构建深局面矩阵。

=================== 为什么必须补这批点（Advisor 2026-11-04）====================
本会话已实证：现有 342 点 samples（worlds ≤ 2516）**太浅**，导致
  · 3-ply 与真值 q 的相关系数中位 **0.9998**、top-1 一致率 75.3%
  · ⇒ 任何强收益估计器都会退化成 q，评估"收益准则"必然得到泄漏读数
  · ⇒ 结论既不能证明有提升，也不能证明无提升

因此凡是"评估收益准则/估计器"的实验，都必须先在**深局面**上做。
本脚本按 worlds 分层等量抽取深局面点，构建 `mm_deep.json`。

=================== 抽样方案（预注册）====================
分层（按 worlds）：
    [ 2500,  4000)
    [ 4000,  7000)
    [ 7000, 12000)
    [12000, 20000]
每层取 N 点（默认 20），尽量取该层内 worlds 较小的（成本低），
但**不是**取全局最小——若某层不足 N 点则全取并如实记录。

⚠️ 这是**便利样本**（按 worlds 排序取前 N），不是随机抽样。
   报告时必须说明：样本在每层内偏向 worlds 较小者。

=================== 技术要点 ====================
· q 用 C++ exact_eval 全枚举（无 saturate 改写，与 v3 同口径）
· F 在**初始候选集**上算（与 v3 同口径）
· 深局面可能很慢：设 node_cap=2e8、time_cap=300s，超限如实标 node_cap
· 逐点 journal，可断点续跑

用法:
  python bench/build_deep_sample.py --per-stratum 20
"""
import argparse
import json
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
from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402
from dump_metric_matrix_ext import (                       # noqa: E402
    METRICS, case_key, count_worlds, minmax, bdist)
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402
import utils.native as native                             # noqa: E402

STRATA = [(2500, 4000), (4000, 7000), (7000, 12000), (12000, 20001)]


def q_cpp(case, node_cap):
    worlds = enumerate_worlds_py(case)
    if not worlds:
        return None, None, 0, "empty_worlds"
    hexw = [format(m, "x") for m in worlds]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    try:
        ql, co, nodes, v_opt = native.mscore.exact_eval(
            hexw, [tuple(x) for x in case["clicks"]],
            [tuple(x) for x in case["clicks9"]], cv,
            int(case["a"]), int(case["w"]), int(case["h"]), node_cap, 0)
    except Exception as e:                                 # noqa: BLE001
        msg = str(e)
        kind = "node_cap" if ("node cap" in msg.lower()) else "err"
        return None, None, 0, kind + ":" + msg[:60]
    if not ql:
        return None, None, nodes, "empty_q"
    qmap = {}
    for (x, y), v in zip(co, ql):
        qmap[(int(x), int(y))] = float(v)
    return qmap, float(v_opt), nodes, None


def metrics_initial_state(eng, wk_all, c):
    """F 指标（初始状态候选集），与 v3 完全同口径。"""
    w, h = int(c["w"]), int(c["h"])
    cands = eng.candidates(wk_all, eng.opened0)
    raw = {m: {} for m in METRICS}
    for cd in cands:
        raw["S0"][cd] = eng.safety(cd, wk_all)
        raw["L"][cd] = float(bdist(w, h, eng._coord(cd)))
    for cd in cands:
        try:
            p_safe, outs = eng.click_outcomes(cd, wk_all, eng.opened0)
        except Budget:
            p_safe, outs = 0.0, []
        ig = (0.0 if p_safe <= 0 else
              p_safe * sum(wt * eng.pop(op3 & ~eng.opened0)
                           for _s, op3, wt in outs))
        raw["IG"][cd] = ig
        if p_safe <= 0 or not outs:
            raw["U"][cd] = 0.0
            raw["E"][cd] = 0.0
            raw["B"][cd] = 0.0
            raw["R"][cd] = 0.0
            continue
        worst = 1.0
        for sub, op3, wt in outs:
            c3 = eng.candidates(sub, op3)
            if c3:
                worst = min(worst, min(eng.safety(x, sub) for x in c3))
        raw["U"][cd] = p_safe * worst
        accE = accB = accR = 0.0
        for sub, op3, wt in outs:
            _u, inter = eng._union_inter(sub)
            accE += wt * eng.pop(eng.all_mask & ~op3 & ~inter)
            c3 = eng.candidates(sub, op3)
            if c3:
                sv = [eng.safety(x, sub) for x in c3]
                spread = max(sv) - min(sv)
                accB += wt * len(outs) / (1.0 + spread * 10.0)
                accR += wt * (1.0 - max(sv))
        raw["E"][cd] = p_safe * accE
        raw["B"][cd] = p_safe * accB
        raw["R"][cd] = accR
    return cands, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-stratum", type=int, default=20)
    ap.add_argument("--node-cap", type=int, default=200_000_000)
    ap.add_argument("--time-cap", type=float, default=300.0)
    ap.add_argument("--out", default=os.path.join("state", "ab", "mm_deep.json"))
    args = ap.parse_args()

    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c
    elig = []
    for c in merged.values():
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= 20000:
            elig.append((nw, c))
    elig.sort(key=lambda t: t[0])

    print("=" * 100)
    print("补深局面样本（供收益准则/估计器评估）")
    print("=" * 100)
    print("  合格点总数 %d，分层方案每层 %d 点" % (len(elig), args.per_stratum))

    selected = []
    print()
    print("  分层抽样（每层取 worlds 较小者，成本导向）：")
    for lo, hi in STRATA:
        b = [(nw, c) for nw, c in elig if lo <= nw < hi]
        take = b[:args.per_stratum]
        selected.extend(take)
        print("    [%5d,%5d): 可选 %3d → 取 %2d（worlds 范围 %s）"
              % (lo, hi, len(b), len(take),
                 ("%d~%d" % (take[0][0], take[-1][0])) if take else "—"))
    print()
    print("  合计选中 %d 点" % len(selected))
    print("  ⚠️ 便利样本：每层内偏向 worlds 较小者，报告须声明")

    out_p = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    journal = out_p + ".journal.jsonl"
    done = {}
    if os.path.exists(journal):
        with open(journal, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("key"):
                    done[r["key"]] = r
        print("  断点续跑：已有 %d 条" % len(done))

    print()
    print("=" * 100)
    print("逐点计算（%d 点，已完成 %d）" % (len(selected), len(done)))
    print("=" * 100)
    rows = []
    skipped = {"node_cap": 0, "err": 0, "empty": 0, "resumed": 0}
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()
    for i, (nw, c) in enumerate(selected):
        ck = repr(case_key(c))
        if ck in done:
            rec = done[ck]
            if rec.get("status") == "ok":
                rows.append(rec["row"])
                skipped["resumed"] += 1
            continue
        ts = time.perf_counter()
        qmap, v_opt, nodes, qerr = q_cpp(c, args.node_cap)
        if qerr:
            key = "node_cap" if qerr.startswith("node_cap") else "err"
            skipped[key] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": qerr}) + "\n")
            jf.flush()
            print("    %2d/%d worlds=%5d → SKIP(%s) %.1fs"
                  % (i + 1, len(selected), nw, key, time.perf_counter() - ts),
                  flush=True)
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap,
                              time_cap=args.time_cap, world_cap=None)
            wk = WKey.all(eng.n_worlds)
            cands, raw = metrics_initial_state(eng, wk, c)
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "engine_fail"}) + "\n")
            jf.flush()
            continue
        if not cands:
            skipped["empty"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_cands"}) + "\n")
            jf.flush()
            continue
        norm = {}
        for m in METRICS:
            nv = minmax(raw[m])
            if m == "R":
                nv = {k: 1.0 - v for k, v in nv.items()}
            norm[m] = nv
        coords_out, q_out, F_out = [], [], {m: [] for m in METRICS}
        for cd in cands:
            xy = eng._coord(cd)
            if xy not in qmap:
                continue
            coords_out.append([xy[0], xy[1]])
            q_out.append(round(qmap[xy], 9))
            for m in METRICS:
                F_out[m].append(round(norm[m][cd], 9))
        if not coords_out:
            skipped["empty"] += 1
            continue
        row = {"limitation": c["limitation"], "worlds": nw,
               "n_cand": len(coords_out), "v_opt": round(v_opt, 9),
               "coords": coords_out, "q": q_out, "F": F_out}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "status": "ok", "row": row}) + "\n")
        jf.flush()
        print("    %2d/%d worlds=%5d n_cand=%2d ok %.1fs"
              % (i + 1, len(selected), nw, len(coords_out),
                 time.perf_counter() - ts), flush=True)
    jf.close()

    print()
    print("  成功 %d，跳过 %s" % (len(rows), skipped))
    print("  ⚠️ 总耗时 %.0fs 不可用于策略排序" % (time.perf_counter() - t0))

    with open(out_p, "w", encoding="utf-8") as f:
        json.dump({"version": "deep_stratified_sample",
                   "purpose": "供收益准则/估计器评估（避免浅样本估计器退化）",
                   "caliber": "初始状态 + C++ 全枚举；与 v3 同口径",
                   "strata": [list(s) for s in STRATA],
                   "per_stratum": args.per_stratum,
                   "sampling_note": "便利样本：每层内取 worlds 较小者",
                   "metrics": list(METRICS), "rows": rows,
                   "skipped": skipped,
                   "args": vars(args)}, f, ensure_ascii=False)
    print("  明细已写 %s" % out_p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
