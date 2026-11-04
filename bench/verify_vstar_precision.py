# -*- coding: utf-8 -*-
"""P1-b：精度验证——对**已成功**的点提高 time_cap，看 V* 是否逐点不变。

=================== 为什么做这个（Advisor 指出）====================
我在报告里曾写「V* 实际是被截断的近似值」，这是**错的**。
真实代码路径（`arms_lookahead.py:282-286`）：

    def _tick(self):
        if self.nodes > self.node_cap: raise Budget("node cap")
        if 超时:                        raise Budget("time cap")

`Budget` 是**异常**，调用方捕获后**整点丢弃**，不写入结果。
⇒ 已保留的成功点是"在 cap 内完整递归跑完"的点，**没有被静默截断**。

**但这不等于已证明其精度。** 要证明精度，需做两件事：
  1. 对**已成功**的点，提高 cap 重算，看 `V*` 是否逐点不变（本脚本）
  2. 已知答案的小局面手算对照（`verify_after_click_semantics.py` 已做基础件）

=================== 判据 ====================
对每个已成功点，用 cap 与 cap×4 各算一次 `V*`：
  · 两次都成功且 |Δ| < 1e-12 ⇒ 该点 V* 与 cap 无关（在该范围内稳定）
  · 第二次成功但 Δ 显著     ⇒ 原 cap 下的值不完整，需提高 cap
  · 第二次仍 Budget         ⇒ 无法验证该点精度，如实标注

**本脚本不改变任何已有结论，只产出"精度是否可验证"的证据。**

用法:
  python bench/verify_vstar_precision.py --limit 60
  python bench/verify_vstar_precision.py --limit 200 --cap 5000000
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


def eval_vstar(case, node_cap, time_cap):
    """返回 (status, v_opt, nodes)。status ∈ {ok, budget_node, budget_time, err}。"""
    try:
        eng = ExactEngine(case, node_cap=node_cap, time_cap=time_cap,
                          world_cap=None)
    except Exception as e:                                 # noqa: BLE001
        return ("err", None, 0, str(e)[:80])
    if eng.n_worlds == 0:
        return ("err", None, 0, "no worlds")
    wk = WKey.all(eng.n_worlds)
    try:
        terms = [t for t in eng.saturate(wk, eng.opened0)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            return ("err", None, eng.nodes, "no terms")
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _ = terms[0]
        cands = eng.candidates(wk2, op2)
        q = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        return ("ok", max(q.values()), eng.nodes, None)
    except Budget as e:
        msg = str(e)
        st = "budget_node" if "node" in msg else "budget_time"
        return (st, None, eng.nodes, msg)
    except Exception as e:                                 # noqa: BLE001
        return ("err", None, eng.nodes, str(e)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=None)
    ap.add_argument("--max-worlds", type=int, default=20000)
    ap.add_argument("--limit", type=int, default=60,
                    help="最多验证多少个已成功点")
    ap.add_argument("--cap", type=int, default=5_000_000)
    ap.add_argument("--cap-mult", type=int, default=4)
    ap.add_argument("--time-cap", type=float, default=30.0)
    ap.add_argument("--time-cap-hi", type=float, default=120.0)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                  "vstar_precision.json"))
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

    print("=" * 100)
    print("P1-b 精度验证：已成功点在提高 cap 后 V* 是否不变")
    print("=" * 100)
    print("  候选 %d 个合格点，最多验证 %d 个" % (len(elig), args.limit))
    print("  低 cap: node_cap=%d, time_cap=%.0fs" % (args.cap, args.time_cap))
    print("  高 cap: node_cap=%d, time_cap=%.0fs"
          % (args.cap * args.cap_mult, args.time_cap_hi))
    print()

    stats = {"both_ok_same": 0, "both_ok_diff": 0,
             "low_ok_high_budget": 0, "low_budget": 0, "err": 0}
    rows = []
    checked = 0
    t0 = time.perf_counter()

    for c, nw in elig:
        if checked >= args.limit:
            break
        st_lo, v_lo, n_lo, msg_lo = eval_vstar(c, args.cap, args.time_cap)
        if st_lo != "ok":
            stats["low_budget" if st_lo.startswith("budget") else "err"] += 1
            continue
        checked += 1

        st_hi, v_hi, n_hi, msg_hi = eval_vstar(
            c, args.cap * args.cap_mult, args.time_cap_hi)

        if st_hi == "ok":
            d = abs(v_lo - v_hi)
            if d < 1e-12:
                stats["both_ok_same"] += 1
                verdict = "不变"
            else:
                stats["both_ok_diff"] += 1
                verdict = "★变化 Δ=%.3e" % d
        elif st_hi.startswith("budget"):
            stats["low_ok_high_budget"] += 1
            verdict = "高cap仍未求出"
            d = None
        else:
            stats["err"] += 1
            verdict = "高cap出错"

        rows.append({"worlds": nw, "limitation": c["limitation"],
                     "v_lo": v_lo, "v_hi": v_hi,
                     "nodes_lo": n_lo, "nodes_hi": n_hi,
                     "verdict": verdict})
        print("  [%2d] lim=%6.2f worlds=%5d  V*_lo=%.9f  %s"
              % (checked, c["limitation"], nw, v_lo, verdict), flush=True)

    elapsed = time.perf_counter() - t0
    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    total = sum(stats.values())
    print("  已验证（低 cap 下成功）%d 点，耗时 %.0fs" % (checked, elapsed))
    print("    V* 不变            : %d" % stats["both_ok_same"])
    print("    V* 变化            : %d" % stats["both_ok_diff"])
    print("    高 cap 仍未求出    : %d" % stats["low_ok_high_budget"])
    print("    低 cap 未求出(跳过): %d" % stats["low_budget"])
    print("    其他错误           : %d" % stats["err"])
    print()
    if checked == 0:
        print("  ✘ 没有可验证的点")
        return 1
    if stats["both_ok_diff"] == 0:
        print("  ✔ 在所有可验证点上，提高 cap 后 V* **逐点不变**")
        print("    ⇒ 这些点的 V* 在该 cap 范围内是稳定的（精度可支持）")
        if stats["low_ok_high_budget"] > 0:
            print("    ⚠️ 但有 %d 点高 cap 仍未求出 ⇒ 其精度**未被验证**，"
                  "报告时须标注" % stats["low_ok_high_budget"])
    else:
        print("  ✘ 存在 %d 点 V* 随 cap 变化 ⇒ 原 cap 下的值不完整，"
              "需提高 cap 重算" % stats["both_ok_diff"])

    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "rows": rows, "checked": checked,
                   "args": vars(args)}, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
