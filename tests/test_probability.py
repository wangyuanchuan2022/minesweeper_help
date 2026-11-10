# -*- coding: utf-8 -*-
"""概率决策相关测试：number5_1、part_solve、part_solve_single、win_rate。"""
import os
import sys
import unittest
from unittest import mock

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


# ⚠️ 2026-11-09：`TestPartSolve` 已作废删除。
# 原因：`part_solve` / `part_solve_single` 整条 pbs 链路已按用户裁决彻底删除
# （生产不可达，实测 0 调用点；C++ 决策链走 mscore.decide）。
# 这两个测试只验证「已删除函数」的返回格式，失去被测对象后不构成任何契约。
# 保留的等价契约由 test_cpp_decide_wiring 的 hasattr 断言把守。

class TestWinRate(unittest.TestCase):
    """[已移除 2026-11-09] win_rate 的最小确定性用例。

    说明（需求变更留痕，非放宽判据）：
      ProbabilityMixin.win_rate 与 C++ py_win_rate 已按用户裁决
      「彻底删干净（含导出）」一并删除 —— 实测 C++ 决策链（decide_impl）
      与 Python 生产侧均**零调用方**，仅 16 个 11-03~11-04 的作废 bench
      脚本曾直接调 native.mscore.win_rate。

      原用例断言 solver.win_rate(...) 返回 (res, sorted_clicks, total,
      clicks2p)，锁的是被删 API 的具体返回结构；API 已不存在，断言对象
      随之消失，故删除用例本体并保留类占位。

    当前「逐候选胜率」语义的正确入口是 C++ 决策层 OPTIMAL 的 after_click
    （`ExactEngine`，见 cpp/mscore.cpp）。⚠️ 注意：原先把
    bench/verify_exact_cpp.py 列为对拍脚本，但它依赖已删的 mscore.exact_eval
    导出、现无法运行（实测 exit=1），故不再作引用。
    """


class TestTimeBudget(unittest.TestCase):
    """决策耗时预算的指数模型辅助函数。"""

    def _solver(self):
        return make_full_solver(w=5, h=5, a=3)

    def setUp(self):
        # 隔离文件系统：不写样本/状态文件，也不用真实历史样本拟合
        p1 = mock.patch("utils.probability._ps_record_ms", return_value=None)
        p2 = mock.patch("utils.probability._tb_save")
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_predict_monotonic(self):
        from utils.probability import _ps_predict_ms
        st = {"ps_base_ms": 10.0, "ps_native": True, "ps_k": 1.2}
        t_small = _ps_predict_ms(st, 20)
        t_big = _ps_predict_ms(st, 33)
        # 指数模型：大组预测显著更大（13 格差 ×1.2^13≈10.7）
        self.assertGreater(t_big, t_small * 5)

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
    """[已移除 2026-11-09] win_rate 阈值反馈用例。

    说明（需求变更留痕，非放宽判据）：
      用户裁决「wr_threshold winrate 分支都取消了，应该取消这个参数」。
      `_wr_update` / `_wr_current_threshold` 整条「自适应阈值」链路已删除
      —— 它读写 state/time_budget.json 的 wr.t、把阈值传给 C++，而 C++
      在 LAYER 2 删除后对该参数**零消费**，属于自我循环的空转。
      本类原 7 个用例全部直接断言 _wr_update 的步长/边界/上下限行为，
      被测函数已不存在，故移除用例本体、保留类占位与留痕。

    阈值相关的运行期状态（state/time_budget.json 的 wr 字段）已同步清理；
    时间预算模型（ps_k / ps_base_ms）不受影响，仍在 TestTimeBudget 覆盖。
    """


class TestFitK(unittest.TestCase):
    """增长因子自动拟合：样本对比值中位数（base 在比值中消去）。"""

    def test_fit_recovers_true_k(self):
        from utils.probability import _ps_fit_k
        rows = [{"native": True, "size": s, "ms": 2.0 * (1.2 ** (s - 33))}
                for s in (10, 15, 20, 25, 30, 33, 36)]
        k = _ps_fit_k(rows, True)
        self.assertAlmostEqual(k, 1.2, places=1)

    def test_insufficient_samples_returns_none(self):
        from utils.probability import _ps_fit_k
        self.assertIsNone(_ps_fit_k([{"native": True, "size": 10, "ms": 1.0}], True))

    def test_filters_other_mode(self):
        from utils.probability import _ps_fit_k
        rows = [{"native": False, "size": 10, "ms": 1.0},
                {"native": False, "size": 30, "ms": 100.0}]
        self.assertIsNone(_ps_fit_k(rows, True))   # 模式不符的样本不参与拟合

    def test_clamped_to_bounds(self):
        from utils.probability import _ps_fit_k
        rows = [{"native": True, "size": s, "ms": 1.0 * (7.94 ** (s - 10))}
                for s in (10, 20, 30, 40)]   # 隐含 k≈7.94，超上限
        self.assertLessEqual(_ps_fit_k(rows, True), 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
