# -*- coding: utf-8 -*-
"""P1-c 覆盖率重测（C++ 路径）：用 native exact_eval 替代 Python ExactEngine。

=================== 为什么重测 ====================
P1-c 原报告（`classify_p1_coverage.py`）用 **Python** `ExactEngine`，
`time_cap=30s`、`node_cap=5e6`，得到"未覆盖 59.7%，其中 259 点是 time_cap"。

但实测同一批 time_cap 点：
  · Python 路径：>30s（超限，26–30s 卡在临界）
  · **C++ `exact_eval` 路径：0.34–9.84s**（快 5–90 倍，节点数还更多）

⇒ 259 个 time_cap 里**确实存在大量只是"Python 太慢"而非"算不出"的点**。
   这直接动摇"能算精确 q 的样本只覆盖简单局面（40.3%）"这一结论，
   以及据此得出的"权重优化线结构性阻塞"判断。

本脚本用 C++ 路径重跑全 462 个合格点，给出现实可达覆盖率。

=================== 与 P1-c 的口径对齐 ====================
· 合格点筛选：与 `classify_p1_coverage.py` 完全相同（worlds<=max_worlds、可算出世界数、按 worlds 升序）
· 状态分类保持同义但更细：
    ok        —— C++ exact_eval 成功返回非空 q
    node_cap  —— 节点超限
    time_cap  —— 墙钟超限
    no_terms  —— 世界枚举为空
    err       —— 其他异常
· **不重跑已确认 ok 的点**：若 journal 已有该点 ok，直接复用（纪律：不重跑已完成样本）

=================== 仪器验证（先于测量）====================
必须先证明 C++ 路径与 Python 路径**在同一可算点上给出相同 q**：
  · 取若干 P1-c 中 Python 已 ok 的点，用 C++ 重算 q 并对拍
  · max|Δq| 必须 ≤ 1e-9 才允许用 C++ 读数
用法:
  python bench/coverage_cpp.py --verify-only
  python bench/coverage_cpp.py
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
import utils.native as native                             # noqa: E402
from arms_lookahead import ExactEngine, Budget, WKey       # noqa: E402
from dump_metric_matrix_ext import case_key, count_worlds  # noqa: E402
from merge_and_eval_cases import enumerate_worlds_py       # noqa: E402


def cpp_q(case, node_cap):
    """用 C++ exact_eval 算 q。返回 (qmap, v_opt, nodes, err)。

    ⚠️ 这条路径用 **全部枚举世界**（不是 saturate 的单分支），
       结构上不存在 P0-3 截断。
    """
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
        kind = "node_cap" if ("node cap" in msg.lower()
                              or "node_cap" in msg.lower()) else "err"
        return None, None, 0, kind + ":" + msg[:50]
    if not ql:
        return None, None, nodes, "empty_q"
    qmap = {}
    for (x, y), v in zip(co, ql):
        qmap[(int(x), int(y))] = float(v)
    return qmap, float(v_opt), nodes, None


def verify(args):
    """仪器验证：C++ 路径 vs Python 路径在**同一初始状态**下必须一致。

    ==================== 这条验证暴露的关键事实 ====================
    首版验证器拿 Python「saturate 之后」的 q 与 C++ 对拍，结果 max|Δq|=0.125，失败。
    深挖后确认（本次实证，见 review/P0-3根因再定位_saturate破坏q定义.md）：

      · Python 的 `saturate()` 会把**必然安全格自动开掉**，从而把一个状态
        拆成多个「终端」；不同终端的世界集与候选集都不同。
      · 对某个格 c，若它在部分终端里已被开出（不再是候选），
        那么「先在终端上算 after_click 再加权」**无法还原**从原状态出发的值。
        实例：(14,4) 只在终端0（wt=0.5）中是候选，其余终端已开出该格；
              多终端加权给 0.25，而 C++ 从原状态算得 0.375。
      · 但对**在所有终端中都是候选**的格（如 (8,15)），加权恰好等于正确值
        （0.5×0.25 + 0.25×0.5 + 0.25×0.5 = 0.375 = C++）。

    ⇒ 结论：**`saturate` 作为 q 的前置步骤会破坏 q 的定义**，
      不是"只丢了一个分支"那么简单。正确做法是从**初始状态**直接递归
      （C++ `exact_eval` 就是这么做的）。
    ⇒ 因此本验证器改为对拍「同一初始状态」，这是唯一自洽的口径。
    """
    print("=" * 100)
    print("仪器验证：C++ exact_eval vs Python ExactEngine（**同一初始状态**，不经 saturate）")
    print("=" * 100)
    print("  说明：不比较 saturate 之后的值——saturate 会改变候选集定义（见函数注释）。")
    merged = {}
    for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
        p = os.path.join(ROOT, "bench", "data", nm)
        with open(p, encoding="utf-8") as f:
            for c in json.load(f)["cases"]:
                merged[case_key(c)] = c

    # 取 P1-c journal 里 ok 的点（Python 已算出的），按 worlds 升序取最小的若干个
    okc = []
    jp = os.path.join(ROOT, "state", "ab",
                      "p1_coverage.json.journal.jsonl")
    import ast
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
    print("  Python 已 ok 的点：%d（取 worlds 最小的 %d 个做对拍，成本低）"
          % (len(okc), args.verify_n))

    max_abs = 0.0
    n_cmp = 0
    n_pt = 0
    for nw, c in okc[:args.verify_n]:
        # Python 路径：直接从**初始状态**算（不经 saturate）
        try:
            eng = ExactEngine(c, node_cap=args.node_cap, time_cap=300.0,
                              world_cap=None)
            wk = WKey.all(eng.n_worlds)
            cands0 = eng.candidates(wk, eng.opened0)
            pyq = {eng._coord(cd): eng.after_click(cd, wk, eng.opened0)
                   for cd in cands0}
        except Exception:
            continue
        # C++ 路径
        cq, _v, _nodes, err = cpp_q(c, args.node_cap)
        if err:
            print("    worlds=%d C++ 失败：%s（跳过）" % (nw, err))
            continue
        common = set(pyq) & set(cq)
        if not common:
            continue
        md = max(abs(pyq[xy] - cq[xy]) for xy in common)
        max_abs = max(max_abs, md)
        n_cmp += len(common)
        n_pt += 1
        print("    点%d worlds=%5d 比较 %2d 格 max|Δq|=%.3e"
              % (n_pt, nw, len(common), md), flush=True)

    ok = (n_pt > 0) and (max_abs <= 1e-9)
    print()
    print("  对拍 %d 点、%d 格，**max|Δq| = %.3e**（门限 1e-9）⇒ %s"
          % (n_pt, n_cmp, max_abs,
             "通过" if ok else "**失败，禁止用 C++ 读数**"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--verify-n", type=int, default=8)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--node-cap", type=int, default=200_000_000)
    ap.add_argument("--time-cap", type=float, default=300.0,
                    help="墙钟上限（C++ 通常远快于此，仅作保险）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "p1_coverage_cpp.json"))
    args = ap.parse_args()

    if not verify(args):
        print()
        print("  ✘ 仪器验证未通过 ⇒ 拒绝出覆盖率读数")
        return 2
    if args.verify_only:
        return 0

    print()
    print("=" * 100)
    print("P1-c 覆盖率重测（C++ 路径）")
    print("=" * 100)

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
        if nw <= args.max_worlds:
            elig.append((c, nw))
    elig.sort(key=lambda t: t[1])
    if args.limit:
        elig = elig[:args.limit]
    print("  合格点 %d，node_cap=%d，wall_cap=%.0fs"
          % (len(elig), args.node_cap, args.time_cap))

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
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()
    slow = []
    for i, (c, nw) in enumerate(elig):
        ck = repr(case_key(c))
        if ck in done:
            rec = done[ck]
            rows.append(rec["row"])
            continue
        ts = time.perf_counter()
        qmap, v_opt, nodes, err = cpp_q(c, args.node_cap)
        el = time.perf_counter() - ts
        if err is None:
            st, detail = "ok", ""
        elif err.startswith("node_cap"):
            st, detail = "node_cap", err[:60]
        elif err.startswith("empty_worlds"):
            st, detail = "no_terms", err[:60]
        elif err.startswith("empty_q"):
            st, detail = "no_terms", err[:60]
        else:
            st, detail = "err", err[:60]
        row = {"worlds": nw, "limitation": c["limitation"],
               "n_cand": len(c["clicks"]) + len(c["clicks9"]),
               "total": c["total"], "status": st, "detail": detail,
               "secs": round(el, 2), "nodes": nodes}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "row": row}) + "\n")
        jf.flush()
        if el > 20:
            slow.append((nw, el))
        if (i + 1) % 25 == 0:
            nok = sum(1 for r in rows if r["status"] == "ok")
            print("    %d/%d（ok=%d，%.0fs）"
                  % (i + 1, len(elig), nok, time.perf_counter() - t0),
                  flush=True)
    jf.close()

    from collections import Counter
    cnt = Counter(r["status"] for r in rows)
    n = len(rows)
    ok = cnt.get("ok", 0)
    print()
    print("=" * 100)
    print("全样本覆盖率（C++ 路径，替代 Python 路径的 40.3%）")
    print("=" * 100)
    print("  总数 %d" % n)
    for st in ("ok", "node_cap", "time_cap", "no_terms", "err"):
        v = cnt.get(st, 0)
        print("    %-10s %5d  %6.1f%%" % (st, v, 100 * v / n))
    print("    %-10s %5d  %6.1f%%" % ("未覆盖", n - ok, 100 * (n - ok) / n))

    # 分层
    print()
    print("  按 worlds 分层：")
    print("    %-12s %6s %6s %8s %10s"
          % ("区间", "总数", "ok", "ok率", "耗时中位"))
    edges = [100, 500, 2000, 10000]
    labels = ["<=%g" % e for e in edges] + [">%g" % edges[-1]]
    for lab in labels:
        if lab.startswith("<="):
            lim = float(lab[2:])
            rs = [r for r in rows if r["worlds"] <= lim]
            for e in edges:
                if lim == e:
                    rs = [r for r in rows if r["worlds"] <= e]
                    break
            rs = [r for r in rows if r["worlds"] <= lim]
        else:
            rs = [r for r in rows if r["worlds"] > edges[-1]]
        # 修正：按区间而非累计
        for idx_e, e in enumerate(edges):
            lo = edges[idx_e - 1] if idx_e > 0 else 0
            if lab == "<=%g" % e:
                rs = [r for r in rows if lo < r["worlds"] <= e]
                break
        if lab == ">%g" % edges[-1]:
            rs = [r for r in rows if r["worlds"] > edges[-1]]
        if not rs:
            continue
        rs_ok = [r for r in rs if r["status"] == "ok"]
        med = sorted(r["secs"] for r in rs)[len(rs) // 2]
        print("    %-12s %6d %6d %7.1f%% %9.2fs"
              % (lab, len(rs), len(rs_ok), 100 * len(rs_ok) / len(rs), med))

    total_el = time.perf_counter() - t0
    print()
    print("  总耗时 %.0fs（不可用于策略排序）" % total_el)
    if slow:
        print("  最慢的 5 点：%s" % ", ".join("worlds=%d/%.1fs" % s
                                            for s in sorted(slow, key=lambda x: -x[1])[:5]))

    with open(out_p, "w", encoding="utf-8") as f:
        json.dump({"n": n, "status_counts": dict(cnt),
                   "ok_rate": ok / n if n else None,
                   "path": "cpp_exact_eval_full_enumeration",
                   "node_cap": args.node_cap,
                   "note": "C++ 路径；q 用全枚举世界，无 P0-3 截断",
                   "rows": rows, "args": vars(args)},
                  f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out_p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
