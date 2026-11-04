# -*- coding: utf-8 -*-
"""导出「候选格 × 指标打分」矩阵 + 真值 q，供权重拟合分析复用。

=================== 为什么需要这个中间层 ====================
上一轮我用「线性 prior 的选格必然落在某一单臂的选格集合内」论证
「拟合权重没有起点」——**这个论证是错的**：
  两个指标各自第二好的格，加权和可能超过各自第一好的格 ⇒
  argmax(w·f) 可以落在**任何单臂都没选过**的格子上。
所以必须真拟合。拟合需要逐候选格的指标打分 + 真值 q；
重算这些要跑精确 V*（分钟级），故单独落盘一次，后续分析秒级迭代。

=================== 打分归一化（决定权重可解释性）====================
每个决策点内，把每个指标**min-max 归一化到 [0,1]**。
理由：不同指标量纲差几个数量级（IG 是格数、safety 是概率），
不归一化的话权重完全被量纲支配，拟合结果无意义。
归一化后 w_i 的含义 = "该指标在本题内的相对重要性"。

方向统一：所有指标都转成**越大越好**（R 取负）。

用法:
  python bench/dump_metric_matrix.py --max-worlds 400 --time-cap 15
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

from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402

# 指标名（方向已统一为"越大越好"）
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


def bdist(w, h, pos):
    x, y = pos
    return min(x - 1, w - x, y - 1, h - y)


def minmax(d):
    """把 dict 的值归一化到 [0,1]；常数向量 → 全 0.5（无区分力）。"""
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-12:
        return {k: 0.5 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lim", type=float, default=8.0)
    ap.add_argument("--max-lim", type=float, default=60.0)
    ap.add_argument("--max-worlds", type=int, default=400)
    ap.add_argument("--node-cap", type=int, default=3_000_000)
    ap.add_argument("--time-cap", type=float, default=15.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "metric_matrix.json"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases
              if args.min_lim < c["limitation"] <= args.max_lim]

    print("=" * 100)
    print(f"导出指标打分矩阵：limitation ∈ ({args.min_lim}, {args.max_lim}]，"
          f"世界数 ≤ {args.max_worlds:,}")
    print("=" * 100, flush=True)

    out_rows = []
    skipped = {"world_cap": 0, "budget": 0, "err": 0}
    t_start = time.perf_counter()

    for ci, c in enumerate(sample):
        nw = count_worlds(c)
        if nw > args.max_worlds:
            skipped["world_cap"] += 1
            continue
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=args.time_cap,
                              world_cap=None)
        except Exception:                                  # noqa: BLE001
            skipped["err"] += 1
            continue
        if eng.n_worlds == 0:
            skipped["err"] += 1
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk_all, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            skipped["err"] += 1
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        w, h = int(c["w"]), int(c["h"])

        # ---- 真值 q ----
        try:
            q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            skipped["budget"] += 1
            continue
        if not q:
            skipped["err"] += 1
            continue

        # ---- 各指标原始打分（未归一化）----
        raw = {m: {} for m in METRICS}
        for cd in cands:
            raw["S0"][cd] = eng.safety(cd, wk2)
            # L：到边界的距离（用户定义的"位置先验"，越大 = 越靠中心）
            raw["L"][cd] = float(bdist(w, h, eng._coord(cd)))

        for cd in cands:
            try:
                p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            except Budget:
                p_safe, outs = 0.0, []

            # IG：safety × E[点开后新开格数]
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

            # U：p_safe × 后续最差候选安全率
            worst = 1.0
            for sub, op3, wt in outs:
                c3 = eng.candidates(sub, op3)
                if c3:
                    worst = min(worst, min(eng.safety(x, sub) for x in c3))
            raw["U"][cd] = p_safe * worst

            # E：p_safe × E[点开后必然安全格数]
            accE = 0.0
            accB = 0.0
            accR = 0.0
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
            raw["R"][cd] = accR          # 越小越好 ⇒ 归一化后取负

        # ---- 归一化 + 方向统一 ----
        norm = {}
        for m in METRICS:
            nv = minmax(raw[m])
            if m == "R":
                nv = {k: 1.0 - v for k, v in nv.items()}
            norm[m] = nv

        out_rows.append({
            "case_idx": ci,
            "limitation": c["limitation"],
            "worlds": nw,
            "w": w, "h": h,
            "cands": [list(eng._coord(cd)) for cd in cands],
            "q": [round(q[cd], 9) for cd in cands],
            "F": {m: [round(norm[m][cd], 9) for cd in cands]
                  for m in METRICS},
        })
        if (len(out_rows) % 5) == 0:
            print(f"  已处理 {len(out_rows)} 个决策点 "
                  f"（{time.perf_counter() - t_start:.0f}s）", flush=True)

    print()
    print(f"可覆盖 {len(out_rows)}/{len(sample)}（{skipped}）")
    if not out_rows:
        return 1

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {"metrics": list(METRICS), "rows": out_rows,
               "skipped": skipped, "args": vars(args)}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"已写 {out}（{len(out_rows)} 点，"
          f"{sum(len(r['cands']) for r in out_rows)} 个候选格）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
