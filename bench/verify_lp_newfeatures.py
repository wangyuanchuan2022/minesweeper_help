# -*- coding: utf-8 -*-
"""数学检验：增加/替换指标能否打破 LP 证明的"线性不可分离"？

=================== 动机 ====================
`bench/verify_lp_separability.py` 已用 LP 严格证明：
**现有 7 个指标（S0/IG/U/E/B/R/L）的线性组合无法完全分离 q 排序**
（LP1 infeasible，阳性对照通过）。

但这只是"这 7 个不够"，不是"任何指标集都不够"。
本脚本把问题推进一层，用**同一套 LP 判定**回答：

    加入/替换候选指标后，不可分离性是否被打破？

=================== 候选新指标（全部来自引擎已有能力，不新造算法）====================
  P2 = ply_score(c, k=2)   2-ply 前瞻值（safety × E[best 1-ply]）
  P3 = ply_score(c, k=3)   3-ply 前瞻值
  S0P2 = S0 × P2           交互项（安全度 × 前瞻质量）
  ND  = 该候选开出的新格数期望（E 的未归一化版本，已在 compute_metrics 里）
  UxS = U × S0             交互项

对每种"指标集"，跑 LP1 判定：
  可行  ⇒ 该指标集**线性可分离**（存在权重使样本内 regret ≡ 0）
  不可行 ⇒ 仍不可分离

⚠️ 关键口径纪律：
  · 新指标必须在**初始状态**上计算（与 v3 口径一致），不能用 saturate 改写状态
  · 所有指标在同一候选集内 min-max 归一化到 [0,1]
  · 与 v3 的 342 点用**同一批点**（否则不可比）

用法:
  python bench/verify_lp_newfeatures.py --limit 120
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

import numpy as np                                        # noqa: E402
from scipy.optimize import linprog                        # noqa: E402
from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402
from dump_metric_matrix_ext import case_key, minmax         # noqa: E402
from merge_and_eval_cases import enumerate_worlds_py        # noqa: E402
from verify_lp_separability import load_rows                # noqa: E402

BASE7 = ("S0", "IG", "U", "E", "B", "R", "L")


def compute_new_features(eng, wk, c):
    """在**初始状态**的候选集上计算候选新指标。

    返回 (coords, feat)：
      coords —— 坐标列表
      feat   —— {name: {coord: raw_value}}（原始值，未归一化）
    """
    cands = eng.candidates(wk, eng.opened0)
    coords = [eng._coord(cd) for cd in cands]
    feat = {"P2": {}, "P3": {}, "NEW": {}}

    memo2, memo3 = {}, {}
    for cd, xy in zip(cands, coords):
        try:
            p2 = eng.ply_score(cd, wk, eng.opened0, 2, memo2)
        except Budget:
            p2 = 0.0
        try:
            p3 = eng.ply_score(cd, wk, eng.opened0, 3, memo3)
        except Budget:
            p3 = p2
        feat["P2"][xy] = float(p2)
        feat["P3"][xy] = float(p3)

        # NEW：点开后新开格数的期望（未归一化）
        try:
            p_safe, outs = eng.click_outcomes(cd, wk, eng.opened0)
            if p_safe <= 0 or not outs:
                new_cnt = 0.0
            else:
                new_cnt = p_safe * sum(
                    wt * eng.pop(op3 & ~eng.opened0) for _s, op3, wt in outs)
        except Budget:
            new_cnt = 0.0
        feat["NEW"][xy] = float(new_cnt)
    return coords, feat


def build_feature_matrix(coords, base_row, feat, names):
    """把某点各"指标"堆成 (n_feat, n_cand) 矩阵，全部在 coords 内归一化。"""
    Fs = []
    q2i = {tuple(x): i for i, x in enumerate(coords)}
    for nm in names:
        if nm in BASE7:
            # 来自 v3 行的 F（已归一化），按坐标对齐
            src = base_row["F"][nm]
            src_coords = [tuple(x) for x in base_row["coords"]]
            m = {sc: v for sc, v in zip(src_coords, src)}
            vec = np.array([m.get(xy, 0.0) for xy in coords], dtype=np.float64)
        elif nm == "S0P2":
            s0 = _base_vec(base_row, "S0", coords)
            p2 = np.array([feat["P2"][xy] for xy in coords], dtype=np.float64)
            p2n = _minmax_vec(p2)
            vec = s0 * p2n
        elif nm == "UxS":
            u = _base_vec(base_row, "U", coords)
            s0 = _base_vec(base_row, "S0", coords)
            vec = u * s0
        else:
            raw = np.array([feat[nm][xy] for xy in coords], dtype=np.float64)
            vec = _minmax_vec(raw)
        Fs.append(vec)
    return np.vstack(Fs) if Fs else np.zeros((0, len(coords)))


def _minmax_vec(v):
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-12:
        return np.full_like(v, 0.5)
    return (v - lo) / (hi - lo)


def _base_vec(base_row, nm, coords):
    src = base_row["F"][nm]
    src_coords = [tuple(x) for x in base_row["coords"]]
    m = {sc: v for sc, v in zip(src_coords, src)}
    return np.array([m.get(xy, 0.0) for xy in coords], dtype=np.float64)


def build_constraints_with(rows, feats, names):
    """构造 A w ≤ 0（每点 × 非最优候选），w 维 = len(names)。"""
    A = []
    for r, (coords, feat) in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        nc = len(q)
        if nc <= 1:
            continue
        qmax = q.max()
        best = set(np.flatnonzero(np.abs(q - qmax) < 1e-12).tolist())
        F = build_feature_matrix(coords, r, feat, names)
        if F.shape[1] != nc:
            continue
        fb = F[:, sorted(best)].mean(axis=1)
        for cidx in range(nc):
            if cidx in best:
                continue
            A.append(F[:, cidx] - fb)
    return np.asarray(A) if A else np.zeros((0, len(names)))


def lp_feasible(A):
    n, d = A.shape
    if n == 0 or d == 0:
        return None
    res = linprog(c=np.zeros(d), A_ub=A, b_ub=np.zeros(n),
                  A_eq=np.ones((1, d)), b_eq=np.array([1.0]),
                  bounds=[(0, None)] * d, method="highs")
    return res


def check_w(rows, feats, names, w):
    """用权重 w 逐点算 argmax，返回选对率与 mean regret。"""
    n_ok, n_tot, regs = 0, 0, []
    for r, (coords, feat) in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        F = build_feature_matrix(coords, r, feat, names)
        if F.shape[1] != len(q):
            continue
        score = w @ F
        pick = int(np.argmax(score))
        reg = q.max() - q[pick]
        regs.append(reg)
        n_tot += 1
        if reg <= 1e-12:
            n_ok += 1
    return n_ok, n_tot, (float(np.mean(regs)) if regs else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=os.path.join("state", "ab", "mm_v3.json"))
    ap.add_argument("--limit", type=int, default=120,
                    help="只用前 N 点（计算 ply 较慢）；0=全部")
    ap.add_argument("--node-cap", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "lp_newfeatures.json"))
    args = ap.parse_args()

    rows, src = load_rows(args.matrix)
    print("=" * 100)
    print("数学检验：增/换指标能否打破「线性不可分离」")
    print("=" * 100)
    print("  v3 数据 %s（%d 点）" % (src, len(rows)))
    if args.limit:
        rows = rows[:args.limit]
        print("  只用前 %d 点（按 worlds 升序，样本偏简单）" % len(rows))

    # 重建 case（需要引擎算新特征）
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c

    # 用 v3 journal 的 key 直接取 case（key = repr(case_key)），比遍历匹配可靠
    v3_journal = os.path.join(ROOT, "state", "ab", "mm_v3.json.journal.jsonl")
    key2case = {}
    ok_key_to_row = {}
    with open(v3_journal, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") != "ok":
                continue
            try:
                import ast
                kk = ast.literal_eval(rec["key"])
            except Exception:
                continue
            c = merged.get(kk)
            if c is not None:
                key2case[rec["key"]] = c
                ok_key_to_row[rec["key"]] = rec["row"]
    print("  v3 journal 可映射 case：%d" % len(key2case))

    # 按 rows 的顺序取对应 case：需要把 rows 与 key 对上。
    # rows 是从 journal 顺序读出的 ok 行，因此重读 journal 保序配对。
    ordered = []
    with open(v3_journal, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("status") == "ok":
                ordered.append((rec["key"], rec["row"]))
    seen = 0
    pairs = []
    for k, r in ordered:
        if seen >= (args.limit if args.limit else 10 ** 9):
            break
        if k in key2case:
            pairs.append((k, r, key2case[k]))
            seen += 1
    print("  配对可用 %d 点" % len(pairs))
    rows = [p[1] for p in pairs]
    cases = [p[2] for p in pairs]

    print()
    print("  重建特征（2-ply / 3-ply / NEW）...", flush=True)
    feats = []
    used_rows = []
    used_cases = []
    t_done = 0
    for r, cand in zip(rows, cases):
        try:
            eng = ExactEngine(cand, node_cap=args.node_cap, time_cap=600.0,
                              world_cap=None)
            wk = WKey.all(eng.n_worlds)
            coords, feat = compute_new_features(eng, wk, cand)
        except Exception:
            continue
        if set(map(tuple, r["coords"])) != set(coords):
            continue
        order = [tuple(x) for x in r["coords"]]
        feat_re = {k: {xy: feat[k][xy] for xy in order if xy in feat[k]}
                   for k in feat}
        feats.append((order, feat_re))
        used_rows.append(r)
        used_cases.append(cand)
        t_done += 1
        if t_done % 20 == 0:
            print("    已重建 %d 点" % t_done, flush=True)
    print("  可用点 %d（坐标与 v3 完全对齐）" % len(used_rows))
    if len(used_rows) < 30:
        print("  可用点太少，无法作判定")
        return 1
    rows = used_rows

    # 定义要检验的指标集
    sets = [
        ("现有 7 指标", list(BASE7)),
        ("7+S0P2", list(BASE7) + ["S0P2"]),
        ("7+P2", list(BASE7) + ["P2"]),
        ("7+P3", list(BASE7) + ["P3"]),
        ("7+P2+P3", list(BASE7) + ["P2", "P3"]),
        ("7+P2+P3+NEW+S0P2+UxS",
         list(BASE7) + ["P2", "P3", "NEW", "S0P2", "UxS"]),
        ("仅 P2+P3", ["P2", "P3"]),
        ("S0+P2+P3", ["S0", "P2", "P3"]),
    ]

    print()
    print("=" * 100)
    print("LP1 判定（每个指标集独立建约束并判定可行性）")
    print("=" * 100)
    print("  %-26s %8s %10s %s" % ("指标集", "维数", "LP 状态", "判定"))
    print("  " + "-" * 74)
    results = []
    for name, names in sets:
        A = build_constraints_with(rows, feats, names)
        if A.shape[0] == 0:
            print("  %-26s %8d  无约束（跳过）" % (name, len(names)))
            continue
        res = lp_feasible(A)
        if res is None:
            continue
        feas = (res.status == 0)
        verdict = "**可分离（regret 可达 0）**" if feas else "仍不可分离"
        print("  %-26s %8d %10s %s"
              % (name, len(names),
                 "可行" if feas else "不可行", verdict), flush=True)
        rec = {"name": name, "n_features": len(names),
               "n_constraints": int(A.shape[0]),
               "lp_status": int(res.status), "feasible": bool(feas)}
        if feas:
            w = res.x
            n_ok, n_tot, mr = check_w(rows, feats, names, w)
            rec["check_ok"] = n_ok
            rec["check_total"] = n_tot
            rec["check_mean_regret"] = mr
            print("      求得 w，实测选对 %d/%d，mean_regret=%.6f"
                  % (n_ok, n_tot, mr if mr is not None else -1))
        results.append(rec)

    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    any_feas = [r for r in results if r["feasible"]]
    base = next((r for r in results if r["name"] == "现有 7 指标"), None)
    if base and not base["feasible"] and not any_feas:
        print("  所有增/换指标方案**仍然不可分离** ⇒")
        print("  问题不是'少算了某个指标'，而是 q 的排序结构不是这些指标的线性函数。")
        print("  ⇒ 继续在这条线性打分路线上加特征，**预期无效**。")
    elif any_feas:
        print("  以下指标集**可达线性可分离**（存在权重使样本内 regret ≡ 0）：")
        for r in any_feas:
            print("    · %s（%d 维，%d 约束）"
                  % (r["name"], r["n_features"], r["n_constraints"]))
        print()
        print("  ⇒ **这是本会话最重要的正面发现**：")
        print("     提高指标表达力确有数学可行性，方向应是加前瞻/交互项。")
        print("     ⚠️ 但 in-sample 可分离 ≠ out-of-sample 有效，必须独立验证。")
    else:
        print("  混合结果，见上表。")

    out = {
        "n_points_used": len(rows),
        "caliber": "v3 初始状态；新特征在初始候选集上算",
        "results": results,
        "caveat": "in-sample；样本偏简单；out-of-sample 未验证",
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
