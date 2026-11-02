# -*- coding: utf-8 -*-
"""把项目求解器封装成"只接受人类可见视图"的决策器。

信息隔离在这里落地：
  · decide(view) 的入参是 BoardView（只含已开数字 + 尺寸 + 总雷数）
  · 内部把这些转成项目算法熟悉的 cell_value 矩阵再调用 number5_1
  · **雷位从不出现在这条链路上**（BoardView 里没有雷位，物理上无法泄漏）

适配要点：
  · 项目 cell_value 编码：9=未开、0..8=已开数字、10=推理标记雷
  · number5_1 会自己重扫棋盘（complete_scan）——模拟环境没有屏幕，
    这里把它替换成恒等函数，让它直接使用我们传入的矩阵
  · number5_1 决定后返回新的 cell_value，我们需要从 _last_heatmap.best
    取它推荐的点击格
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


class EngineSolver:
    """项目求解器适配器（只吃 BoardView）。"""

    def __init__(self, w, h, a, limit=80):
        from utils.solver import Solver
        self.w, self.h, self.a = w, h, a
        self.s = Solver()          # 完整 Solver（需要 Qt 信号）
        self.s.w = w
        self.s.h = h
        self.s.a = a
        self.s.limit = limit
        self.s.is_play = False
        # 模拟环境无屏幕：把重扫替换为恒等，让算法用我们给的矩阵
        self.s.complete_scan = lambda cell_value, no_10=True: cell_value

    def _to_cell_value(self, view):
        """BoardView → 项目 cell_value 矩阵 ((h+2, w+2), 0 边框)。"""
        cv = np.zeros((self.h + 2, self.w + 2), np.int32)
        for y in range(self.h):
            for x in range(self.w):
                v = view.cells[y][x]
                if v == 9:          # 未开
                    cv[y + 1, x + 1] = 9
                elif 0 <= v <= 8:   # 已开数字
                    cv[y + 1, x + 1] = v
        # 求解器自己的标记 → 10
        for (fx, fy) in view.flags:
            if 0 <= fx < self.w and 0 <= fy < self.h and cv[fy + 1, fx + 1] == 9:
                cv[fy + 1, fx + 1] = 10
        return cv

    def decide(self, view):
        """返回 ("open", (x,y)) 或 None。坐标为 0-based。"""
        cv_in = self._to_cell_value(view)
        self.s.num = 1
        self.s.checked = {}
        self.s.pos_dict_list = []
        self.s.appended_pos = set()
        try:
            self.s.number5_1(cv_in.copy())
        except Exception:  # noqa: BLE001 —— 求解失败退回随机（外部兜底）
            return None
        hm = getattr(self.s, "_last_heatmap", {}) or {}
        best = hm.get("best")
        if best is None:
            # 没有明确推荐：从概率表里挑"非雷概率"最高的未开格
            prob = hm.get("prob") or {}
            cand = [(p, v) for p, v in prob.items()
                    if 0 <= p[0] - 1 < self.w and 0 <= p[1] - 1 < self.h
                    and view.cells[p[1] - 1][p[0] - 1] == 9 and p not in view.flags]
            if not cand:
                return None
            p, _ = max(cand, key=lambda kv: kv[1])
            return ("open", (p[0] - 1, p[1] - 1))
        bx, by = best
        if not (1 <= bx <= self.w and 1 <= by <= self.h):
            return None
        if view.cells[by - 1][bx - 1] != 9:
            return None
        return ("open", (bx - 1, by - 1))


def make_factory(w, h, a, limit=80):
    def _f():
        return EngineSolver(w, h, a, limit)
    return _f


if __name__ == "__main__":
    print("EngineSolver 自检")
    from sim.minesweeper_sim import Board, BoardView
    import random
    rng = random.Random(0)
    b = Board(30, 16, 99, rng)
    b.open(15, 8)
    v = b.view()
    v.audit_no_leak()
    e = EngineSolver(30, 16, 99)
    print("首点后未开格数 =", len(v.unopened()))
    mv = e.decide(v)
    print("求解器决策 =", mv)
