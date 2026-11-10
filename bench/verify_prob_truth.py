# -*- coding: utf-8 -*-
"""正确性回归：C++ decide 的逐格概率 vs 暴力枚举真值（小盘面，含 rem == n9）。

========================= 为什么要这个脚本 =========================
用户问「计算是否准确」。准确性的唯一判据不是自证（守恒律只保证一阶矩对），
而是**与独立真值逐格比对**：

    真值 = 枚举全部「与盘面数字一致、且雷数 == 剩余雷数」的雷位布局，
           逐格统计「该格是雷」的频率。

C++ 侧只需要一个信号：它返回的 prob 是否等于这个频率。

========================= 重点覆盖 rem == n9 =========================
`get_list_sizes(a, num, listnum)`（cpp/mscore.cpp:416）与 Python
`utils/combinatorics.py:get_list` 同款钳制：

    if (num > listnum - 1) num = listnum - 1;

LAYER 2 / OPTIMAL 物化世界时调用 `get_list_sizes(rem, rem, n9)`。当 rem == n9
时上限被钳成 n9-1 ⇒ 枚举的是「从 n9 个内部格里放 n9-1 个雷」（C(n9,n9-1)=n9 个
世界），而正确语义是「放 n9 个」（C(n9,n9)=1 个世界）。
⇒ 若该行是唯一可行行，物化世界集会**完全不含「内部格全为雷」这个世界**。

本脚本把这类局面单独挑出来打标，逐格与暴力真值对拍。

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/verify_prob_truth.py
    ... --json state/audit/prob_truth.json
"""
import argparse
import io
import itertools
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np                      # noqa: E402

from utils import native                # noqa: E402


# ---------------------------------------------------------------------------
# 模拟器：造真实盘面（与生产同规则：首点安全、flood-fill）
# ---------------------------------------------------------------------------
class Sim:
    def __init__(self, w, h, a, rng):
        self.w, self.h, self.a = w, h, a
        self.mine = set()
        self.cv = np.zeros((h + 2, w + 2), np.int32)
        for y in range(1, h + 1):
            for x in range(1, w + 1):
                self.cv[y, x] = 9
        self.opened = 0
        self.dead = False
        self.won = False
        self.rng = rng
        self.generated = False

    def _generate(self, fx, fy):
        pool = [(x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)
                if not (x == fx and y == fy)]
        self.mine = set(self.rng.sample(pool, self.a))
        self.generated = True

    def _num(self, x, y):
        return sum(1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   if (dx or dy) and (x + dx, y + dy) in self.mine)

    def open(self, x, y):
        if not self.generated:
            self._generate(x, y)
        if (x, y) in self.mine:
            self.dead = True
            return
        st = [(x, y)]
        while st:
            cx, cy = st.pop()
            if not (1 <= cx <= self.w and 1 <= cy <= self.h):
                continue
            if self.cv[cy, cx] != 9 or (cx, cy) in self.mine:
                continue
            n = self._num(cx, cy)
            self.cv[cy, cx] = n
            self.opened += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx or dy:
                            st.append((cx + dx, cy + dy))
        self.won = self.opened >= self.w * self.h - self.a


# ---------------------------------------------------------------------------
# 暴力真值：回溯枚举 + 约束传播（严格回滚，含阳性对照）
# ---------------------------------------------------------------------------
def brute_worlds(cv, w, h, a, cap=4_000_000):
    """返回 (unopened 列表, worlds 列表, 尝试次数)。worlds 里每项是 0/1 元组。"""
    cells = [(x, y) for y in range(1, h + 1) for x in range(1, w + 1)]
    unop = [c for c in cells if cv[c[1], c[0]] == 9]
    num10 = sum(1 for c in cells if cv[c[1], c[0]] == 10)
    need = a - num10
    cons = []
    for (x, y) in cells:
        v = int(cv[y, x])
        if 0 <= v <= 8:
            nb = [(x + dx, y + dy)
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                  if (dx or dy) and 1 <= x + dx <= w and 1 <= y + dy <= h
                  and cv[y + dy, x + dx] == 9]
            if nb:
                cons.append((v, nb))
    idx = {c: i for i, c in enumerate(unop)}
    cidx = [[idx[c] for c in nb] for _v, nb in cons]
    targets = [v for v, _nb in cons]
    cell_cons = [[] for _ in unop]
    for ci, ids in enumerate(cidx):
        for i in ids:
            cell_cons[i].append(ci)
    order = sorted(range(len(unop)), key=lambda i: -len(cell_cons[i]))
    assigned = [0] * len(cons)
    filled = [0] * len(cons)
    val = [-1] * len(unop)
    worlds = []
    used = [0]
    tries = [0]

    def dfs(k):
        if tries[0] > cap:
            return
        if used[0] > need:
            return
        if k == len(order):
            if used[0] == need:
                worlds.append(tuple(val))
            return
        if used[0] + (len(order) - k) < need:
            return
        i = order[k]
        touched = cell_cons[i]
        for v in (1, 0):
            tries[0] += 1
            if v == 1 and used[0] >= need:
                continue
            val[i] = v
            inc = []
            ok = True
            for ci in touched:
                assigned[ci] += v
                filled[ci] += 1
                inc.append(ci)
                t = targets[ci]
                if (assigned[ci] > t
                        or assigned[ci] + (len(cidx[ci]) - filled[ci]) < t):
                    ok = False
                    break
            if ok:
                used[0] += v
                dfs(k + 1)
                used[0] -= v
            for ci in inc:
                assigned[ci] -= v
                filled[ci] -= 1
            val[i] = -1

    dfs(0)
    return unop, worlds, tries[0], (tries[0] > cap)


# ---------------------------------------------------------------------------
# 单个局面的对拍
# ---------------------------------------------------------------------------
def check_board(cv, w, h, a, sim=None, cap=4_000_000):
    unop, worlds, tries, truncated = brute_worlds(cv, w, h, a, cap)
    # ⚠️ 仪器纪律：枚举被 cap 截断时，worlds 只是**部分**世界，
    #   由此算出的频率是错的 —— 必须整例作废，不能当"真值"用。
    #   （首版没做这个检查，把 1 例截断样本报成了 C++ 概率错误：真实盘面
    #    trial=124 应枚举 2718576 个世界，实际只跑到 363258 个就停了。）
    if truncated:
        return {"instrument_error": "brute_enum_truncated",
                "tries": tries, "worlds_brute": len(worlds)}
    if not worlds or len(worlds) > 400_000:
        return None
    n = len(worlds)
    # 阳性对照：真实雷位布局必须出现在枚举里（否则枚举器有漏解）
    if sim is not None:
        truth = tuple(1 if (c in sim.mine) else 0 for c in unop)
        if truth not in worlds:
            return {"instrument_error": "truth_world_missing"}

    sf = native.split_frontier(cv)
    n9 = len(sf[1])
    rem = a - int((cv == 10).sum())

    r = native.decide(cv, w=w, h=h, a=a, node_cap=2_000_000, is_play=False)
    prob = r.get("prob") or {}

    cnt = {}
    for wd in worlds:
        for i, v in enumerate(wd):
            if v == 1:
                cnt[unop[i]] = cnt.get(unop[i], 0) + 1

    # ⚠️ 容差口径（仪器校准，勿随意收紧）：
    #   C++ 非 OPTIMAL 分支（pbs / clicks9 / certain_safe）的概率用 **float(32bit)**
    #   累加，实测与双精度真值差 ~1e-7（相对误差 ~1e-7）。用 1e-9 会把纯精度差异
    #   报成 bug（首版就踩了这个坑：14/50 局面"不一致"全是 4e-08 量级）。
    #   ⇒ 判据取 1e-6：既容得下 float32 舍入，又足以抓出真正的逻辑错误
    #     （逻辑错误的最小可观测偏差是 1/世界数，小盘面 ≥1e-5，且通常远大于此）。
    TOL = 1e-6
    bad = []
    for c in unop:
        if c not in prob:
            continue
        p_cpp = 1.0 - float(prob[c])
        p_bf = cnt.get(c, 0) / n
        if abs(p_cpp - p_bf) > TOL:
            bad.append({"cell": list(c), "cpp_p_mine": round(p_cpp, 9),
                        "brute": round(p_bf, 9),
                        "diff": round(p_cpp - p_bf, 9)})

    # ⚠️ ΣP(mine) 守恒只对「prob 覆盖全部未开格」的局面成立：
    #   certain_safe / clicks9_fallback 等分支只给前沿格概率，内部格不进 prob，
    #   此时 Σ 覆盖格 ≠ 剩余雷数属**正常语义**，不能当错误（首版误判了 14 例）。
    coverage_complete = (len(prob) >= len(unop))
    sum_cpp = sum(1.0 - float(prob[c]) for c in unop if c in prob)

    return {
        "w": w, "h": h, "a": a,
        "n9": n9, "rem": rem, "rem_eq_n9": (n9 == rem and n9 > 0),
        "worlds_brute": n, "n_unopened": len(unop),
        "n_prob_covered": len(prob),
        "coverage_complete": coverage_complete,
        "reason": str(r.get("reason")),
        "world_upper": r.get("world_upper"),
        "situations": r.get("situations"),
        "world_count": (r.get("transition_meta") or {}).get("world_count"),
        "sum_p_mine_cpp": round(sum_cpp, 6),
        "sum_p_mine_brute": round(sum(cnt.get(c, 0) for c in unop) / n, 6),
        "conservation_ok": (None if not coverage_complete
                            else abs(sum_cpp - rem) <= TOL),
        "world_count_matches": (
            None if (r.get("transition_meta") or {}).get("world_count") is None
            else ((r.get("transition_meta") or {}).get("world_count")
                  == r.get("situations"))),
        "n_mismatch": len(bad),
        "mismatch": bad[:12],
        "tries": tries,
        "tolerance": TOL,
    }


def collect_cases(want_rem_n9=10, want_general=40, max_trials=6000):
    """采样：优先收集 rem == n9 的局面，再补一般局面。"""
    rem_cases, gen_cases = [], []
    valid = 0
    inst_errors = {}
    t0 = time.perf_counter()
    for trial in range(max_trials):
        if len(rem_cases) >= want_rem_n9 and len(gen_cases) >= want_general:
            break
        rng = random.Random(trial)
        w, h = rng.choice([(4, 4), (5, 5), (6, 6), (5, 6), (6, 5)])
        a = rng.choice([3, 4, 5, 6, 7, 8])
        if a >= w * h - 1:
            continue
        g = Sim(w, h, a, rng)
        g.open(1, 1)
        if g.dead:
            continue
        for _ in range(rng.choice([0, 1, 2, 3, 4, 5])):
            unop = [(x, y) for y in range(1, h + 1) for x in range(1, w + 1)
                    if g.cv[y, x] == 9]
            if not unop:
                break
            fr = []
            for (x, y) in unop:
                hit = False
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if (1 <= nx <= w and 1 <= ny <= h
                                and 0 <= g.cv[ny, nx] <= 8):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    fr.append((x, y))
            if not fr:
                break
            g.open(*rng.choice(fr))
            if g.dead:
                break
        if g.dead or g.won:
            continue
        valid += 1
        sf = native.split_frontier(g.cv)
        n9 = len(sf[1])
        rem = a - int((g.cv == 10).sum())
        is_rem_n9 = (n9 == rem and n9 > 0)
        if is_rem_n9 and len(rem_cases) >= want_rem_n9:
            continue
        if not is_rem_n9 and len(gen_cases) >= want_general:
            continue
        out = check_board(g.cv, w, h, a, sim=g)
        if out is None or out.get("instrument_error"):
            if out and out.get("instrument_error"):
                inst_errors[out["instrument_error"]] = (
                    inst_errors.get(out["instrument_error"], 0) + 1)
            continue
        out["trial"] = trial
        (rem_cases if is_rem_n9 else gen_cases).append(out)
    return rem_cases, gen_cases, valid, time.perf_counter() - t0, inst_errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(
        ROOT, "state", "audit", "prob_truth.json"))
    ap.add_argument("--want-rem-n9", type=int, default=10)
    ap.add_argument("--want-general", type=int, default=40)
    args = ap.parse_args()

    print("=" * 100)
    print("正确性对拍：C++ decide 逐格概率 vs 暴力枚举真值")
    print("=" * 100)

    rem_cases, gen_cases, valid, dt, inst_errors = collect_cases(
        args.want_rem_n9, args.want_general)
    print(f"扫描 {valid} 个有效盘面，用时 {dt:.1f}s")
    print(f"  重点组 rem == n9：{len(rem_cases)} 个")
    print(f"  一般组           ：{len(gen_cases)} 个")
    if inst_errors:
        print(f"  ⚠️ 仪器作废（不计入结论）：{inst_errors}")
        print("     说明：暴力枚举被 cap 截断 / 枚举器漏掉真实布局 ⇒ 该例不能当真值")

    def report(title, cases):
        print("\n" + "-" * 100)
        print(title)
        print("-" * 100)
        if not cases:
            print("  （无样本 —— 不构成任何结论）")
            return 0, 0, 0
        hdr = (f"{'trial':>6} {'board':>6} {'a':>2} {'n9':>3} {'rem':>4} "
               f"{'reason':>16} {'worlds':>7} {'bad':>4} {'cov':>7} {'Σcpp':>7} {'Σbrute':>7}")
        print("  " + hdr)
        bad_cases = 0
        bad_cells = 0
        for c in cases:
            flag = "" if c["n_mismatch"] == 0 else "  ❌"
            cov = f"{c['n_prob_covered']}/{c['n_unopened']}"
            cons = c.get("conservation_ok")
            cons_txt = ("—" if cons is None else ("✅" if cons else "❌守恒"))
            print(f"  {c['trial']:>6} {c['w']}x{c['h']:<4} {c['a']:>2} {c['n9']:>3} "
                  f"{c['rem']:>4} {c['reason']:>16} {c['worlds_brute']:>7} "
                  f"{c['n_mismatch']:>4} {cov:>7} {c['sum_p_mine_cpp']:>7.3f} "
                  f"{c['sum_p_mine_brute']:>7.3f}{flag} {cons_txt}")
            if c["n_mismatch"]:
                bad_cases += 1
                bad_cells += c["n_mismatch"]
                for m in c["mismatch"][:4]:
                    print(f"        cell={tuple(m['cell'])} "
                          f"cpp_p_mine={m['cpp_p_mine']} brute={m['brute']} "
                          f"diff={m['diff']}")
            if cons is False:
                print(f"        ⚠️ 概率守恒失败：ΣP(mine)={c['sum_p_mine_cpp']} "
                      f"≠ 剩余雷数 {c['rem']}（prob 已覆盖全部未开格）")
        return bad_cases, bad_cells, len(cases)

    b1, c1, n1 = report("[重点组] rem == n9（get_list_sizes 钳制边界）", rem_cases)
    b2, c2, n2 = report("[一般组] 其它局面（回归对照）", gen_cases)

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    total_cases, total_bad_cases, total_bad_cells = n1 + n2, b1 + b2, c1 + c2
    if total_cases == 0:
        print("  ⚠️ 无有效样本，无法判定")
        verdict = "NO_SAMPLE"
    elif total_bad_cases == 0:
        print(f"  ✅ 全部 {total_cases} 个局面逐格一致（0 格偏差）")
        verdict = "PASS"
    else:
        print(f"  ❌ {total_bad_cases}/{total_cases} 个局面存在逐格概率偏差，"
              f"共 {total_bad_cells} 格")
        if b1:
            print(f"     · rem==n9 组：{b1}/{n1} 局面有偏差（{c1} 格）")
        if b2:
            print(f"     · 一般组   ：{b2}/{n2} 局面有偏差（{c2} 格）")
        verdict = "FAIL"

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict,
                   "rem_n9_cases": rem_cases,
                   "general_cases": gen_cases,
                   "valid_boards": valid,
                   "elapsed_s": round(dt, 1)}, f, ensure_ascii=False, indent=1)
    print(f"\n落盘：{args.json}")
    return 0 if verdict in ("PASS", "NO_SAMPLE") else 1


if __name__ == "__main__":
    sys.exit(main())
