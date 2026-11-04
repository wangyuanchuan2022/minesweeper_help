# -*- coding: utf-8 -*-
"""P1-c：全候选集的失败分类 + 按 limitation/worlds 分层的成功率。

=================== 为什么需要（Advisor 指出）====================
我在报告里用「前 40 点中 19 点失败（47.5%）」描述全样本，这是**外推错误**：
  · 前 40 点不是随机子集
  · 47.5% 不能代表 462 个合格点的失败率
  · "失败者都是复杂点"只是推测，未做分层统计

本脚本对**全部 462 个合格点**做：
  1. 精确失败分类（ok / node cap / time cap / 其他）
  2. 按 limitation 分层的成功率
  3. 按 worlds 分层的成功率
  4. 统计"被丢弃点"与"保留点"的分布差异（量化自选择偏差方向）

=================== 为什么能跑完 ====================
用与 P1 完全相同的 cap（node_cap=5e6, time_cap=30s）逐点试，
只记录状态，不算全部指标 ⇒ 与 P1 同代价。
支持 journal 断点续跑（单点可能几十秒）。

用法:
  python bench/classify_p1_coverage.py
  python bench/classify_p1_coverage.py --time-cap 30 --node-cap 5000000
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

from arms_lookahead import ExactEngine, Budget, WKey      # noqa: E402


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


def classify(case, node_cap, time_cap):
    """返回 (status, detail)。status ∈ ok/node_cap/time_cap/err。"""
    try:
        eng = ExactEngine(case, node_cap=node_cap, time_cap=time_cap,
                          world_cap=None)
    except Exception as e:                                 # noqa: BLE001
        return ("err", type(e).__name__ + ":" + str(e)[:60])
    if eng.n_worlds == 0:
        return ("err", "no_worlds")
    wk = WKey.all(eng.n_worlds)
    try:
        terms = [t for t in eng.saturate(wk, eng.opened0)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            return ("err", "no_terms")
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _ = terms[0]
        cands = eng.candidates(wk2, op2)
        # 触发完整递归（与 P1 同路径）
        _ = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        return ("ok", "")
    except Budget as e:
        msg = str(e)
        return ("node_cap" if "node" in msg else "time_cap", msg[:60])
    except Exception as e:                                 # noqa: BLE001
        return ("err", type(e).__name__ + ":" + str(e)[:60])


def bucket_report(rows, key_fn, label, edges):
    """按 key_fn 分桶统计成功率。"""
    buckets = {}
    for r in rows:
        v = key_fn(r)
        b = None
        for e in edges:
            if v <= e:
                b = "<=%g" % e
                break
        if b is None:
            b = ">%g" % edges[-1]
        buckets.setdefault(b, []).append(r)
    print()
    print("  按 %s 分层：" % label)
    print("    %-10s %6s %6s %6s %6s %6s %8s"
          % ("区间", "总数", "ok", "node", "time", "err", "成功率"))
    print("    " + "-" * 62)
    out = {}
    order = ["<=%g" % e for e in edges] + [">%g" % edges[-1]]
    for b in order:
        rs = buckets.get(b, [])
        if not rs:
            continue
        n = len(rs)
        nok = sum(1 for r in rs if r["status"] == "ok")
        nnode = sum(1 for r in rs if r["status"] == "node_cap")
        ntime = sum(1 for r in rs if r["status"] == "time_cap")
        nerr = sum(1 for r in rs if r["status"] == "err")
        print("    %-10s %6d %6d %6d %6d %6d %7.1f%%"
              % (b, n, nok, nnode, ntime, nerr, nok / n * 100))
        out[b] = {"n": n, "ok": nok, "node_cap": nnode,
                  "time_cap": ntime, "err": nerr}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--node-cap", type=int, default=5_000_000)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "p1_coverage.json"))
    ap.add_argument("--journal", default=None)
    args = ap.parse_args()

    if args.inputs:
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p)
                 for p in args.inputs]
    else:
        paths = []
        for nm in ("midgame_cases.json", "midgame_cases_ext.json"):
            p = os.path.join(ROOT, "bench", "data", nm)
            if os.path.exists(p):
                paths.append(p)

    merged = {}
    for p in paths:
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
    elig.sort(key=lambda t: t[1])       # 按 worlds 升序（先跑简单的，早出结果）

    print("=" * 100)
    print("P1 全候选集覆盖统计（失败分类 + 分层成功率）")
    print("=" * 100)
    print("  合格点 %d，node_cap=%d，time_cap=%.0fs"
          % (len(elig), args.node_cap, args.time_cap))

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
        print("  断点续跑：已有 %d 条" % len(done))

    print()
    rows = []
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()
    for i, (c, nw) in enumerate(elig):
        ck = repr(case_key(c))
        if ck in done:
            rows.append(done[ck]["row"])
            continue
        st, detail = classify(c, args.node_cap, args.time_cap)
        row = {"worlds": nw, "limitation": c["limitation"],
               "n_cand": len(c["clicks"]) + len(c["clicks9"]),
               "total": c["total"], "status": st, "detail": detail}
        rows.append(row)
        jf.write(json.dumps({"key": ck, "row": row}) + "\n")
        jf.flush()
        if (i + 1) % 25 == 0:
            nok = sum(1 for r in rows if r["status"] == "ok")
            print("    %d/%d（ok=%d，%.0fs）"
                  % (i + 1, len(elig), nok, time.perf_counter() - t0), flush=True)
    jf.close()

    print()
    print("=" * 100)
    print("全样本结果")
    print("=" * 100)
    n = len(rows)
    from collections import Counter
    c = Counter(r["status"] for r in rows)
    print("  总数 %d" % n)
    for st in ("ok", "node_cap", "time_cap", "err"):
        cnt = c.get(st, 0)
        print("    %-10s %5d  %6.1f%%" % (st, cnt, cnt / n * 100))
    print()
    print("  [!] 这是**全样本**读数，替代此前「前 40 点 47.5%」的外推。")

    lim_edges = [10, 15, 20]
    w_edges = [100, 500, 2000, 10000]
    lim_stats = bucket_report(rows, lambda r: r["limitation"], "limitation", lim_edges)
    w_stats = bucket_report(rows, lambda r: r["worlds"], "worlds", w_edges)

    # 自选择偏差方向：比较 ok 与 非 ok 的分布
    print()
    print("  自选择偏差方向（ok vs 非 ok 的中位数）：")
    ok_rows = [r for r in rows if r["status"] == "ok"]
    bad_rows = [r for r in rows if r["status"] != "ok"]
    def med(v):
        if not v:
            return None
        s = sorted(v)
        return s[len(s) // 2]
    for key in ("limitation", "worlds", "n_cand"):
        m_ok = med([r[key] for r in ok_rows])
        m_bad = med([r[key] for r in bad_rows])
        print("    %-12s ok 中位 %-10s 非ok 中位 %-10s"
              % (key, ("%.2f" % m_ok if isinstance(m_ok, float) else m_ok),
                 ("%.2f" % m_bad if isinstance(m_bad, float) else m_bad)))
    print()
    print("  ⇒ 若非 ok 的中位数明显更大，说明丢弃偏向复杂点（自选择偏差方向）。")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": n, "status_counts": dict(c),
                   "limitation_buckets": lim_stats,
                   "worlds_buckets": w_stats,
                   "args": vars(args)}, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
