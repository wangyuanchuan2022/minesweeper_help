# -*- coding: utf-8 -*-
"""Solver 核心推理方法测试：cell_around、get_set、get_set_1、open_num5x5、
number0、mine_clear1、number_3_1。"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_solver, make_full_solver, make_board


class TestCellAround(unittest.TestCase):
    def setUp(self):
        self.solver = make_solver(w=5, h=5)

    def test_basic(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 1
        cell_value[1, 1] = 9
        cell_value[1, 2] = 10
        cnt9, cnt10 = self.solver.cell_around(2, 2, cell_value)
        self.assertEqual(cnt9, 1)
        self.assertEqual(cnt10, 1)

    def test_boundary_corner(self):
        cell_value = make_board(5, 5)
        cell_value[1, 1] = 2
        cell_value[0, 0] = 9
        cell_value[0, 1] = 9
        cell_value[1, 0] = 10
        cnt9, cnt10 = self.solver.cell_around(1, 1, cell_value)
        self.assertEqual(cnt9, 2)
        self.assertEqual(cnt10, 1)

    def test_all_unopened(self):
        cell_value = make_board(5, 5, fill=9)
        cnt9, cnt10 = self.solver.cell_around(3, 3, cell_value)
        self.assertEqual(cnt9, 9)
        self.assertEqual(cnt10, 0)

    def test_all_mines(self):
        cell_value = make_board(5, 5, fill=10)
        cnt9, cnt10 = self.solver.cell_around(3, 3, cell_value)
        self.assertEqual(cnt9, 0)
        self.assertEqual(cnt10, 9)

    def test_empty_neighbors(self):
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 1
        cnt9, cnt10 = self.solver.cell_around(3, 3, cell_value)
        self.assertEqual(cnt9, 0)
        self.assertEqual(cnt10, 0)


class TestGetSet(unittest.TestCase):
    def setUp(self):
        self.solver = make_solver(w=5, h=5)

    def test_basic(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 2
        cell_value[1, 1] = 9
        cell_value[2, 1] = 9
        cell_value[1, 2] = 10
        result_set, cnt10 = self.solver.get_set(2, 2, cell_value)
        self.assertEqual(cnt10, 1)
        self.assertEqual(len(result_set), 2)
        self.assertIn((1, 1), result_set)
        self.assertIn((1, 2), result_set)

    def test_no_unopened(self):
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 1
        result_set, cnt10 = self.solver.get_set(3, 3, cell_value)
        self.assertEqual(len(result_set), 0)
        self.assertEqual(cnt10, 0)

    def test_only_mines(self):
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 3
        for n in range(2, 5):
            for m in range(2, 5):
                if m != 3 or n != 3:
                    cell_value[n, m] = 10
        result_set, cnt10 = self.solver.get_set(3, 3, cell_value)
        self.assertEqual(len(result_set), 0)
        self.assertEqual(cnt10, 8)


class TestGetSet1(unittest.TestCase):
    def setUp(self):
        self.solver = make_solver(w=5, h=5)

    def test_basic(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 3
        cell_value[1, 1] = 1
        cell_value[2, 1] = 2
        cell_value[1, 2] = 9
        result = self.solver.get_set_1(2, 2, cell_value)
        self.assertEqual(len(result), 3)
        self.assertIn((1, 1), result)
        self.assertIn((1, 2), result)
        self.assertIn((2, 2), result)

    def test_no_numbered_neighbors(self):
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 9
        self.assertEqual(len(self.solver.get_set_1(3, 3, cell_value)), 0)

    def test_filters_values(self):
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 2
        cell_value[2, 2] = 9    # 未开，应排除
        cell_value[2, 3] = 10   # 雷，应排除
        cell_value[3, 2] = 1    # 数字 1，应包含
        cell_value[3, 4] = 5    # 数字 5，应包含
        cell_value[4, 4] = 8    # 数字 8，超出 (0,8)，应排除
        cell_value[2, 4] = 3    # 数字 3，应包含
        result = self.solver.get_set_1(3, 3, cell_value)
        self.assertIn((3, 3), result)
        self.assertIn((2, 3), result)
        self.assertIn((4, 3), result)
        self.assertIn((4, 2), result)
        self.assertNotIn((2, 2), result)
        self.assertNotIn((3, 2), result)
        self.assertNotIn((4, 4), result)


class TestOpenNum5x5(unittest.TestCase):
    def setUp(self):
        self.solver = make_solver(w=10, h=10)

    def test_center_fully_open(self):
        cell_value = make_board(10, 10)
        self.assertEqual(self.solver.open_num5x5(cell_value, (5, 5)), 25)

    def test_center_fully_unopened(self):
        cell_value = make_board(10, 10, fill=9)
        self.assertEqual(self.solver.open_num5x5(cell_value, (5, 5)), 0)

    def test_boundary(self):
        cell_value = make_board(10, 10, fill=9)
        self.assertGreater(self.solver.open_num5x5(cell_value, (1, 1)), 0)

    def test_mixed(self):
        cell_value = make_board(10, 10, fill=9)
        for i in range(4, 7):
            for j in range(4, 7):
                cell_value[j, i] = 1
        self.assertEqual(self.solver.open_num5x5(cell_value, (5, 5)), 9)


class TestNumber0(unittest.TestCase):
    def setUp(self):
        # mine_clear1/number_3_1 内含实时热力图发射，需要完整初始化的 Qt 实例
        self.solver = make_full_solver(w=5, h=5)
        self.solver.appended_pos = set()

    def test_all_mines(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 1
        cell_value[1, 1] = 9
        self.solver.cell_value = cell_value.copy()
        result = self.solver.number0(2, 2, cell_value.copy())
        self.assertEqual(result[1, 1], 10)

    def test_all_safe(self):
        """数字 2 且周围已有 2 个雷 -> 其余未开格安全。"""
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 2
        cell_value[1, 1] = 10
        cell_value[1, 2] = 10
        cell_value[2, 1] = 9
        cell_value[3, 3] = 9
        self.solver.cell_value = cell_value.copy()
        result = self.solver.number0(2, 2, cell_value.copy())
        # 帮助模式下，安全格被标记为 11 并加入 pos_dict_list
        self.assertEqual(result[2, 1], 11)
        self.assertEqual(result[3, 3], 11)

    def test_inconsistent_raises(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 1
        cell_value[1, 1] = 10
        cell_value[1, 2] = 10
        with self.assertRaises(ValueError):
            self.solver.number0(2, 2, cell_value.copy())


class TestMineClear1(unittest.TestCase):
    def setUp(self):
        # mine_clear1/number_3_1 内含实时热力图发射，需要完整初始化的 Qt 实例
        self.solver = make_full_solver(w=5, h=5)
        self.solver.appended_pos = set()

    def test_with_clicks(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 1
        cell_value[1, 1] = 9
        self.solver.cell_value = cell_value.copy()
        result = self.solver.mine_clear1(cell_value.copy(), clicks=[(2, 2)])
        self.assertEqual(result[1, 1], 10)

    def test_simple_board(self):
        cell_value = make_board(5, 5)
        cell_value[2, 2] = 1
        cell_value[1, 1] = 9
        self.solver.cell_value = cell_value.copy()
        result = self.solver.mine_clear1(cell_value.copy())
        self.assertEqual(result[1, 1], 10)


class TestNumber31(unittest.TestCase):
    """number_3_1 高级推理的基础用例。"""

    def setUp(self):
        # mine_clear1/number_3_1 内含实时热力图发射，需要完整初始化的 Qt 实例
        self.solver = make_full_solver(w=5, h=5)
        self.solver.appended_pos = set()

    def test_no_change_when_satisfied(self):
        # 数字已经满足时不应修改棋盘
        cell_value = make_board(5, 5)
        cell_value[3, 3] = 2
        cell_value[2, 2] = 10
        cell_value[2, 3] = 10
        result = self.solver.number_3_1(3, 3, cell_value.copy())
        self.assertTrue(np.array_equal(result, cell_value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
