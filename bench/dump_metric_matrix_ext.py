# -*- coding: utf-8 -*-
"""扩展版指标矩阵导出：多输入 + C++ 引擎 + 断点续跑。

=================== 为什么需要（P1 前置）====================
`layered_weights.py` 需要 7 个指标的打分矩阵 `F`（每决策点、每候选格），
而 `merge_and_eval_cases.py` 只输出 `q`（真值）与 `coords`，没有 `F`。
`dump_metric_matrix.py` 有 `F` 但：
  · 只读单个 `midgame_cases.json`（400 点，其中 41 个可评估）
  · 用 Python `ExactEngine`（单点 0.1~2.5s，462 点要 10+ 分钟且受同进程漂移影响）

本脚本把两者合并：
  · 多输入文件（发现集 / 验证集可分开跑）
  · C++ `exact_eval` 算 after_click 真值（已逐位对拍 140/140）
  · 指标打分仍用 Python（7 个指标需要遍历分支，C++ 侧未实现全部）
  · 逐点 journal 断点续跑（复用 merge 的机制）

=================== 指标定义（与 dump_metric_matrix.py 保持一致）====================
  S0 = safety(c)                                  越大越好
  IG = safety(c) × E[点开后新开格数]              越大越好
  U  = safety(c) × min_{c'} safety(c'|c 安全)      越大越好
  E  = safety(c) × E[点开后必然安全格数]           越大越好
  B  = safety(c) × Σ_branch w·|branch| / (1+10·spread)   越大越好
  R  = Σ_branch w·(1 − max safety)                越小越好 ⇒ 归一化后取负
  L  = 到边界距离                                  越大越好
每决策点内 min-max 归一化到 [0,1]。

用法:
  python bench/dump_metric_matrix_ext.py \
      --inputs bench/data/midgame_cases.json bench/data/midgame_cases_ext.json \
      --out state/ab/metric_matrix_discovery.json
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

METRICS = ("S0", "IG", "U", "E", "B", "R", "L")


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = sum(sum(int(v) for v in case["groups"][gi][sel])
                 for gi, sel in enumerate(idx))
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def case_key(c):
    return (int(c["a"]), int(c["num10"]), int(c["total"]),
            len(c["clicks"]), len(c["clicks9"]),
            tuple(sorted(tuple(x) for x in c["clicks"])),
            tuple(sorted(tuple(x) for x in c["clicks9"])))


def enumerate_worlds_py(case):
    """枚举全部一致世界的雷位（Python 大整数位集）。"""
    import itertools
    w, h = int(case["w"]), int(case["h"])
    a, num10 = int(case["a"]), int(case["num10"])
    W2 = w + 2
    clicks = [tuple(x) for x in case["clicks"]]
    clicks9 = [tuple(x) for x in case["clicks9"]]
    groups, ck = case["groups"], case["ck"]
    cv = case["cell_value"]

    base = 0
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            if 10 == int(cv[y][x]):
                base |= 1 << (y * W2 + x)

    worlds = []
    for idx in itertools.product(*[range(n) for n in ck]):
        m = base
        mine_total = 0
        r = []
        for i, sel in enumerate(idx):
            row = groups[i][sel]
            for v in row:
                r.append(int(v))
                mine_total += int(v)
        for i, v in enumerate(r):
            if v == 1:
                x, y = clicks[i]
                m |= 1 << (int(y) * W2 + int(x))
        rem = a - num10 - mine_total
        if rem == 0:
            worlds.append(m)
            continue
        if rem < 0 or rem > len(clicks9):
            continue
        for comb in itertools.combinations(range(len(clicks9)), rem):
            mm = m
            for j in comb:
                x, y = clicks9[j]
                mm |= 1 << (int(y) * W2 + int(x))
            worlds.append(mm)
    return worlds


def minmax(d):
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.5 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def compute_metrics(eng, wk2, op2, cands, c):
    """用 Python ExactEngine 算 7 指标原始打分（未归一化）。"""
    w, h = int(c["w"]), int(c["h"])
    raw = {m: {} for m in METRICS}
    for cd in cands:
        raw["S0"][cd] = eng.safety(cd, wk2)
        raw["L"][cd] = float(bdist(w, h, eng._coord(cd)))

    for cd in cands:
        try:
            p_safe, outs = eng.click_outcomes(cd, wk2, op2)
        except Budget:
            p_safe, outs = 0.0, []

        ig = (0.0 if p_safe <= 0 else
              p_safe * sum(wt * eng.pop(op3 & ~op2)
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
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--node-cap", type=int, default=5_000_000)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "metric_matrix_ext.json"))
    ap.add_argument("--journal", default=None)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    if args.inputs:
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p)
                 for p in args.inputs]
    else:
        paths = [os.path.join(ROOT, "bench", "data", "midgame_cases.json")]

    print("=" * 100)
    print("扩展版指标矩阵导出（多输入 + C++ 真值 + 断点续跑）")
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

    # ---- 筛可枚举 ----
    print()
    print("  筛选中（worlds <= %d）..." % args.max_worlds, flush=True)
    eligible = []
    for i, c in enumerate(merged.values()):
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= args.max_worlds:
            eligible.append((c, nw))
        if (i + 1) % 300 == 0:
            print("    已筛 %d/%d，合格 %d"
                  % (i + 1, len(merged), len(eligible)), flush=True)
    print("  合格 %d/%d = %.2f%%"
          % (len(eligible), len(merged),
             len(eligible) / max(1, len(merged)) * 100))
    if args.limit:
        eligible = eligible[:args.limit]

    # ---- journal ----
    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    journal = args.journal or (out_path + ".journal.jsonl")
    if not os.path.isabs(journal):
        journal = os.path.join(ROOT, journal)
    done = {}
    if (not args.no_resume) and os.path.exists(journal):
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
        print("  断点续跑：journal 已有 %d 条（%s）"
              % (len(done), os.path.basename(journal)))

    print()
    print("=" * 100)
    print("逐点计算（%d 点，已完成 %d）" % (len(eligible), len(done)))
    print("=" * 100)

    rows = []
    skipped = {"enum_fail": 0, "budget": 0, "err": 0, "resumed": 0}
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()

    for i, (c, nw) in enumerate(eligible):
        ck = repr(case_key(c))
        if ck in done:
            rec = done[ck]
            if rec.get("status") == "ok":
                rows.append(rec["row"])
                skipped["resumed"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                              world_cap=None)
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue
        if eng.n_worlds == 0:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        try:
            terms = [t for t in eng.saturate(wk_all, op)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            skipped["budget"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "budget"}) + "\n")
            jf.flush()
            continue
        if not terms:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)

        # 真值 q：优先用 C++ exact_eval
        try:
            worlds = [format(eng.world_bits[k], "x") for k in wk2]
            cv = np.asarray(c["cell_value"], dtype=np.int32)
            ql, co, _nodes, v_opt = native.mscore.exact_eval(
                worlds, [tuple(x) for x in c["clicks"]],
                [tuple(x) for x in c["clicks9"]], cv,
                int(c["a"]), int(c["w"]), int(c["h"]), args.node_cap, 0)
        except Exception:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue
        if not ql:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue

        # 指标打分（Python，需要分支遍历）
        try:
            raw = compute_metrics(eng, wk2, op2, cands, c)
        except Budget:
            skipped["budget"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "budget"}) + "\n")
            jf.flush()
            continue

        norm = {}
        for m in METRICS:
            nv = minmax(raw[m])
            if m == "R":
                nv = {k: 1.0 - v for k, v in nv.items()}
            norm[m] = nv

        # C++ 返回的 coords 与 eng.candidates 顺序可能不同 ⇒ 按坐标对齐
        coord_to_q = {}
        for (x, y), v in zip(co, ql):
            coord_to_q[(int(x), int(y))] = float(v)
        coords_out, q_out, F_out = [], [], {m: [] for m in METRICS}
        for cd in cands:
            xy = eng._coord(cd)
            if xy not in coord_to_q:
                continue
            coords_out.append([xy[0], xy[1]])
            q_out.append(round(coord_to_q[xy], 9))
            for m in METRICS:
                F_out[m].append(round(norm[m][cd], 9))
        if not coords_out:
            skipped["err"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "err"}) + "\n")
            jf.flush()
            continue

        row = {"limitation": c["limitation"], "worlds": nw,
               "n_cand": len(coords_out), "v_opt": round(v_opt, 9),
               "coords": coords_out, "q": q_out, "F": F_out}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "status": "ok", "row": row}) + "\n")
        jf.flush()
        if (i + 1) % 10 == 0:
            print("    %d/%d（%.0fs）"
                  % (i + 1, len(eligible), time.perf_counter() - t0), flush=True)

    jf.close()
    elapsed = time.perf_counter() - t0
    print()
    print("  成功 %d，跳过 %s" % (len(rows), skipped))
    print("  ⚠️ 总耗时 %.1fs 不可用于策略排序（同进程漂移）" % elapsed)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"metrics": list(METRICS), "rows": rows,
                   "skipped": skipped, "raw_total": raw_total,
                   "dedup": len(merged), "eligible": len(eligible),
                   "USABLE_FOR_RANKING_TIME": False,
                   "args": vars(args)}, f, ensure_ascii=False)
    print()
    print("  明细已写 %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
