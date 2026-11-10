# -*- coding: utf-8 -*-
"""决策记录分析器：读 state/decide_trace.jsonl，验证本会话的争议结论。

用法（跑完几局真实扫雷后）：
    & 'D:\anaconda3\envs\py310\python.exe' utils\analyze_decide_trace.py

回答四个问题（全部用**真实端到端数据**，非离线推演）：
  Q1  limitation 与真实 decide 耗时是否相关？方向对不对？
  Q2  win_rate 层真实触发了几次？（limitation<=wr_threshold 是否在用）
  Q3  内存门是否「假放行」？（estimated_bytes 占 1.5GB 的比例分布）
  Q4  各 reason 的真实分布与耗时，哪层在真正干活？
"""
import json
import math
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "state", "decide_trace.jsonl")

LIMIT_BYTES = 1536.0 * 1024 * 1024   # 1.5GB 硬上限（用户铁律）
BUDGET_MS = 30000.0                  # DECISION_TIME_BUDGET


def spearman(x, y):
    """Spearman 秩相关（无 scipy 依赖，并列取平均秩）。"""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and abs(v[order[j + 1]] - v[order[i]]) < 1e-12:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx > 0 and dy > 0 else float("nan")


def pct(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def main():
    if not os.path.exists(PATH):
        print(f"没有记录文件：{PATH}")
        print("先跑几局真实扫雷（记录点默认开启，MSW_DECIDE_TRACE=0 可关）。")
        return 1

    rows = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

    if not rows:
        print("记录文件为空。")
        return 1

    sessions = Counter(r.get("session") for r in rows)
    print(f"=== 记录概况 ===")
    print(f"  文件: {PATH}")
    print(f"  总记录数: {len(rows)}")
    print(f"  会话数: {len(sessions)}")
    for s, n in sessions.most_common(5):
        print(f"    {s}: {n} 条")

    valid = [r for r in rows if r.get("reason") is not None]
    print(f"\n  有效决策（有 reason）: {len(valid)}/{len(rows)}")

    # ---------- Q4 reason 分布 ----------
    print(f"\n=== Q4  reason 分布（哪层在真正干活）===")
    c = Counter(r["reason"] for r in valid)
    tot = len(valid)
    for k, v in c.most_common():
        print(f"  {k:>22}: {v:>6} ({100*v/tot:5.2f}%)")

    # ---------- Q2 win_rate 是否触发 ----------
    print(f"\n=== Q2  win_rate 层触发情况 ===")
    n_wr = c.get("win_rate", 0)
    print(f"  reason=win_rate 次数: {n_wr}")
    if n_wr == 0:
        print("  ⇒ 从未触发。请查 Q1b 的 cost_log2 分布确认是被哪道门挡住。")
    else:
        print(f"  ⇒ 触发了 {n_wr} 次，占比 {100*n_wr/tot:.2f}%")
        wrms = sorted(r["ms"] for r in valid if r["reason"] == "win_rate")
        print(f"  耗时: p50={wrms[len(wrms)//2]:.1f}ms max={wrms[-1]:.1f}ms")

    # ---------- Q1 limitation 与耗时相关性 ----------
    # ⚠️ limitation 自 2026-11-08 起**已不参与任何决策**（仅诊断字段）。
    # 实测其与真实成本方向相反（重计算层 Spearman 负值），此处保留
    # 仅作**已废弃指标的对照列**，判读请以下方 Q1b 的 cost_log2 为准。
    print(f"\n=== Q1  [DEPRECATED] limitation 与真实耗时 ===")
    print("  提示：limitation 已退出决策链，本节仅作历史对照。")
    with_lim = [r for r in valid if isinstance(r.get("limitation"), (int, float))
                and r["limitation"] >= 0]
    print(f"  有 limitation 字段的记录: {len(with_lim)}")
    if len(with_lim) >= 10:
        rho_all = spearman([r["limitation"] for r in with_lim],
                           [r["ms"] for r in with_lim])
        print(f"  全量 Spearman = {rho_all:+.3f}")

        # 分层：只在真正重计算的层内评估
        heavy = [r for r in with_lim
                 if r["reason"] in ("limitation", "clicks9_fallback", "memory_gate")]
        if len(heavy) >= 10:
            rho_h = spearman([r["limitation"] for r in heavy],
                             [r["ms"] for r in heavy])
            print(f"  重计算层（limitation/clicks9/memory_gate，n={len(heavy)}）"
                  f" = {rho_h:+.3f}")
        opt = [r for r in with_lim if r["reason"] == "optimal"]
        if len(opt) >= 10:
            rho_o = spearman([r["limitation"] for r in opt],
                             [r["ms"] for r in opt])
            print(f"  OPTIMAL 层（n={len(opt)}） = {rho_o:+.3f}")
        print("  判读：|rho|>=0.8 强相关；正号=越大越慢（正确方向）")

    # ---------- Q1b cost_log2（当前生效的门槛量）----------
    print(f"\n=== Q1b  [CURRENT] cost_log2 与真实耗时（前瞻准入门槛的唯一输入）===")
    with_cost = [r for r in valid if isinstance(r.get("cost_log2"), (int, float))
                 and r["cost_log2"] >= 0]
    print(f"  有 cost_log2 字段的记录: {len(with_cost)}/{len(valid)}")
    if len(with_cost) == 0:
        print("  ⚠️ 无 cost_log2 记录。若 trace 里全是旧记录（schema=1），")
        print("     说明该字段是后补的，旧的无法回溯；请用新 trace 复跑。")
    else:
        cv = [r["cost_log2"] for r in with_cost]
        sc = sorted(cv)
        print(f"  分布: p10={sc[int(.1*len(sc))]:.1f} p50={sc[len(sc)//2]:.1f} "
              f"p90={sc[int(.9*len(sc))]:.1f} max={sc[-1]:.1f}")
        print(f"  COST_CAP=20（2^20 世界）覆盖: "
              f"{sum(1 for v in cv if v <= 20)}/{len(cv)} "
              f"({100*sum(1 for v in cv if v <= 20)/len(cv):.1f}%)")
        # 与耗时相关性（这是新门槛是否「真的相关」的直接检验）
        if len(with_cost) >= 10:
            rho_c = spearman([r["cost_log2"] for r in with_cost],
                             [r["ms"] for r in with_cost])
            print(f"  全量 Spearman(cost_log2, ms) = {rho_c:+.3f}")
            heavy_c = [r for r in with_cost
                       if r["reason"] in ("limitation", "clicks9_fallback",
                                          "memory_gate", "win_rate")]
            if len(heavy_c) >= 10:
                print(f"  重计算层（n={len(heavy_c)}） = "
                      f"{spearman([r['cost_log2'] for r in heavy_c], [r['ms'] for r in heavy_c]):+.3f}")
        print("  判读：cost_log2 越大应越慢（正号）；负号说明门槛量仍与成本背离。")

    # ---------- Q2b 前瞻准入门的分解（cost_log2 vs mem.allowed vs DSTAR）----------
    print(f"\n=== Q2b  前瞻层被哪道门挡住（cost_log2<=20 AND mem.allowed AND wu>400）===")
    withc = [r for r in valid if isinstance(r.get("cost_log2"), (int, float))]
    if withc:
        n_cost = sum(1 for r in withc if r["cost_log2"] <= 20)
        n_mem = sum(1 for r in withc if float(r.get("mem_pen") or 999) <= 100)
        n_dstar = sum(1 for r in withc if float(r.get("world_upper") or 0) > 400)
        both = sum(1 for r in withc
                   if r["cost_log2"] <= 20
                   and float(r.get("mem_pen") or 999) <= 100
                   and float(r.get("world_upper") or 0) > 400)
        print(f"  样本 {len(withc)}")
        print(f"  通过 cost_log2<=20    : {n_cost}")
        print(f"  通过 mem.allowed      : {n_mem}")
        print(f"  通过 world_upper>400  : {n_dstar}")
        print(f"  三道门**同时**通过    : {both}  ← 这些才真正进 win_rate")
        print("  ⇒ 若 both 远小于任一单门，说明是某道门在主导拦截，可据此定位。")


    # ---------- Q3 内存门 ----------
    print(f"\n=== Q3  内存门「假放行」检查 ===")
    with_mem = [r for r in valid if isinstance(r.get("estimated_bytes"), (int, float))
                and r["estimated_bytes"] >= 0]
    print(f"  有 estimated_bytes 的记录: {len(with_mem)}")
    if with_mem:
        pen = sorted(r.get("mem_pen", r["estimated_bytes"] / LIMIT_BYTES * 100)
                     for r in with_mem)
        print(f"  占 1.5GB 预算比例: p50={pen[len(pen)//2]:.2f}%  "
              f"p90={pen[int(.9*len(pen))]:.2f}%  max={pen[-1]:.2f}%")
        over = [r for r in with_mem if r["estimated_bytes"] > LIMIT_BYTES]
        print(f"  超 1.5GB 的记录: {len(over)}")
        # 估算 vs 真实：C(n9,rem) 缺失导致低估
        print("  说明: estimated_bytes = world_upper × candidates × 2304B，")
        print("        不含 C(n9, rem) 项 ⇒ 该值偏小，不能作为真实规模依据。")
        big_n9 = [r for r in with_mem if r.get("n9", 0) > 0]
        if big_n9:
            print(f"  其中 n9>0（有内部无约束格）的记录: {len(big_n9)}")
            print(f"    这些记录的 n9: p50={pct([r['n9'] for r in big_n9], 50):.0f} "
                  f"max={max(r['n9'] for r in big_n9)}")
            print(f"    ⚠️ 只要 rem_mines 也大，真实世界数远超估算（C(n9,rem) 爆炸）")

    # ---------- 耗时总览 ----------
    print(f"\n=== 耗时总览 ===")
    mss = sorted(r["ms"] for r in valid)
    print(f"  p50={mss[len(mss)//2]:.1f}ms  p90={mss[int(.9*len(mss))]:.1f}ms  "
          f"max={mss[-1]:.1f}ms  （预算 {BUDGET_MS:.0f}ms）")
    slow = [r for r in valid if r["ms"] > 1000]
    print(f"  超 1s 的决策: {len(slow)}")
    for r in slow[:5]:
        print(f"    {r['reason']} ms={r['ms']:.0f} wu={r.get('world_upper')} "
              f"n9={r.get('n9')} cand={r.get('cand')} lim={r.get('limitation')}")

    # ---------- 无效坐标 ----------
    print(f"\n=== 无效坐标（决策不可用）===")
    bad = [r for r in valid if isinstance(r.get("x"), (int, float)) and r["x"] < 1]
    print(f"  次数: {len(bad)}")
    for r in bad[:5]:
        print(f"    {r['reason']} wu={r.get('world_upper')} num9={r.get('num9')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
