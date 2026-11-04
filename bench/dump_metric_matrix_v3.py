# -*- coding: utf-8 -*-
"""v3 指标矩阵：统一到「初始状态 + C++ 全枚举」口径。

=================== v3 与 v1/v2 的关系（必须读）====================
· **v1**（`dump_metric_matrix_ext.py`）：先 `saturate()` 再 `terms.sort()` 取 `terms[0]`
  ⇒ ① 丢弃其余终端；② q 与 F 都建在**被 saturate 改写过的状态**上
· **v2**（`dump_metric_matrix_ext_v2.py`）：修掉了"只取 terms[0]"，改成多终端加权聚合
  ⇒ 但仍建在 **saturate 状态**上
· **v3**（本文件）：**不使用 saturate 做状态改写**，直接：
      q  = C++ `exact_eval` 从**初始状态**、**全部枚举世界**递归
      F  = 在同一**初始候选集**上计算 7 个指标（每个候选格算其指标原始值）

=================== 为什么 v2 仍不够（本轮实证）====================
`saturate()` 会把**必然安全格自动开掉**，把一个状态拆成多个终端；
不同终端的世界集与候选集**都不同**。

实证（worlds=8 的点，`review/P0-3根因再定位_saturate破坏q定义.md`）：
  · `(8,15)`：在**所有**终端中都是候选 ⇒ 多终端加权 = 0.375 = C++ 值 ✔
  · `(14,4)`：只在终端0（wt=0.5）中是候选，其余终端已开出该格
             ⇒ 多终端加权给 0.25，而 C++ 从原状态给 **0.375** ✘

⇒ 只要某格在部分终端中已被开出，加权聚合就**无法还原**从原状态出发的值。
   v2 的 F 与 q 因此仍与 C++ 口径不一致。

=================== v3 的定义 ====================
对初始状态 (wk_all, opened0)：
  · 候选集 cands0 = candidates(wk_all, opened0)   ← 与 C++ exact_eval 完全一致
  · q(c) = C++ exact_eval 返回的值（从初始状态递归，全世界）
  · F 指标：对每个 c ∈ cands0 计算
      S0 = safety(c | wk_all)                        ← 初始状态上的安全度
      IG = safety × E[新开格数]                      ← click_outcomes 从初始状态
      U  = safety × min_{c'} safety(c'|分支)          ← 同上
      E  = safety × E[必然安全格数]
      B  = safety × Σ_branch w·|branch|/(1+10·spread)
      R  = Σ_branch w·(1 − max safety)
      L  = 到边界距离
  然后在 **cands0 内** 做 min-max 归一化（与 v1/v2 同口径，只是集合不同）
  ⇒ F 与 q **同一状态、同一候选集**，可直接用于 regret 计算

=================== 仪器验证（必须先通过）====================
1. q 与 C++ 直算一致（本文件直接调 C++，故比较对象是 Python 初始状态实现）
2. F 的 S0 指标必须与 `safety()` 从初始状态直算一致
3. 与 merged_regret 的 q 对拍（用户已确认的独立基准）

用法:
  python bench/dump_metric_matrix_v3.py --self-test
  python bench/dump_metric_matrix_v3.py --out state/ab/mm_v3.json
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
    METRICS, case_key, count_worlds, minmax, bdist)
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402


def metrics_initial_state(eng, wk_all, c):
    """在**初始状态**的候选集上计算 7 指标原始值（不经过 saturate）。"""
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


def q_cpp(c, node_cap):
    """C++ exact_eval：从初始状态、全部枚举世界递归。"""
    worlds = enumerate_worlds_py(c)
    if not worlds:
        return None, None, 0, "empty_worlds"
    hexw = [format(m, "x") for m in worlds]
    cv = np.asarray(c["cell_value"], dtype=np.int32)
    try:
        ql, co, nodes, v_opt = native.mscore.exact_eval(
            hexw, [tuple(x) for x in c["clicks"]],
            [tuple(x) for x in c["clicks9"]], cv,
            int(c["a"]), int(c["w"]), int(c["h"]), node_cap, 0)
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


# ==================== 自测 ====================
def self_test(args):
    print("=" * 100)
    print("v3 自测（初始状态口径的正确性）")
    print("=" * 100)
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c
    import ast
    okc = []
    jp = os.path.join(ROOT, "state", "ab", "p1_coverage.json.journal.jsonl")
    with open(jp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rw = r.get("row")
            if rw and rw.get("status") == "ok":
                try:
                    c = merged.get(ast.literal_eval(r["key"]))
                except Exception:
                    continue
                if c is not None:
                    okc.append((rw["worlds"], c))
    okc.sort(key=lambda t: t[0])
    print("  样本池：%d 个 Python 可算点" % len(okc))

    # 自测 1：候选集必须与 C++ 一致
    print()
    print("-" * 100)
    print("自测 1：候选集一致性（eng.candidates(初始状态) vs C++ 返回的 coords）")
    print("-" * 100)
    n1 = 0
    bad1 = 0
    for nw, c in okc[:10]:
        eng = ExactEngine(c, node_cap=args.node_cap, time_cap=600.0,
                          world_cap=None)
        wk = WKey.all(eng.n_worlds)
        pyc = {eng._coord(cd) for cd in eng.candidates(wk, eng.opened0)}
        qmap, _v, _nodes, err = q_cpp(c, args.node_cap)
        if err:
            continue
        cpc = set(qmap)
        n1 += 1
        if pyc != cpc:
            bad1 += 1
            print("    ✘ worlds=%d Python %d 格 vs C++ %d 格，差集 %s"
                  % (nw, len(pyc), len(cpc),
                     sorted(pyc ^ cpc)[:6]))
        else:
            print("    ✔ worlds=%d 候选 %d 格完全一致" % (nw, len(pyc)))
    ok1 = (n1 > 0) and (bad1 == 0)
    print("  ⇒ %d 点中不一致 %d 个：%s"
          % (n1, bad1, "通过" if ok1 else "**失败**"))

    # 自测 2：S0 指标 vs 独立 safety 直算
    print()
    print("-" * 100)
    print("自测 2：S0 指标与 safety() 直算一致")
    print("-" * 100)
    n2 = 0
    maxd2 = 0.0
    for nw, c in okc[:6]:
        eng = ExactEngine(c, node_cap=args.node_cap, time_cap=600.0,
                          world_cap=None)
        wk = WKey.all(eng.n_worlds)
        cands, raw = metrics_initial_state(eng, wk, c)
        for cd in cands:
            direct = eng.safety(cd, wk)
            maxd2 = max(maxd2, abs(raw["S0"][cd] - direct))
            n2 += 1
        print("    worlds=%d 候选 %d 格已比对" % (nw, len(cands)))
    ok2 = (n2 > 0) and (maxd2 <= 1e-12)
    print("  ⇒ %d 格，S0 最大差 = %.3e：%s"
          % (n2, maxd2, "通过" if ok2 else "**失败**"))

    # 自测 3：q 与 merged_regret 对拍（独立基准）
    print()
    print("-" * 100)
    print("自测 3：q 与 merged_regret 已有 q 对拍（独立基准）")
    print("-" * 100)
    mrp = os.path.join(ROOT, "state", "ab", "merged_regret.json")
    n3 = 0
    maxd3 = 0.0
    if os.path.exists(mrp):
        with open(mrp, encoding="utf-8") as f:
            mrows = json.load(f)["rows"]
        # 用 limitation + coords 集合匹配
        bycoord = {}
        for c in merged.values():
            cl = frozenset(tuple(x) for x in
                           (c["clicks"] + c["clicks9"]))
            bycoord.setdefault(cl, []).append(c)
        for r in mrows[:8]:
            cl = frozenset(tuple(x) for x in r["coords"])
            cand_list = bycoord.get(cl, [])
            found = None
            for c in cand_list:
                if abs(c["limitation"] - r["limitation"]) < 1e-6:
                    found = c
                    break
            if found is None:
                continue
            qmap, _v, _nodes, err = q_cpp(found, args.node_cap)
            if err:
                continue
            md = 0.0
            nc = 0
            for x, v in zip(r["coords"], r["q"]):
                xy = tuple(x)
                if xy in qmap:
                    md = max(md, abs(qmap[xy] - float(v)))
                    nc += 1
            maxd3 = max(maxd3, md)
            n3 += 1
            print("    limitation %.2f，比较 %d 格，max|Δq|=%.3e"
                  % (r["limitation"], nc, md))
    ok3 = (n3 > 0) and (maxd3 <= 1e-6)
    print("  ⇒ %d 点，max|Δq| = %.3e（门限 1e-6）：%s"
          % (n3, maxd3, "通过" if ok3 else "**失败**"))

    print()
    print("=" * 100)
    allok = ok1 and ok2 and ok3
    print("自测总结：候选集 %s / S0 %s / q对拍 %s ⇒ %s"
          % ("✔" if ok1 else "✘", "✔" if ok2 else "✘", "✔" if ok3 else "✘",
             "全部通过，可用于正式矩阵" if allok
             else "**未通过，禁止生成正式矩阵**"))
    return 0 if allok else 2


# ==================== 正式导出 ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--node-cap", type=int, default=200_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args)

    if args.inputs:
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p)
                 for p in args.inputs]
    else:
        paths = [os.path.join(ROOT, "bench", "data", nm)
                 for nm in ("midgame_cases.json", "midgame_cases_ext.json")]

    print("=" * 100)
    print("v3 指标矩阵（初始状态 + C++ 全枚举，统一口径）")
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
    print("  原始点 %d，去重后 %d" % (raw_total, len(merged)))

    print("  筛选中（worlds <= %d）..." % args.max_worlds, flush=True)
    elig = []
    for c in merged.values():
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= args.max_worlds:
            elig.append((c, nw))
    elig.sort(key=lambda t: t[1])       # 按 worlds 升序，早出结果
    print("  合格 %d" % len(elig))
    if args.limit:
        elig = elig[:args.limit]

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
    rows = []
    skipped = {"engine_fail": 0, "no_worlds": 0, "node_cap": 0,
               "empty_q": 0, "no_cands": 0, "resumed": 0}
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()

    for i, (c, nw) in enumerate(elig):
        ck = repr(case_key(c))
        if ck in done:
            rec = done[ck]
            if rec.get("status") == "ok":
                rows.append(rec["row"])
                skipped["resumed"] += 1
            continue

        # q：C++ 全枚举（初始状态）
        qmap, v_opt, nodes, qerr = q_cpp(c, args.node_cap)
        if qerr:
            key = ("node_cap" if qerr.startswith("node_cap")
                   else "empty_q" if qerr.startswith("empty_q")
                   else "no_worlds" if qerr.startswith("empty_worlds")
                   else "engine_fail")
            skipped[key] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": key}) + "\n")
            jf.flush()
            continue

        # F：初始状态候选集上的指标
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=1800.0,
                              world_cap=None)
            wk = WKey.all(eng.n_worlds)
            cands, raw = metrics_initial_state(eng, wk, c)
        except Budget:
            skipped["node_cap"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "budget_f"}) + "\n")
            jf.flush()
            continue
        except Exception:
            skipped["engine_fail"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "engine_fail"}) + "\n")
            jf.flush()
            continue
        if not cands:
            skipped["no_cands"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_cands"}) + "\n")
            jf.flush()
            continue

        # 归一化（cands 内）
        coords_out, q_out, F_out = [], [], {m: [] for m in METRICS}
        norm = {}
        for m in METRICS:
            nv = minmax(raw[m])
            if m == "R":
                nv = {k: 1.0 - v for k, v in nv.items()}
            norm[m] = nv
        for cd in cands:
            xy = eng._coord(cd)
            if xy not in qmap:
                continue
            coords_out.append([xy[0], xy[1]])
            q_out.append(round(qmap[xy], 9))
            for m in METRICS:
                F_out[m].append(round(norm[m][cd], 9))
        if not coords_out:
            skipped["no_cands"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "no_overlap"}) + "\n")
            jf.flush()
            continue

        row = {"limitation": c["limitation"], "worlds": nw,
               "n_cand": len(coords_out), "v_opt": round(v_opt, 9),
               "coords": coords_out, "q": q_out, "F": F_out}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "status": "ok", "row": row}) + "\n")
        jf.flush()
        if (i + 1) % 25 == 0:
            print("    %d/%d（%.0fs）" % (i + 1, len(elig),
                                          time.perf_counter() - t0), flush=True)

    jf.close()
    print()
    print("  成功 %d，跳过 %s" % (len(rows), skipped))
    print("  ⚠️ 总耗时 %.0fs 不可用于策略排序" % (time.perf_counter() - t0))

    with open(out_p, "w", encoding="utf-8") as f:
        json.dump({"version": "v3_initial_state_cpp_full_enum",
                   "caliber": "initial_state (no saturate rewrite)",
                   "p0_3_fixed": True,
                   "q_path": "cpp_exact_eval_full_enumeration",
                   "F_path": "metrics_on_initial_state_candidates",
                   "metrics": list(METRICS), "rows": rows,
                   "skipped": skipped, "raw_total": raw_total,
                   "dedup": len(merged), "eligible": len(elig),
                   "USABLE_FOR_RANKING_TIME": False,
                   "args": vars(args)}, f, ensure_ascii=False)
    print("  明细已写 %s" % out_p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
