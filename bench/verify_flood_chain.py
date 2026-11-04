# -*- coding: utf-8 -*-
"""仪器核查：ExactEngine.flood 在 click_outcomes 里是否真的发生了 0 连锁展开？

=================== 疑点（读源码得出的，必须先证伪）====================
arms_lookahead.py 有两处调用 flood：

  saturate()        : op = self.flood(wb, opened, cell)            ← 不传 bit
  click_outcomes()  : op = self.flood(wb, opened | (1 << cell), cell)  ← 传了 bit

而 flood 的**第一行**是：
    if opened & bit or mine_bits & bit:  return opened
⇒ 若 opened 已经包含 bit，flood 立刻返回，**一格都不展开**。

如果这是真的，那么：
  · 免费推理饱和（saturate）是真 flood-fill ✔
  · 而"点开某个候选格"（click_outcomes → after_click → V*）**只开 1 格**，
    没有 0 连锁展开 ✘
⇒ 所有 after_click / V_expect / regret 都会系统性低估"点 0 格连锁开一片"
   的价值 ⇒ 本轮之前的 regret 读数以及上一轮 33 用例结论都需重算。

=================== 判定方式（构造已知答案的用例）====================
造一个**没有已有已开格**（opened0=0）、候选格 C 在布局下数字为 0 的盘面，
其余格全为雷的邻居关系可控。若 flood 正确，点 C 后应开出一大片（>1）；
若返回只多 1 格，则缺陷成立。

同时也用 **GenericFlood（独立实现，照真实扫雷规则重写）** 对拍，
避免"用同一个函数证明它自己"。

用法: python bench/verify_flood_chain.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
class IndependentBoard:
    """完全独立重写的盘面 flood-fill（不引用 arms_lookahead 的位运算实现）。

    语义：点开 cell；若其数字为 0，递归开 8 邻域（未开且非雷）；否则只开自己。
    坐标用 (x, y)，与 ExactEngine 的位索引分离，避免共享错误。
    """

    def __init__(self, w, h, mine_set):
        self.w, self.h = w, h
        self.mines = set(mine_set)

    def nbrs(self, x, y):
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= self.w and 1 <= ny <= self.h:
                    out.append((nx, ny))
        return out

    def number(self, x, y):
        return sum(1 for (nx, ny) in self.nbrs(x, y) if (nx, ny) in self.mines)

    def click(self, x, y, opened):
        """返回点开后的 opened 集合（含零连锁）。"""
        if (x, y) in opened or (x, y) in self.mines:
            return set(opened)
        op = set(opened)
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in op or (cx, cy) in self.mines:
                continue
            op.add((cx, cy))
            if self.number(cx, cy) == 0:
                for nb in self.nbrs(cx, cy):
                    if nb not in op and nb not in self.mines:
                        stack.append(nb)
        return op


def build_engine_degenerate_case():
    """构造"点一个 0 格会连锁开一片"的最小盘面，喂给 ExactEngine。

    盘面 6x6、2 雷、全部格未开（opened0 = 0）：

        y=1:  . . . . . .
        ...
        y=3:  . . 0 0 . .      ← (3,3)/(4,3) 无邻雷
        y=4:  . . 0 0 . .

    雷放在 (1,1) 与 (6,6)。
    点 (3,3)：其 8 邻域无雷 ⇒ 数字 0 ⇒ 应连锁开到 (2..5, 2..4) 共 9 格。
    """
    w = h = 6
    mines = {(1, 1), (6, 6)}
    case = {
        "w": w, "h": h, "a": 2, "num10": 0,
        "F": 1, "U": w * h - 1, "total": 1,
        "clicks": [[3, 3]],
        "clicks9": [[x, y] for y in range(1, h + 1) for x in range(1, w + 1)
                    if (x, y) != (3, 3)],
        "ck": [1],
        "groups": [[[0]]],
        "cell_value": [[0] * (w + 2) for _ in range(h + 2)],
        "is_play": True,
    }
    # cell_value：1-based 内部全 9（未开），外框 0
    import numpy as np
    cv = np.zeros((h + 2, w + 2), dtype=int)
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            cv[y, x] = 9
    case["cell_value"] = cv.tolist()
    return case, mines


def main():
    import numpy as np
    import arms_lookahead as al

    case, mines = build_engine_degenerate_case()
    w, h = case["w"], case["h"]
    indep = IndependentBoard(w, h, mines)

    # 独立实现的"点 (3,3) 会开出多少格"
    base_opened = set()            # 全盘未开 ⇒ 没有已开格
    got = indep.click(3, 3, base_opened)
    new_cells_indep = len(got - base_opened)
    print("=" * 96)
    print("构造用例：6x6、2 雷（(1,1) 与 (6,6)）、全盘未开")
    print(f"  独立实现（照真实规则重写）：点 (3,3) 新开 {new_cells_indep} 格"
          f" → {sorted(got)}")
    assert new_cells_indep > 1, "构造无效：独立实现也没连锁"

    # ---- 用 ExactEngine 的 flood 三种调用形态 ----
    eng = al.ExactEngine(case, node_cap=200000, time_cap=30, world_cap=None)
    # 该盘面世界数：2 雷、1 前沿格(3,3)（它不是雷则剩 2 雷进 35 格）
    # ⇒ 世界数 = C(35,2) = 595（≤ cap）
    cell = eng._idx_of((3, 3))
    wb = eng.world_bits[0]
    opened0 = eng.opened0
    print(f"  ExactEngine: n_worlds={eng.n_worlds} opened0 格数={eng.pop(opened0)}")

    opA = eng.flood(wb, opened0, cell)                    # saturate 的调用形态
    opB = eng.flood(wb, opened0 | (1 << cell), cell)      # click_outcomes 的形态
    newA = eng.pop(opA & ~opened0)
    newB = eng.pop(opB & ~opened0)
    print(f"  flood(opened)            → 新开 {newA} 格   "
          f"{'✔ 与独立实现一致' if newA == new_cells_indep else '✘ 不一致'}")
    print(f"  flood(opened | bit)      → 新开 {newB} 格   "
          f"{'✔ 与独立实现一致' if newB == new_cells_indep else '✘ 不一致'}")

    # ---- click_outcomes 实际返回的观测 ----
    p_safe, outs = eng.click_outcomes(cell, al.WKey.all(eng.n_worlds), opened0)
    new_in_obs = max(eng.pop(op & ~opened0) for _sub, op, _wt in outs)
    print(f"  click_outcomes() 的观测   → 最多新开 {new_in_obs} 格   "
          f"{'✔' if new_in_obs == new_cells_indep else '✘ 缺陷成立'}")

    print("\n" + "=" * 96)
    print("判定")
    print("=" * 96)
    # 判据：`click_outcomes`（真正驱动 after_click/V_exact 的那条路径）
    # 的观测必须与独立实现的连锁格数一致。
    if new_in_obs == new_cells_indep:
        print("  ✔ 无缺陷：`click_outcomes` 已正确展开 0 连锁（与独立实现一致）。")
        print("  （若 flood 的两种调用形态仍有差异，那只是 saturate 路径的")
        print("    内部细节；判定以 click_outcomes 的实际观测为准。）")
    else:
        print("  ✘ 缺陷成立：`click_outcomes` 的连锁展开与独立实现不一致 ⇒")
        print("    `after_click`/`V_exact` 读数系统性偏移，相关结论须重算。")

    # ---- 阳性对照：确认本脚本能抓到"故意改坏的 flood" ----
    print("\n" + "=" * 96)
    print("阳性对照（注入必红）：把 flood 改成永不连锁，本脚本必须报缺陷")
    print("=" * 96)
    orig_flood = al.ExactEngine.flood

    def broken_flood(self, mine_bits, opened, cell):
        bit = 1 << cell
        if opened & bit or mine_bits & bit:
            return opened
        return opened | bit              # 只开自己，永不连锁

    al.ExactEngine.flood = broken_flood
    try:
        eng2 = al.ExactEngine(case, node_cap=200000, time_cap=30, world_cap=None)
        cell2 = eng2._idx_of((3, 3))
        wb2 = eng2.world_bits[0]
        op_new = eng2.flood(wb2, eng2.opened0, cell2)
        n_new = eng2.pop(op_new & ~eng2.opened0)
        detected = n_new < new_cells_indep
        tag = "✔ 被抓出（闸门有效）" if detected else "✘ 没抓出（闸门失效）"
        print(f"  注入不连锁后 flood 新开 {n_new} 格 ⇒ {tag}")
    finally:
        al.ExactEngine.flood = orig_flood


if __name__ == "__main__":
    main()
