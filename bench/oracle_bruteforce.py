# -*- coding: utf-8 -*-
"""真·独立穷举 oracle：**完全不共享** enumerate_worlds / emit / 分组表。

=================== 为什么必须再写一个 ====================
bench/arms_lookahead.py 的 ExactEngine 与 bench/verify_exact_oracle.py 的
Indep 共同依赖 bench/compare_2ply_vs_winrate.py 的 enumerate_worlds。
那个函数的列号错位 bug（fm.append(clicks[ci])）让两个实现**一起错**，
因此"两实现读数一致"根本不构成独立验证（本轮实证：两者都给出偏移的 V*）。

本文件的独立性来自：
  · 世界枚举：只从 cell_value 的已开数字格读约束，枚举"未开格里哪些是雷"，
    完全不碰 clicks/clicks9/groups/ck 这套分组表。
  · 状态转移：只用"已开格集合 + 雷集合"两个 frozenset，无位运算、无 memo 共享。
  · 判胜：已开格数 == w*h - a。
这样它与 ExactEngine 的唯一共同点只剩"扫雷规则本身"。

=================== 规模控制 ====================
真实 30x16/99 雷局面未开格可能很多（本用例集 U+F 最多 18），C(18,8) 量级
可接受。若某用例未开格过多，按 node_cap 记为预算淘汰（不崩溃）。

用法: python bench/oracle_bruteforce.py
"""
import json
import math
import os
import sys
import time
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

UNOPENED, MINE = 9, 10


class Budget(Exception):
    pass


def neighbors(cell):
    x, y = cell
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if not (dx == 0 and dy == 0)]


class BruteOracle:
    """从 cell_value 独立重建全部一致世界，再走忠实状态转移。"""

    def __init__(self, case, node_cap=200000, time_cap=120.0):
        self.w = int(case["w"])
        self.h = int(case["h"])
        self.a = int(case["a"])
        self.node_cap = node_cap
        self.time_cap = time_cap
        self.nodes = 0
        self.t0 = time.perf_counter()

        cv = case["cell_value"]
        # 只读 cell_value：9=未开, 10=已标雷, 0..8=已开数字
        self.unknown = [(x, y) for y in range(1, self.h + 1)
                        for x in range(1, self.w + 1) if cv[y][x] == UNOPENED]
        self.flagged = {(x, y) for y in range(1, self.h + 1)
                        for x in range(1, self.w + 1) if cv[y][x] == MINE}
        self.opened0 = frozenset((x, y) for y in range(1, self.h + 1)
                                 for x in range(1, self.w + 1)
                                 if 0 <= cv[y][x] <= 8)
        # 数字约束：对每个已开数字格 v，其未开邻格中恰有 v - (已标雷邻格数) 颗雷
        # 已标雷本身确定是雷，直接并入 base，并从未知格里排除。
        constraints = []
        for (x, y) in self.opened0:
            v = int(cv[y][x])
            nbrs = [(m, n) for (m, n) in neighbors((x, y))
                    if 1 <= m <= self.w and 1 <= n <= self.h]
            fixed = sum(1 for p in nbrs if p in self.flagged)
            free = [p for p in nbrs if p in set(self.unknown)]
            need = v - fixed
            if need < 0:
                raise AssertionError(f"已开格 {(x, y)} 数字 {v} 与已标雷矛盾")
            if free:
                constraints.append((frozenset(free), need))

        self.unk_index = {p: i for i, p in enumerate(self.unknown)}
        self.constraints = [(frozenset(self.unk_index[p] for p in s), k)
                            for s, k in constraints]

        # 剩余雷数（扣掉已标雷）
        need_total = self.a - len(self.flagged)
        if need_total < 0 or need_total > len(self.unknown):
            raise AssertionError("剩余雷数超出未开格数")

        # 穷举：从 unknown 里选 need_total 个作为雷
        n_unk = len(self.unknown)
        n_comb = math.comb(n_unk, need_total) if n_unk >= need_total else 0
        if n_comb > 3_000_000:
            raise Budget(f"组合数 {n_comb} 过大")

        self.worlds = []
        for combo in combinations(range(n_unk), need_total):
            mine_idx = set(combo)
            ok = True
            for s, k in self.constraints:
                if len(s & mine_idx) != k:
                    ok = False
                    break
            if ok:
                self.worlds.append(frozenset(
                    [self.unknown[i] for i in mine_idx] + list(self.flagged)))
        self.n_worlds = len(self.worlds)
        self.cand_coords = list(self.unknown)
        self.memo = {}
        # 前沿掩码（供 safety 快速判断），此处不需要

    def _tick(self):
        self.nodes += 1
        if self.nodes > self.node_cap:
            raise Budget("node cap")
        if (self.nodes & 255) == 0 and time.perf_counter() - self.t0 > self.time_cap:
            raise Budget("time cap")

    def number(self, cell, mines):
        return sum(1 for p in neighbors(cell) if p in mines)

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
                for n in neighbors(c):
                    if not (1 <= n[0] <= self.w and 1 <= n[1] <= self.h):
                        continue
                    if n not in op and n not in mines:
                        stack.append(n)
        return frozenset(op)

    def obs(self, mines, before, after):
        delta = after - before
        return (after, tuple(sorted((c, self.number(c, mines)) for c in delta)))

    def won(self, opened):
        return len(opened) == self.w * self.h - self.a

    def saturate(self, wk, opened, weight=1.0):
        while True:
            self._tick()
            if self.won(opened):
                return [(wk, opened, weight)]
            union = frozenset().union(*[self.worlds[i] for i in wk])
            cells = [(x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)]
            safe = [p for p in cells if p not in opened and p not in union]
            if not safe:
                return [(wk, opened, weight)]
            cell = min(safe)
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
        cells = [(x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)]
        union = frozenset().union(*[self.worlds[i] for i in wk])
        inter = frozenset.intersection(*[self.worlds[i] for i in wk])
        return [p for p in cells if p not in opened and p in union and p not in inter]

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

    from arms_lookahead import ExactEngine
    from verify_exact_oracle import Indep

    print("=" * 100)
    print("三实现交叉验证：真独立穷举 oracle vs ExactEngine vs Indep(frozenset)")
    print("独立性说明：本 oracle 只从 cell_value 重枚举世界，不碰 clicks/clicks9/groups")
    print("=" * 100)

    fail_eng, fail_ind = 0, 0
    theo_tests, theo_viol = 0, 0
    for ci, case in enumerate(cases):
        try:
            o = BruteOracle(case, node_cap=300000, time_cap=120)
            e = ExactEngine(case, node_cap=300000, time_cap=120)
            i1 = Indep(case, node_cap=300000, time_cap=120)
            wk_o = frozenset(range(o.n_worlds))
            v_o = o.V(wk_o, o.opened0)
            v_e = e.V_exact(frozenset(range(e.n_worlds)), e.opened0)
            v_i = i1.V(frozenset(range(len(i1.worlds))), i1.opened0)
            ok_e = abs(v_o - v_e) < 1e-9
            ok_i = abs(v_o - v_i) < 1e-9
            if not ok_e:
                fail_eng += 1
            if not ok_i:
                fail_ind += 1
            print(f"[{ci:2d}] 独立worlds={o.n_worlds:3d} | "
                  f"oracle={v_o:.6f} 引擎={v_e:.6f} "
                  f"{'OK' if ok_e else 'XX'} | indep={v_i:.6f} "
                  f"{'OK' if ok_i else 'XX'}")

            # 安全格定理（用真独立 oracle 检验）
            for c in o.cand_coords:
                if o.safety(c, wk_o) < 1.0 - 1e-12:
                    continue
                theo_tests += 1
                aft = o.after(c, wk_o, o.opened0)
                if aft < v_o - 1e-9:
                    theo_viol += 1
                    print(f"      定理违反：安全格 {c} → {aft:.6f} < V*={v_o:.6f}")
        except Budget as e:
            print(f"[{ci:2d}] 预算淘汰：{e}")
        except AssertionError as e:
            print(f"[{ci:2d}] 约束不一致：{e}")
        except Exception as e:                              # noqa: BLE001
            print(f"[{ci:2d}] 错误 {type(e).__name__}: {e}")

    print("=" * 100)
    print(f"与 ExactEngine 不一致用例 = {fail_eng}")
    print(f"与 Indep 不一致用例 = {fail_ind}")
    print(f"安全格定理：样本 {theo_tests}，违反 {theo_viol}")


if __name__ == "__main__":
    main()
