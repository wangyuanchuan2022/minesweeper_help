# -*- coding: utf-8 -*-
"""确凿 bug 复现：`get_list_sizes` 钳制导致物化世界少 1 个雷（rem == n9 时）。

========================= 证据链（全部可自证，不需要真值） =========================

【证据 1】同一批 odometer 行产生两个数，二者不等：
    situations      = count_situations_exact = Σ_rows [rem==0 ? 1 : C(n9, rem)]
    world_count     = transition_meta.world_count = Σ_rows Σ_{s∈sizes} C(n9, s)
  这两者在数学上**必须相等**（都是"物化出来的世界总数"）。
  实测：situations=30，world_count=36 ⇒ 分叉 6。

【证据 2】分叉量恰好等于「rem == n9 的行数 × (n9-1)」：
    get_list_sizes(rem, rem, n9) 里 `if (num > listnum-1) num = listnum-1;`
    ⇒ rem == n9 时被钳成 n9-1 ⇒ 枚举 C(n9, n9-1)=n9 个世界（应为 C(n9,n9)=1）
    ⇒ 每行多出 (n9-1) 个世界
    n9=2 时每行多 1，分叉 6 ⇒ 6 行 rem==n9。与证据 3 的算术完全吻合。

【证据 3】ΣP(mine) ≠ 剩余雷数（概率守恒律，纯数学恒等式）：
    设 x 行 rem=1（每行 2 世界，雷数正确 = 99）
       z 行 rem=2（每行 2 世界，钳制后少 1 雷 ⇒ 98）
    x + z = 18（world_upper）
    Σ世界数 = 2x + 2z = 36 ⇒ 与证据 1 一致
    situations = 2x·C(2,1)/2... 实算：situations = x·C(2,1) + z·C(2,2) = 2x + z = 30
    ⇒ x=12, z=6
    ΣP(mine) = [2x·99 + 2z·98] / 36 = [2376 + 1176]/36 = 3552/36 = 98.6667
    实测 ΣP(mine) = 98.666667 ✅ 三者完全闭合

【结论】OPTIMAL 层在 rem == n9 的 odometer 行上物化了「雷数不足」的世界，
        逐格概率因此错误。该局面下内部格必全是雷（p_safe 应为 0），
        错误概率会诱导点必雷格 ⇒ 输掉本可赢的局。

========================= 修复 =========================
`cpp/mscore.cpp:get_list_sizes`：
    - if (num > (int64_t)listnum - 1) num = (int64_t)listnum - 1;
    + if (num > (int64_t)listnum)     num = (int64_t)listnum;
同步修 `utils/combinatorics.py:get_list` 的同款钳制（保持参考实现一致）。

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/repro_world_count_split.py
    ... --cv <npy>       # 指定盘面；缺省用内置找到的 30x16 盘面
    ... --json state/audit/world_count_split.json
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np                      # noqa: E402

from utils import native                # noqa: E402
from utils.combinatorics import get_list  # noqa: E402

DEFAULT_CV = os.path.join(ROOT, "output", "_conservation_cv.npy")


def _comb(n, k):
    if k < 0 or k > n:
        return 0
    r = 1
    for i in range(k):
        r = r * (n - i) // (i + 1)
    return r


def sizes_from_cpp_clamp(rem, n9):
    """复刻 cpp get_list_sizes(rem, rem, n9) 的当前行为（含钳制）。"""
    a = rem if rem >= 1 else 1
    num = rem
    if num > n9 - 1:
        num = n9 - 1
    if num < 1:
        num = 1
    if num < a:
        a = num
    return list(range(a, num + 1))


def sizes_correct(rem, n9):
    """正确语义：从 n9 格里放 rem 个，rem 允许等于 n9。"""
    if n9 <= 0:
        return []
    if rem < 0 or rem > n9:
        return []
    return [rem]


def analyze_cv(cv, a, w, h):
    unop = [(x, y) for y in range(1, h + 1) for x in range(1, w + 1)
            if cv[y, x] == 9]
    num10 = int((cv == 10).sum())
    rem_total = a - num10
    sf = native.split_frontier(cv)
    nF, n9 = len(sf[0]), len(sf[1])

    r = native.decide(cv, w=w, h=h, a=a, node_cap=2_000_000, is_play=False)
    prob = r.get("prob") or {}
    covered = [c for c in unop if c in prob]
    sum_mine = sum(1.0 - float(prob[c]) for c in covered)
    meta = r.get("transition_meta") or {}
    situations = r.get("situations")
    world_count = meta.get("world_count")

    out = {
        "w": w, "h": h, "a": a,
        "n_frontier": nF, "n9": n9, "rem_total": rem_total,
        "reason": str(r.get("reason")),
        "world_upper": r.get("world_upper"),
        "situations": situations,
        "world_count": world_count,
        "n_unopened": len(unop), "n_prob_covered": len(covered),
        "sum_p_mine": round(sum_mine, 6),
        "conservation_ok": (abs(sum_mine - rem_total) < 1e-6
                            if len(covered) >= len(unop) else None),
        "world_count_matches": (None if (world_count is None
                                         or situations is None)
                                else world_count == situations),
        "clamp_table": [],
    }
    # 枚举 rem 的每个可能取值，展示钳制与正确语义的分叉
    for rem in range(0, min(n9 + 2, 8)):
        s_cpp = sizes_from_cpp_clamp(rem, n9)
        s_ok = sizes_correct(rem, n9)
        n_cpp = sum(_comb(n9, s) for s in s_cpp)
        n_ok = sum(_comb(n9, s) for s in s_ok)
        out["clamp_table"].append({
            "rem": rem, "sizes_cpp": s_cpp, "sizes_correct": s_ok,
            "worlds_cpp": n_cpp, "worlds_correct": n_ok,
            "split": n_cpp - n_ok,
            "rem_eq_n9": (rem == n9),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv", default=DEFAULT_CV)
    ap.add_argument("--json", default=os.path.join(
        ROOT, "state", "audit", "world_count_split.json"))
    args = ap.parse_args()

    print("=" * 100)
    print("确凿 bug：get_list_sizes 钳制 ⇒ rem == n9 时物化世界少 1 个雷")
    print("=" * 100)

    # ---- [0] 纯组合学层：钳制表（不依赖任何盘面）----
    print("\n[0] 钳制表（n9=2 为例）：rem == n9 时 C++ 枚举的世界数 ≠ 正确值")
    print(f"    {'rem':>4} {'sizes(C++)':>14} {'sizes(正确)':>14} "
          f"{'世界数(C++)':>12} {'世界数(正确)':>13} {'多出':>5}")
    n9_demo = 2
    for rem in range(0, 4):
        s_cpp = sizes_from_cpp_clamp(rem, n9_demo)
        s_ok = sizes_correct(rem, n9_demo)
        n_cpp = sum(_comb(n9_demo, s) for s in s_cpp)
        n_ok = sum(_comb(n9_demo, s) for s in s_ok)
        mark = "  ← rem==n9" if rem == n9_demo else ""
        print(f"    {rem:>4} {str(s_cpp):>14} {str(s_ok):>14} "
              f"{n_cpp:>12} {n_ok:>13} {n_cpp - n_ok:>5}{mark}")

    print("\n[0b] Python get_list 同款钳制（参考实现，需同步修）")
    for n in (2, 3, 5):
        it = get_list(n, n, n)
        reported = next(it)
        got = list(it)
        print(f"    get_list({n},{n},{n}) → 报总数 {reported}，实枚举 {len(got)} 个"
              f"（正确应为 1 个 = C({n},{n})）"
              f"  {'❌' if len(got) != 1 else '✅'}")

    # ---- [1] 真实盘面：world_count vs situations ----
    if not os.path.exists(args.cv):
        print(f"\n[1] ⚠️ 找不到盘面文件 {args.cv}")
        print("    先用 bench/verify_prob_truth.py 或 --cv 指定一个盘面")
        return 0
    cv = np.load(args.cv)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    print(f"\n[1] 真实盘面 {w}x{h}（{args.cv}）")
    out = analyze_cv(cv, 99, w, h)
    print(f"    未开格={out['n_unopened']} 前沿={out['n_frontier']} "
          f"内部格 n9={out['n9']} 剩余雷数={out['rem_total']}")
    print(f"    reason={out['reason']} world_upper={out['world_upper']} "
          f"situations={out['situations']} world_count={out['world_count']}")
    print(f"    ΣP(mine)={out['sum_p_mine']}  剩余雷数={out['rem_total']}  "
          f"偏差={out['rem_total'] - out['sum_p_mine']:.6f}")
    print()
    ok_wc = out["world_count_matches"]
    ok_cons = out["conservation_ok"]
    if ok_wc is None:
        txt_wc = "—"
    elif ok_wc:
        txt_wc = "✅ 一致"
    else:
        txt_wc = "❌ 分叉 %d" % (out["world_count"] - out["situations"])
    if ok_cons is None:
        txt_cons = "—（prob 未覆盖全部未开格，不适用）"
    elif ok_cons:
        txt_cons = "✅ 一致"
    else:
        txt_cons = "❌ 差 %.4f" % (out["rem_total"] - out["sum_p_mine"])
    print(f"    证据1 物化世界数 == 准入计数 : {txt_wc}")
    print(f"    证据3 ΣP(mine) == 剩余雷数  : {txt_cons}")

    print("\n[2] 该盘面 rem 的钳制分叉表（n9=%d）" % out["n9"])
    print(f"    {'rem':>4} {'sizes(C++)':>14} {'sizes(正确)':>14} "
          f"{'世界数(C++)':>12} {'世界数(正确)':>13} {'多出':>5}")
    for row in out["clamp_table"]:
        mark = "  ← rem==n9" if row["rem_eq_n9"] else ""
        print(f"    {row['rem']:>4} {str(row['sizes_cpp']):>14} "
              f"{str(row['sizes_correct']):>14} {row['worlds_cpp']:>12} "
              f"{row['worlds_correct']:>13} {row['split']:>5}{mark}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    bug = (ok_wc is False) or (ok_cons is False)
    if bug:
        print("  ❌ BUG 确认（两条独立证据同时成立）")
        if ok_wc is False:
            print(f"     · 物化世界数 {out['world_count']} ≠ 准入计数 "
                  f"{out['situations']}（差 {out['world_count'] - out['situations']}）")
        if ok_cons is False:
            print(f"     · ΣP(mine)={out['sum_p_mine']} ≠ 剩余雷数 "
                  f"{out['rem_total']}（差 {out['rem_total'] - out['sum_p_mine']:.4f}）")
        print("\n  根因：cpp/mscore.cpp:416 get_list_sizes 把 num 钳到 listnum-1；")
        print("        OPTIMAL 物化调用 get_list_sizes(rem, rem, n9)，")
        print("        rem == n9 时枚举「放 n9-1 个雷」而非「放 n9 个」")
        print("  修复：`num > listnum-1` → `num > listnum`（同步修 Python get_list）")
        verdict = "BUG_CONFIRMED"
    else:
        print("  ✅ 未发现分叉")
        verdict = "PASS"

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict, "board": out}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n落盘：{args.json}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
