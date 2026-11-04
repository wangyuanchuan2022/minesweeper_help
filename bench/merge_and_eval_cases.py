# -*- coding: utf-8 -*-
"""合并多批中局决策点，并用 C++ exact_eval 扩样评估 regret。

=================== 为什么需要合并 ====================
bench/data/midgame_cases.json 只有 400 个原始点，其中满足可枚举条件
（worlds <= 20000）的仅 119 个（29.75%）。
MDE 与 sqrt(n) 成反比：119 点 MDE≈0.0090，500 点约 0.0044。
⇒ 必须合并多批采集结果才能把检出力提上来。

=================== 去重规则（必须严格）====================
不同批次可能采到**同一个决策点**（同一 seed 同一局同一步）。
去重键 = (seed 不存在时用 limitation, total, F, U, tuple(sorted(clicks)),
          tuple(sorted(clicks9)))。仅按 limitation 去重会误删不同局面。

=================== 为什么用 C++ exact_eval ====================
用户要求实验用 C++。regret 需要精确 V*，Python 版单点 0.1~2.5s，
500 点要 10+ 分钟且受"同进程耗时漂移"影响；
C++ 版已逐位对拍通过（140/140，maxΔ=0），加速 9~15x。

⚠️ 耗时读数一律走 bench/timing_protocol.py 的新进程协议，
   不在本脚本内做同进程耗时比较。

用法:
  python bench/merge_and_eval_cases.py
  python bench/merge_and_eval_cases.py --max-worlds 20000 --limit 500
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


def count_worlds(case):
    """纯算术算世界数（零内存），用于筛选可枚举点。"""
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
    """去重键：能唯一标识一个决策点的最小信息集。"""
    return (
        int(c["a"]), int(c["num10"]), int(c["total"]),
        len(c["clicks"]), len(c["clicks9"]),
        tuple(sorted(tuple(x) for x in c["clicks"])),
        tuple(sorted(tuple(x) for x in c["clicks9"])),
    )


def enumerate_worlds_py(case):
    """枚举全部一致世界的雷位（Python 大整数位集）。

    与 bench/arms_lookahead.py::enumerate_worlds 语义一致：
    每组选一个解，剩余雷数从 clicks9 里选，满足全局雷数约束。
    """
    import itertools
    w, h, a, num10 = int(case["w"]), int(case["h"]), int(case["a"]), int(case["num10"])
    W2 = w + 2
    clicks = [tuple(x) for x in case["clicks"]]
    clicks9 = [tuple(x) for x in case["clicks9"]]
    groups = case["groups"]
    ck = case["ck"]
    cv = case["cell_value"]

    base = 0
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            if 10 == int(cv[y][x]):          # 已标记雷
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None,
                    help="要合并的 JSON 列表；默认自动发现")
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--limit", type=int, default=0,
                    help="最多评估多少个合格点（0=全部）")
    ap.add_argument("--node-cap", type=int, default=5_000_000)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "merged_regret.json"))
    ap.add_argument("--journal", default=None,
                    help="逐点结果落盘路径（默认与 --out 同目录，后缀 .journal.jsonl）")
    ap.add_argument("--no-resume", action="store_true",
                    help="忽略已有 journal，从头算")
    args = ap.parse_args()

    if args.inputs:
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p)
                 for p in args.inputs]
    else:
        paths = []
        for name in ("midgame_cases.json", "midgame_cases_ext.json"):
            p = os.path.join(ROOT, "bench", "data", name)
            if os.path.exists(p):
                paths.append(p)

    print("=" * 100)
    print("合并多批中局决策点 + C++ exact_eval 评估 regret")
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
    print("  原始点合计 %d，去重后 %d" % (raw_total, len(merged)))

    # ---- 筛可枚举点 ----
    print()
    print("  筛选中（世界数 <= %d）..." % args.max_worlds, flush=True)
    eligible = []
    for i, c in enumerate(merged.values()):
        try:
            nw = count_worlds(c)
        except Exception:
            continue
        if nw <= args.max_worlds:
            eligible.append((c, nw))
        if (i + 1) % 200 == 0:
            print("    已筛 %d/%d，合格 %d"
                  % (i + 1, len(merged), len(eligible)), flush=True)
    print("  合格点 %d/%d = %.2f%%"
          % (len(eligible), len(merged),
             len(eligible) / max(1, len(merged)) * 100))

    if args.limit:
        eligible = eligible[:args.limit]

    # ---- 断点续跑：逐点 journal ----
    # ⚠️ 为什么需要（Advisor 指出）：单点可跑数分钟，进程异常会浪费整批。
    # 每个点完成后立即 append 一行 JSONL，重启时按 key 跳过已完成点。
    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    journal = args.journal
    if journal is None:
        journal = out_path + ".journal.jsonl"
    elif not os.path.isabs(journal):
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
                k = rec.get("key")
                if k:
                    done[k] = rec
        print()
        print("  断点续跑：journal 已有 %d 条完成记录（%s）"
              % (len(done), os.path.basename(journal)))

    # ---- 用 C++ exact_eval 算 q ----
    print()
    print("=" * 100)
    print("C++ exact_eval 逐点评估（%d 点，已完成 %d）"
          % (len(eligible), len(done)))
    print("=" * 100)
    rows = []
    skipped = {"enum_fail": 0, "cpp_fail": 0, "empty": 0, "resumed": 0}
    jf = open(journal, "a", encoding="utf-8")
    t0 = time.perf_counter()
    for i, (c, nw) in enumerate(eligible):
        ck = repr(case_key(c))
        # 已完成点直接复用
        if ck in done:
            rec = done[ck]
            if rec.get("status") == "ok":
                rows.append(rec["row"])
                skipped["resumed"] += 1
            else:
                skipped[rec.get("reason", "cpp_fail")] = \
                    skipped.get(rec.get("reason", "cpp_fail"), 0) + 1
            continue
        try:
            worlds = enumerate_worlds_py(c)
        except Exception:
            skipped["enum_fail"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "enum_fail"}) + "\n")
            jf.flush()
            continue
        if not worlds:
            skipped["empty"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "empty"}) + "\n")
            jf.flush()
            continue
        hexw = [format(m, "x") for m in worlds]
        cv = np.asarray(c["cell_value"], dtype=np.int32)
        try:
            ql, co, nodes, v_opt = native.mscore.exact_eval(
                hexw, [tuple(x) for x in c["clicks"]],
                [tuple(x) for x in c["clicks9"]], cv,
                int(c["a"]), int(c["w"]), int(c["h"]), args.node_cap, 0)
        except Exception:
            skipped["cpp_fail"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "cpp_fail"}) + "\n")
            jf.flush()
            continue
        if not ql:
            skipped["empty"] += 1
            jf.write(json.dumps({"key": ck, "status": "skip",
                                 "reason": "empty"}) + "\n")
            jf.flush()
            continue
        # 生产实际选格（若快照里有）
        picked = c.get("picked")
        reg_prod = None
        if picked:
            px, py = int(picked[0]), int(picked[1])
            for (x, y), v in zip(co, ql):
                if x == px and y == py:
                    reg_prod = v_opt - v
                    break
        row = {
            "limitation": c["limitation"], "worlds": nw,
            "n_cand": len(ql), "v_opt": v_opt,
            "q": [round(v, 9) for v in ql],
            "coords": [list(p) for p in co],
            "reg_prod": (round(reg_prod, 9) if reg_prod is not None else None),
        }
        rows.append(row)
        # 逐点落盘（断点续跑用）：每个点完成立即 append + flush
        jf.write(json.dumps({"key": ck, "status": "ok", "row": row}) + "\n")
        jf.flush()
        if (i + 1) % 25 == 0:
            print("    %d/%d（%.0fs）"
                  % (i + 1, len(eligible), time.perf_counter() - t0), flush=True)
    elapsed = time.perf_counter() - t0
    jf.close()

    print()
    print("  成功 %d，跳过 %s" % (len(rows), skipped))
    print("  ⚠️ 总耗时 %.1fs **不可用于策略排序**"
          "（同进程漂移，见 bench/report_repeat_slowdown.py）" % elapsed)

    # ---- 统计 ----
    print()
    print("=" * 100)
    print("结果")
    print("=" * 100)
    if not rows:
        print("  无有效点")
        return 1
    n = len(rows)
    print("  可评估决策点 n = %d（MDE 与 sqrt(n) 成反比）" % n)
    regs = [r["reg_prod"] for r in rows if r["reg_prod"] is not None]
    if regs:
        regs_s = sorted(regs)
        print("  生产选格 regret：n=%d  中位 %.6f  均值 %.6f  最大 %.6f"
              % (len(regs), regs_s[len(regs_s) // 2],
                 sum(regs) / len(regs), regs_s[-1]))
        print("  完全选对 %d/%d = %.1f%%"
              % (sum(1 for x in regs if x <= 1e-12), len(regs),
                 sum(1 for x in regs if x <= 1e-12) / len(regs) * 100))
    else:
        print("  快照无 picked 字段 ⇒ 无法算生产 regret")

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"n": n, "rows": rows, "skipped": skipped,
                   "raw_total": raw_total, "dedup": len(merged),
                   "eligible": len(eligible),
                   "USABLE_FOR_RANKING_TIME": False,
                   "args": vars(args)}, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
