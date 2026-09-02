# -*- coding: utf-8 -*-
"""概率决策相关测试：number5_1、part_solve、part_solve_single、win_rate。"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_full_solver, load_test_data


class TestNumber51(unittest.TestCase):
    """number5_1 主决策入口的冒烟测试。"""

    def test_simple_case(self):
        solver = make_full_solver(w=4, h=3, a=3)
        cell_value = np.array([
            [0, 0, 0, 0, 0, 0],
            [0, 9, 9, 9, 9, 0],
            [0, 9, 1, 9, 9, 0],
            [0, 9, 9, 9, 9, 0],
            [0, 0, 0, 0, 0, 0]
        ], dtype=np.int32)
        solver.cell_value = cell_value.copy()
        result = solver.number5_1(cell_value.copy())
        # 结果可能是 (confidence, ...) 元组，或需要重扫时返回 ndarray
        self.assertTrue(isinstance(result, (tuple, np.ndarray)))
        if isinstance(result, tuple):
            self.assertGreaterEqual(result[0], 0)
            self.assertLessEqual(result[0], 1)


class TestPartSolve(unittest.TestCase):
    """基于真实对局数据（test_33.json）的 part_solve 集成测试。"""

    @classmethod
    def setUpClass(cls):
        cls.data = load_test_data("test_33.json")
        cls.cell_value = np.array(cls.data["cell_value"], dtype=np.int32)

    def _make_solver(self):
        return make_full_solver(w=30, h=16, a=99)

    def test_part_solve_format(self):
        solver = self._make_solver()
        result = solver.part_solve(
            self.data["clicks"][:5], self.cell_value.copy(),
            self.data["num10"], self.data["num9"], self.data["cs"], _try=False)
        res_l, num_solve, canopen_res = result
        self.assertIsInstance(res_l, list)
        self.assertIsInstance(num_solve, int)
        self.assertIsInstance(canopen_res, np.ndarray)
        # 注：任意点击子集可能无可行解，故不强制 num_solve > 0

    def test_part_solve_single_format(self):
        solver = self._make_solver()
        result = solver.part_solve_single(
            self.data["clicks"][:5], self.cell_value.copy(),
            self.data["num10"], self.data["num9"], self.data["cs"], _try=False)
        res_list, total, canopen_res = result
        self.assertIsInstance(res_list, list)
        self.assertIsInstance(total, int)
        self.assertIsInstance(canopen_res, np.ndarray)
        # 注：任意点击子集可能无可行解，故不强制 total > 0


class TestWinRate(unittest.TestCase):
    """win_rate 的最小确定性用例。"""

    def test_minimal(self):
        solver = make_full_solver(w=3, h=3, a=1)
        cell_value = np.zeros((5, 5), dtype=np.int32)
        cell_value[2, 2] = 1  # 中心数字 1，两格未开，恰有 1 雷
        clicks = [(1, 2), (3, 2)]
        clicks9 = []
        # 单组两种场景：左雷右安全 / 左安全右雷
        res_list = [[np.array([1, 0]), np.array([0, 1])]]
        ck = [2]

        res, sorted_clicks, total, clicks2p = solver.win_rate(
            clicks, clicks9, res_list, cell_value, ck, num10=0)

        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 2)
        self.assertEqual(total, 2)
        for v in res:
            self.assertAlmostEqual(v, 0.5, places=6)
        self.assertIsInstance(clicks2p, dict)


class TestTimeBudget(unittest.TestCase):
    """决策耗时预算的指数模型辅助函数。"""

    def _solver(self):
        return make_full_solver(w=5, h=5, a=3)

    def test_predict_monotonic(self):
        from utils.probability import _ps_predict_ms, _ps_observe, _ps_base_ms
        st = {}
        t_small = _ps_predict_ms(st, 20)
        t_big = _ps_predict_ms(st, 33)
        # 指数模型：大组预测显著更大
        self.assertGreater(t_big, t_small * 100)

    def test_observe_calibrates(self):
        from utils.probability import _ps_predict_ms, _ps_observe
        st = {}
        # 模拟实测远慢于初值（如一次 33 格组耗时 1000ms）
        _ps_observe(st, 33, 1000.0)
        self.assertGreater(_ps_predict_ms(st, 33), 500.0)

    def test_mode_change_resets_model(self):
        from utils import native
        from utils.probability import _ps_predict_ms, _ps_observe, _ps_base_ms
        st = {}
        _ps_observe(st, 33, 0.001)  # 记录为极快（C++ 模式）
        self.assertEqual(st.get("ps_native"), native.available)
        # 模拟模式切换后自动重置为默认基准
        saved = native.available
        native.available = not saved
        try:
            _ps_base_ms(st)  # 触发重置
            self.assertNotEqual(st.get("ps_native"), saved)
        finally:
            native.available = saved


class TestWrFeedback(unittest.TestCase):
    """win_rate 阈值的实测反馈调节（无上限升降）。"""

    def test_no_history_returns_start(self):
        from utils.probability import _wr_feedback
        th = _wr_feedback({}, 0.0)
        self.assertEqual(th, 8)  # 起点 6+2

    def test_rises_without_cap(self):
        from utils import native
        from utils.probability import _wr_feedback, DECISION_TIME_BUDGET
        tb = {"wr": {"t": 8, "ms": 10.0, "fast_streak": 0}}
        saved = native.available
        native.available = True
        try:
            th = 8
            # 连续多次快速 win_rate → 每 3 次升 1 档，无上限
            for expected in (8, 8, 9, 9, 9, 10, 10, 10, 11):
                tb["wr"]["t"] = th
                tb["wr"]["ms"] = DECISION_TIME_BUDGET * 1000 * 0.1  # 快（<25% 预算）
                th = _wr_feedback(tb, 0.0)
                self.assertEqual(th, expected)
        finally:
            native.available = saved

    def test_drops_on_overload(self):
        from utils.probability import _wr_feedback, DECISION_TIME_BUDGET
        tb = {"wr": {"t": 11, "ms": DECISION_TIME_BUDGET * 1000 * 0.9, "fast_streak": 2}}
        th = _wr_feedback(tb, 0.0)
        self.assertEqual(th, 10)  # 超预算（>75%）→ 降一档
        self.assertEqual(tb["wr"]["fast_streak"], 0)  # 连击清零

    def test_middle_band_holds(self):
        from utils.probability import _wr_feedback, DECISION_TIME_BUDGET
        tb = {"wr": {"t": 9, "ms": DECISION_TIME_BUDGET * 1000 * 0.5, "fast_streak": 1}}
        th = _wr_feedback(tb, 0.0)
        self.assertEqual(th, 9)  # 25%~75% 区间不动


if __name__ == "__main__":
    unittest.main(verbosity=2)
