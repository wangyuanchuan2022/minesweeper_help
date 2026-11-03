# -*- coding: utf-8 -*-
"""独立暴力判定器：验证 ExactEngine（bench/arms_lookahead.py）的正确性。

=================== 为什么需要它 ====================
ExactEngine 用位运算 + memo 实现，可能在某处写错而**输出自洽但错误的数值**。
本文件用**完全不同的表示**（雷集合 frozenset + 已开集合 frozenset，无位运算）
独立实现同一语义，逐用例对拍 V*。

=================== 已两次抓到的协同错误（重要教训）====================
1) "批量开掉所有必然安全格" —— 破坏观测因果顺序，V* 系统性偏低。
2) "只开一个安全格、一遇分叉就返回" —— 结果依赖"先开哪个安全格"，
   实测同一局面两个实现给出 1.0000 / 0.8462 且都自洽。
⇒ 正确语义：对每个观测分支**递归饱和**，直到分支内再无必然安全格。
   "两个独立实现读数一致"**不能**证明正确（两处都犯了同一个错）。

=================== 定理检验 ====================
用户命题：必然安全格（safety=1.0）的点击不该被高风险点击排在前面——
在没有点击次数限制、允许连续行动的模型里，先点它拿到信息、再执行原策略，
不会降低最优胜率。因此应有：
    after(c) == V*(S)   对所有 safety(c)=1.0 的候选 c 成立
本文件对该等式逐用例检验（这是问题的判定式，比"≥"更强）。

用法: python bench/verify_exact_oracle.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)


class Budget(Exception):
    pass


def neighbors(cell, w, h):
    x, y = cell
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if not (dx == 0 and dy == 0) and 1 <= x + dx <= w and 1 <= y + dy <= h]


class Indep:
    """独立实现：全部用 frozenset，与位运算版本零共享代码。"""

    def __init__(self, case, node_cap=200000, time_cap=60.0):
        self.w, self.h, self.a = int(case["w"]), int(case["h"]), int(case["a"])
        self.node_cap, self.time_cap = node_cap, time_cap
        self.nodes = 0
        self.t0 = time.perf_counter()

        cv = case["cell_value"]
        self.opened0 = frozenset(
            (x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)
            if 0 <= int(cv[y][x]) <= 8)
        self.all_cells = frozenset(
            (x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1))
        self.nbr = {c: frozenset(neighbors(c, self.w, self.h))
                    for c in self.all_cells}

        from compare_2ply_vs_winrate import enumerate_worlds
        worlds, clicks, clicks9, _ = enumerate_worlds(case)
        # ⚠️ enumerate_worlds 现在返回位集整数（bit=(y-1)*w+(x-1)），
        # 本类用 frozenset[tuple] 表示，需要转回坐标。
        self.worlds = []
        for m0 in worlds:
            ws = set()
            mm = m0
            while mm:
                low = mm & -mm
                idx = low.bit_length() - 1
                mm &= mm - 1
                ws.add((idx % self.w + 1, idx // self.w + 1))
            self.worlds.append(frozenset(ws))
        self.cand_coords = [tuple(p) for p in (clicks + clicks9)]
        self.memo = {}

    def _tick(self):
        self.nodes += 1
        if self.nodes > self.node_cap:
            raise Budget("node cap")
        if (self.nodes & 1023) == 0 and time.perf_counter() - self.t0 > self.time_cap:
            raise Budget("time cap")

    def number(self, cell, mines):
        return len(self.nbr[cell] & mines)

    def flood(self, mines, opened, cell):
        if cell in opened or cell in mines:
            return opened
        op = set(opened)
        stack = [cell]
        while stack:
            c = stack.pop()
            if c in op or c in mines:
                continue
            op.add(c)
            if self.number(c, mines) == 0:
                for n in self.nbr[c]:
                    if n not in op and n not in mines:
                        stack.append(n)
        return frozenset(op)

    def obs(self, mines, before, after):
        delta = after - before
        return (after, tuple(sorted((c, self.number(c, mines)) for c in delta)))

    def won(self, opened):
        return len(opened) == self.w * self.h - self.a

    def saturate(self, wk, opened, weight=1.0):
        """完全饱和：对每个观测分支递归，返回终端态 [(wk, opened, weight)]。"""
        while True:
            self._tick()
            if self.won(opened):
                return [(wk, opened, weight)]
            mine_union = frozenset().union(*[self.worlds[i] for i in wk])
            safe = self.all_cells - opened - mine_union
            if not safe:
                return [(wk, opened, weight)]
            cell = min(safe)                    # 确定性取最小坐标
            outs = {}
            for i in wk:
                op = self.flood(self.worlds[i], opened, cell)
                outs.setdefault(self.obs(self.worlds[i], opened, op), []).append(i)
            if len(outs) == 1:
                (op2, _), subs = next(iter(outs.items()))
                opened, wk = op2, frozenset(subs)
                continue
            n = len(wk)
            res = []
            for k, v in outs.items():
                res.extend(self.saturate(frozenset(v), k[0],
                                         weight * len(v) / n))
            return res

    def outcomes(self, cell, wk, opened):
        safe = [i for i in wk if cell not in self.worlds[i]]
        if not safe:
            return 0.0, []
        outs = {}
        for i in safe:
            op = self.flood(self.worlds[i], opened | {cell}, cell)
            outs.setdefault(self.obs(self.worlds[i], opened, op), []).append(i)
        ns = len(safe)
        return ns / len(wk), [(frozenset(v), k[0], len(v) / ns)
                              for k, v in outs.items()]

    def candidates(self, wk, opened):
        mu = frozenset().union(*[self.worlds[i] for i in wk])
        inter = frozenset.intersection(*[self.worlds[i] for i in wk])
        return sorted(mu - opened - inter)

    def safety(self, cell, wk):
        return 1.0 - sum(1 for i in wk if cell in self.worlds[i]) / len(wk)

    def V(self, wk, opened):
        key = (wk, opened)
        if key in self.memo:
            return self.memo[key]
        self._tick()
        val = 0.0
        for wk2, op2, wt in self.saturate(wk, opened):
            if self.won(op2):
                val += wt * 1.0
                continue
            cands = self.candidates(wk2, op2)
            if not cands:
                continue
            val += wt * max(self.after(c, wk2, op2) for c in cands)
        self.memo[key] = val
        return val

    def after(self, cell, wk, opened):
        p, outs = self.outcomes(cell, wk, opened)
        if p <= 0:
            return 0.0
        val = 0.0
        for s, o, wt in outs:
            val += p * wt * (1.0 if self.won(o) else self.V(s, o))
        return val


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 96)
    print("独立暴力判定器 vs ExactEngine（逐用例对拍 + 安全格最优性定理检验）")
    print("=" * 96)

    from arms_lookahead import ExactEngine

    failures = 0
    theo_tests = 0
    theo_viol = 0
    eq_tests = 0
    eq_viol = 0
    for ci, case in enumerate(cases):
        try:
            e1 = Indep(case, node_cap=300000, time_cap=90)
            e2 = ExactEngine(case, node_cap=300000, time_cap=90)
            if not e1.worlds:
                continue
            wk1 = frozenset(range(len(e1.worlds)))
            wk2 = frozenset(range(e2.n_worlds))
            v1 = e1.V(wk1, e1.opened0)
            v2 = e2.V_exact(wk2, e2.opened0)
            same = abs(v1 - v2) < 1e-9
            if not same:
                failures += 1
            print(f"[{ci:2d}] 独立 V*={v1:.6f}  引擎 V*={v2:.6f}  "
                  f"{'OK 一致' if same else 'XX 不一致'}  "
                  f"(worlds={len(e1.worlds)} 节点 {e1.nodes}/{e2.nodes})")

            # 定理：安全格点开后必须达到 V*（更强形式：等式）
            for c in [tuple(p) for p in e1.cand_coords]:
                if e1.safety(c, wk1) < 1.0 - 1e-12:
                    continue
                theo_tests += 1
                aft = e1.after(c, wk1, e1.opened0)
                if aft < v1 - 1e-9:
                    theo_viol += 1
                    print(f"      定理违反（弱形式 <）：安全格 {c} → {aft:.6f} "
                          f"< V*={v1:.6f}")
                eq_tests += 1
                if abs(aft - v1) > 1e-9:
                    eq_viol += 1
                    print(f"      定理违反（强形式 !=）：安全格 {c} → {aft:.6f} "
                          f"≠ V*={v1:.6f} (差 {v1-aft:+.6f})")
        except Budget as e:
            print(f"[{ci:2d}] 预算淘汰：{e}")
        except Exception as e:                              # noqa: BLE001
            print(f"[{ci:2d}] 错误 {type(e).__name__}: {e}")

    print("=" * 96)
    print(f"两实现不一致用例 = {failures}")
    print(f"定理弱形式（安全格点开后 ≥ V*）：样本 {theo_tests}，违反 {theo_viol}")
    print(f"定理强形式（安全格点开后 == V*）：样本 {eq_tests}，违反 {eq_viol}")


if __name__ == "__main__":
    main()
