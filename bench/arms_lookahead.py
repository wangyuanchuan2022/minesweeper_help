# -*- coding: utf-8 -*-
"""选格策略对比：纯 safety / 期望展开 / 2-ply / 3-ply / 生产 win_rate / **精确最优**

=================== 这个实验要回答什么 ====================
1. 生产 win_rate（cpp/mscore.cpp 的 wr_f）是不是"真实最优胜率"？
2. 2-ply / 3-ply 前瞻能不能补上缺口？值多少？
3. 期望展开（信息增益代理）值多少？
4. 各臂离**精确最优**还差多远（纯算法上限，不含视觉/点击损耗）？
5. 判定题："safety = 1.0 的格是否必然是最优点击？"

=================== wr_f 为什么不是 V*（理论依据，逐行可查）====================
cpp/mscore.cpp:626-704 的 wr_f：
  a) 点开后只写"邻域已标雷数(cnt10)"（:676 `nb[u,v]=c10`），**不做 flood-fill**；
     真实点开 0 会连锁开一大片，免费信息被系统性低估。
  b) 世界划分用"邻域雷数桶"近似，不按实际观测到的数字重建约束。
  c) `depth > depth_limit(200/len(clicks))` 直接 `return 1.0`（:633），截断处高估。
⇒ wr_f 是启发式估值，不是 V*(S)。本文件用 ExactEngine 给出忠实实现的 V*。

=================== 精确引擎的语义（与真实扫雷一致）====================
  · 世界 = 与观测一致的全部雷布局，等权（均匀先验）
  · 点开后按**实际布局**算数字并 flood-fill（0 连锁展开）
  · 观测分支 = (已开集合, 每个新开格的数字)；不同观测必须分叉
  · 免费推理 = 把"在所有剩余世界里都不是雷"的格**全部**开掉（零风险）
  · 胜利 = 已开格数 == w*h - a

=================== 关键正确性坑（本轮实测，务必保留注释）====================
免费推理**必须先饱和全部分叉路径**再评估候选：
  若只开一个安全格、一遇分叉就返回，则"先开哪个安全格"会改变结果
  （实测同一局面：引擎给 V*=1.0000、独立实现给 V*=0.8462，两者都自洽）。
  正确写法 = 对每个观测分支递归继续饱和，直到**该分支内再无必然安全格**，
  再把各终端态按权重汇总。这样结果对"先开哪个安全格"严格无关。

=================== 安全设计 ====================
· 只读 bench/data/wr_cases.json，不跑生产 win_rate 递归、不改生产配置
· 硬上限 --node-cap（默认 5e5/用例）+ --time-cap（默认 120s/用例）
  超限记 budget_exceeded 跳过，**不崩溃**
· 纯离线，结果落 bench/data/arms_result.json

用法:
  python bench/arms_lookahead.py
  python bench/arms_lookahead.py --cases 3 --node-cap 40000 --no-policy
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)


class Budget(Exception):
    """节点/时间超限：记为预算淘汰，不是错误。"""


# ---------------------------------------------------------------------------
class ExactEngine:
    """忠实状态转移 + 精确最优胜率。坐标 x∈[1,w] y∈[1,h]，位索引=y*W2+x。"""

    def __init__(self, case, node_cap=500000, time_cap=120.0):
        self.w = int(case["w"])
        self.h = int(case["h"])
        self.a = int(case["a"])
        self.W2 = self.w + 2
        self.node_cap = node_cap
        self.time_cap = time_cap
        self.nodes = 0
        self.t0 = time.perf_counter()

        cv = case["cell_value"]
        self.all_mask = 0
        self.opened0 = 0
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                b = 1 << (y * self.W2 + x)
                self.all_mask |= b
                if 0 <= int(cv[y][x]) <= 8:
                    self.opened0 |= b

        self.nbr = {}
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                m = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 1 <= nx <= self.w and 1 <= ny <= self.h:
                            m |= 1 << (ny * self.W2 + nx)
                self.nbr[y * self.W2 + x] = m

        from compare_2ply_vs_winrate import enumerate_worlds
        worlds, clicks, clicks9, _ = enumerate_worlds(case)
        self.world_bits = []
        for wset in worlds:
            m = 0
            for (x, y) in wset:
                m |= 1 << (y * self.W2 + x)
            self.world_bits.append(m)
        self.n_worlds = len(self.world_bits)
        self.cand_coords = [tuple(p) for p in (clicks + clicks9)]
        self._memo = {}
        self._pc = {}

    # ---- 基础件 ----
    def _coord(self, idx):
        return (idx % self.W2, idx // self.W2)

    def _idx_of(self, xy):
        return xy[1] * self.W2 + xy[0]

    def pop(self, m):
        r = self._pc.get(m)
        if r is None:
            r = bin(m).count("1")
            self._pc[m] = r
        return r

    def number(self, cell, mine_bits):
        return self.pop(mine_bits & self.nbr[cell])

    def won(self, opened):
        return self.pop(opened) == self.w * self.h - self.a

    def flood(self, mine_bits, opened, cell):
        bit = 1 << cell
        if opened & bit or mine_bits & bit:
            return opened
        stack = [cell]
        op = opened
        while stack:
            c = stack.pop()
            b = 1 << c
            if op & b or mine_bits & b:
                continue
            op |= b
            if self.number(c, mine_bits) == 0:
                m = self.nbr[c]
                while m:
                    low = m & -m
                    m &= m - 1
                    n = low.bit_length() - 1
                    if not (op & low) and not (mine_bits & low):
                        stack.append(n)
        return op

    def _obs(self, mine_bits, before, after):
        delta = after & ~before
        pairs = []
        m = delta
        while m:
            low = m & -m
            c = low.bit_length() - 1
            m &= m - 1
            pairs.append((c, self.number(c, mine_bits)))
        return (after, tuple(pairs))

    def _tick(self):
        self.nodes += 1
        if self.nodes > self.node_cap:
            raise Budget("node cap")
        if (self.nodes & 2047) == 0:
            if time.perf_counter() - self.t0 > self.time_cap:
                raise Budget("time cap")

    def _union_inter(self, wkey):
        union = 0
        inter = self.all_mask
        for i in wkey:
            wb = self.world_bits[i]
            union |= wb
            inter &= wb
        return union, inter

    # ---- 免费推理：完全饱和（对所有分叉递归）----
    def saturate(self, wkey, opened, weight=1.0):
        """把必然安全格全部开掉，返回终端态列表 [(wkey, opened, weight)]。

        ⚠️ 必须**递归饱和每个观测分支**，不能一遇分叉就返回：
        否则"先开哪个安全格"会改变结果（实测同一局面 1.0000 vs 0.8462）。
        """
        while True:
            self._tick()
            if self.won(opened):
                return [(wkey, opened, weight)]
            union, _ = self._union_inter(wkey)
            safe = self.all_mask & ~opened & ~union
            if not safe:
                return [(wkey, opened, weight)]
            low = safe & -safe                  # 取最低位，确定性
            cell = low.bit_length() - 1
            outs = {}
            for i in wkey:
                wb = self.world_bits[i]
                op = self.flood(wb, opened, cell)
                outs.setdefault(self._obs(wb, opened, op), []).append(i)
            if len(outs) == 1:
                (op2, _), subs = next(iter(outs.items()))
                opened, wkey = op2, frozenset(subs)
                continue
            n = len(wkey)
            res = []
            for k, v in outs.items():
                res.extend(self.saturate(frozenset(v), k[0],
                                         weight * len(v) / n))
            return res

    # ---- 点开 ----
    def click_outcomes(self, cell, wkey, opened):
        safe = [i for i in wkey if not (self.world_bits[i] >> cell & 1)]
        if not safe:
            return 0.0, []
        outs = {}
        for i in safe:
            wb = self.world_bits[i]
            op = self.flood(wb, opened | (1 << cell), cell)
            outs.setdefault(self._obs(wb, opened, op), []).append(i)
        ns = len(safe)
        return ns / len(wkey), [(frozenset(v), k[0], len(v) / ns)
                                for k, v in outs.items()]

    def candidates(self, wkey, opened):
        union, inter = self._union_inter(wkey)
        m = union & ~opened & ~inter
        out = []
        while m:
            low = m & -m
            out.append(low.bit_length() - 1)
            m &= m - 1
        return out

    def safety(self, cell, wkey):
        bad = sum(1 for i in wkey if self.world_bits[i] >> cell & 1)
        return 1.0 - bad / len(wkey)

    # ---- 精确最优 ----
    def V_exact(self, wkey, opened):
        key = (wkey, opened)
        r = self._memo.get(key)
        if r is not None:
            return r
        self._tick()
        terms = self.saturate(wkey, opened)
        val = 0.0
        for wk, op, wt in terms:
            if self.won(op):
                val += wt * 1.0
                continue
            cands = self.candidates(wk, op)
            if not cands:
                continue                     # 卡死：贡献 0
            best = max(self.after_click(c, wk, op) for c in cands)
            val += wt * best
        self._memo[key] = val
        return val

    def after_click(self, cell, wkey, opened):
        """点 cell 后用完美策略续玩能拿到的胜率（踩雷贡献 0）。"""
        p_safe, outs = self.click_outcomes(cell, wkey, opened)
        if p_safe <= 0.0:
            return 0.0
        val = 0.0
        for sub, op2, wt in outs:
            if self.won(op2):
                val += p_safe * wt
            else:
                val += p_safe * wt * self.V_exact(sub, op2)
        return val

    # ---- 前瞻打分（k-ply）----
    def ply_score(self, cell, wkey, opened, k, memo):
        """safety(c) × E[best_{k-1}]；k=1 时即 safety(c)。"""
        if k <= 1:
            return self.safety(cell, wkey)
        key = (cell, wkey, opened, k)
        r = memo.get(key)
        if r is not None:
            return r
        self._tick()
        p_safe, outs = self.click_outcomes(cell, wkey, opened)
        if p_safe <= 0.0:
            memo[key] = 0.0
            return 0.0
        exp = 0.0
        for sub, op2, wt in outs:
            exp += wt * (1.0 if self.won(op2) else self.best_ply(sub, op2, k - 1, memo))
        r = p_safe * exp
        memo[key] = r
        return r

    def best_ply(self, wkey, opened, k, memo):
        key = ("B", wkey, opened, k)
        r = memo.get(key)
        if r is not None:
            return r
        terms = self.saturate(wkey, opened)
        val = 0.0
        for wk, op, wt in terms:
            if self.won(op):
                val += wt * 1.0
                continue
            cands = self.candidates(wk, op)
            if not cands:
                continue
            val += wt * max(self.ply_score(c, wk, op, k, memo) for c in cands)
        memo[key] = val
        return val

    def expand_score(self, cell, wkey, opened):
        """期望展开格数（信息增益代理）。"""
        p_safe, outs = self.click_outcomes(cell, wkey, opened)
        if p_safe <= 0.0:
            return 0.0
        return p_safe * sum(wt * self.pop(op2 & ~opened) for sub, op2, wt in outs)

    # ---- 策略臂整局价值 ----
    def V_policy(self, wkey, opened, arm, ply, memo):
        key = (wkey, opened)
        r = memo.get(key)
        if r is not None:
            return r
        self._tick()
        terms = self.saturate(wkey, opened)
        val = 0.0
        for wk, op, wt in terms:
            if self.won(op):
                val += wt * 1.0
                continue
            cands = self.candidates(wk, op)
            if not cands:
                continue
            c = self.pick(cands, wk, op, arm, ply)
            val += wt * self.after_click(c, wk, op)
        memo[key] = val
        return val

    def pick(self, cands, wkey, opened, arm, ply):
        if arm == "safety":
            return max(cands, key=lambda c: (self.safety(c, wkey), -c))
        if arm == "expand":
            return max(cands, key=lambda c: (self.expand_score(c, wkey, opened), -c))
        if arm == "ply":
            memo = {}
            return max(cands, key=lambda c: (self.ply_score(c, wkey, opened, ply,
                                                           memo), -c))
        raise ValueError(f"未知臂 {arm}")


# ---------------------------------------------------------------------------
ARMS = [("safety", 0, "纯 safety"),
        ("expand", 0, "期望展开"),
        ("ply", 2, "2-ply 前瞻"),
        ("ply", 3, "3-ply 前瞻")]
ARM_NAMES = [a[2] for a in ARMS]
PROD = "生产 win_rate"


def _arm_picks(eng, cands, wk, op):
    return {
        "纯 safety": eng.pick(cands, wk, op, "safety", 0),
        "期望展开": eng.pick(cands, wk, op, "expand", 0),
        "2-ply 前瞻": eng.pick(cands, wk, op, "ply", 2),
        "3-ply 前瞻": eng.pick(cands, wk, op, "ply", 3),
    }


def evaluate_case(ci, case, gold, eng, do_policy):
    wk0 = frozenset(range(eng.n_worlds))
    op0 = eng.opened0
    row = {"idx": ci, "F": int(case["F"]), "U": int(case["U"]),
           "worlds": eng.n_worlds}

    v_opt = eng.V_exact(wk0, op0)
    row["V_exact"] = round(v_opt, 6)

    # ---- A) 首决策：在饱和后的每个终端决策态上比较各臂 ----
    terms = eng.saturate(wk0, op0)
    row["n_terms"] = len(terms)
    first = {n: {"regret": 0.0, "is_optimal": True, "cases": 0}
             for n in ARM_NAMES + [PROD]}
    for wk, op, wt in terms:
        if eng.won(op):
            continue
        cands = eng.candidates(wk, op)
        if not cands:
            continue
        v_dec = max(eng.after_click(c, wk, op) for c in cands)
        picks = _arm_picks(eng, cands, wk, op)
        for n, c in picks.items():
            q = eng.after_click(c, wk, op)
            e = first[n]
            e["regret"] += wt * (v_dec - q)
            e["is_optimal"] = e["is_optimal"] and q >= v_dec - 1e-12
            e["cases"] += 1
        # 安全格判定
        safes = [c for c in cands if eng.safety(c, wk) >= 1.0 - 1e-12]
        if safes:
            row.setdefault("safe_probe", []).append({
                "n_safe": len(safes),
                "worst_safe_quality": round(min(eng.after_click(c, wk, op)
                                                for c in safes), 6),
                "v_dec": round(v_dec, 6),
                "all_safe_optimal": bool(
                    min(eng.after_click(c, wk, op) for c in safes)
                    >= v_dec - 1e-12),
            })
        # 生产 win_rate 首选（只用已存盘 gold，不重算）
        if gold is not None:
            res = [float(x) for x in gold["res"]]
            clk = [tuple(p) for p in gold["clicks"]]
            mp = {eng._idx_of(xy): res[k] for k, xy in enumerate(clk)
                  if k < len(res)}
            if mp:
                pw = max(mp.items(), key=lambda kv: kv[1])
                pc = pw[0]
                if any(c == pc for c in cands):
                    q = eng.after_click(pc, wk, op)
                    e = first[PROD]
                    e["regret"] += wt * (v_dec - q)
                    e["is_optimal"] = e["is_optimal"] and q >= v_dec - 1e-12
                    e["cases"] += 1
                    if e.get("reported") is None:
                        e["reported"] = round(pw[1], 4)
                        e["cell"] = list(eng._coord(pc))
                        e["quality"] = round(q, 6)
                        e["safety"] = round(eng.safety(pc, wk), 4)

    for n, e in first.items():
        e["regret"] = round(e["regret"], 6)
        if e["cases"] == 0:
            e["note"] = "该臂在饱和态无有效决策点"
    row["first"] = first

    # ---- B) 整局策略价值 ----
    if do_policy:
        row["V_policy"] = {}
        for arm, ply, label in ARMS:
            try:
                row["V_policy"][label] = round(
                    eng.V_policy(wk0, op0, arm, ply, {}), 6)
            except Budget as e:
                row["V_policy"][label] = f"budget:{e}"
    row["nodes"] = eng.nodes
    row["sec"] = round(time.perf_counter() - eng.t0, 2)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=11)
    ap.add_argument("--node-cap", type=int, default=500000)
    ap.add_argument("--time-cap", type=float, default=120.0)
    ap.add_argument("--no-policy", action="store_true")
    ap.add_argument("--out", default=os.path.join(ROOT, "bench", "data",
                                                  "arms_result.json"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "bench", "data", "wr_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    with open(os.path.join(ROOT, "bench", "data", "wr_before.json"),
              encoding="utf-8") as f:
        gold = json.load(f)["results"]

    print("=" * 106)
    print("选格策略对比（真值 = 精确 oracle；各臂同一决策状态、同一后验）")
    print("=" * 106)
    rows = []
    for ci in range(min(args.cases, len(cases))):
        g = gold[ci] if ci < len(gold) else None
        print(f"\n[{ci:2d}] F={cases[ci]['F']} U={cases[ci]['U']} 求值中 …", flush=True)
        try:
            eng = ExactEngine(cases[ci], args.node_cap, args.time_cap)
            if eng.n_worlds == 0:
                print("     无一致世界，跳过")
                continue
            row = evaluate_case(ci, cases[ci], g, eng, not args.no_policy)
        except Budget as e:
            rows.append({"idx": ci, "budget": str(e)})
            print(f"     ⚠️ 预算淘汰（{e}）——不计入结论")
            continue
        except Exception as e:                          # noqa: BLE001
            rows.append({"idx": ci, "error": f"{type(e).__name__}: {e}"})
            print(f"     ⚠️ 错误 {type(e).__name__}: {e}")
            continue
        rows.append(row)
        print(f"     worlds={row['worlds']} 饱和终端态={row['n_terms']} "
              f"节点={row['nodes']} 耗时={row['sec']}s")
        print(f"     ★ 精确最优 V* = {row['V_exact']:.4f}")
        for n, e in row["first"].items():
            if e["cases"] == 0:
                print(f"       {n:>12}: 无有效决策点")
                continue
            mark = "✓" if e["is_optimal"] else f"✗ regret={e['regret']:.4f}"
            extra = ""
            if e.get("cell"):
                extra = (f" 选{tuple(e['cell'])} safety={e['safety']:.4f} "
                         f"质量={e['quality']:.4f} wr_f报={e['reported']}")
            print(f"       {n:>12}: {mark}{extra}")
        sp = row.get("safe_probe") or []
        if sp:
            n_ok = sum(1 for s in sp if s["all_safe_optimal"])
            print(f"     安全格判定：{n_ok}/{len(sp)} 个决策点的安全格全部达到最优")
            for s in sp:
                if not s["all_safe_optimal"]:
                    print(f"       ✗ 安全格最低质量 {s['worst_safe_quality']} "
                          f"< V*{s['v_dec']}")
        if row.get("V_policy"):
            print("     整局: " + "  ".join(f"{k}={v}"
                                          for k, v in row["V_policy"].items()))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "node_cap": args.node_cap,
                   "time_cap": args.time_cap}, f, ensure_ascii=False, indent=1)
    print(f"\n结果已写 {args.out}")
    summarize(rows)


def summarize(rows):
    ok = [r for r in rows if "V_exact" in r and r.get("first")]
    if not ok:
        print("\n无有效用例，不出汇总")
        return

    names = ARM_NAMES + [PROD]
    print("\n" + "=" * 106)
    print("汇总 A：首决策 regret（相对同状态精确最优；0 = 选对了）")
    print("=" * 106)
    print(f"{'idx':>3} {'V*':>8} | " + " | ".join(f"{n:>14}" for n in names))
    print("-" * 106)
    agg = {n: [] for n in names}
    for r in ok:
        line = f"{r['idx']:>3} {r['V_exact']:>8.4f} | "
        for n in names:
            e = r["first"].get(n)
            if e is None or e["cases"] == 0:
                line += f"{'-':>14} | "
                continue
            agg[n].append(e["regret"])
            txt = "0.0000" if e["is_optimal"] else f"{e['regret']:.4f}✗"
            line += f"{txt:>14} | "
        print(line)
    print("-" * 106)
    line = f"{'平均regret':>9} {'':>2} | "
    for n in names:
        v = agg[n]
        line += f"{(sum(v)/len(v) if v else float('nan')):>14.4f} | "
    print(line)
    line = f"{'全对用例':>9} {'':>2} | "
    for n in names:
        k = sum(1 for r in ok if r["first"].get(n, {}).get("is_optimal"))
        line += f"{f'{k}/{len(ok)}':>14} | "
    print(line)
    print("\n0.0000 = 该臂每一步都选到精确最优；数字 = 平均 regret（越小越好）")

    print("\n" + "=" * 106)
    print("汇总 B：判定题 —— safety=1.0 的格是否达到最优质量？")
    print("=" * 106)
    probes = [(r["idx"], s) for r in ok for s in (r.get("safe_probe") or [])]
    print(f"含 safety=1.0 候选格的决策点：{len(probes)} 个"
          f"（分布在 {len(set(p[0] for p in probes))} 个用例）")
    if probes:
        good = sum(1 for _, s in probes if s["all_safe_optimal"])
        print(f"其中『安全格全部达到最优』：{good}/{len(probes)}")
        for idx, s in probes:
            st = "✓ 全部最优" if s["all_safe_optimal"] else \
                 f"✗ 最低 {s['worst_safe_quality']} < {s['v_dec']}"
            print(f"   [{idx:2d}] {s['n_safe']} 个安全格 → {st}")

    pol = [r for r in ok if isinstance(r.get("V_policy"), dict)]
    if pol:
        print("\n" + "=" * 106)
        print("汇总 C：整局策略价值（各臂从根节点完整走完的精确胜率）")
        print("=" * 106)
        cols = list(pol[0]["V_policy"].keys())
        print(f"{'idx':>3} {'V*':>8} | " + " | ".join(f"{n:>14}" for n in cols))
        print("-" * 106)
        agg2 = {n: [] for n in cols}
        for r in pol:
            line = f"{r['idx']:>3} {r['V_exact']:>8.4f} | "
            for n in cols:
                v = r["V_policy"].get(n)
                if isinstance(v, float):
                    agg2[n].append(v)
                    txt = f"{v:.4f}" + ("" if round(v, 4) >= round(r["V_exact"], 4)
                                        else "✗")
                else:
                    txt = str(v)[:14]
                line += f"{txt:>14} | "
            print(line)
        print("-" * 106)
        line = f"{'均值':>3} {sum(r['V_exact'] for r in pol)/len(pol):>8.4f} | "
        for n in cols:
            v = agg2[n]
            line += f"{(sum(v)/len(v) if v else float('nan')):>14.4f} | "
        print(line)


if __name__ == "__main__":
    main()
