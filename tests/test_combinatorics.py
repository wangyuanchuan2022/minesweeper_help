# -*- coding: utf-8 -*-
"""组合数学工具函数测试：C、C_num、A、p_of_c、get_list、combination_ratio。"""
import math
import os
import sys
import unittest
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.util import C, C_num, A, p_of_c, get_list, combination_ratio


def _exact_c(n, k):
    """精确整数组合数 C(n, k)。"""
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    num = 1
    den = 1
    for i in range(k):
        num *= n - i
        den *= i + 1
    return num // den


class TestC(unittest.TestCase):
    """C(a, b) 组合生成器"""

    def test_basic(self):
        result = list(C(4, 2))
        self.assertEqual(result, [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]])

    def test_single_element(self):
        self.assertEqual(list(C(3, 1)), [[0], [1], [2]])

    def test_select_all(self):
        self.assertEqual(list(C(3, 3)), [[0, 1, 2]])

    def test_zero_select(self):
        self.assertEqual(list(C(5, 0)), [[]])

    def test_b_greater_than_a(self):
        self.assertEqual(list(C(2, 5)), [])

    def test_negative_b(self):
        self.assertEqual(list(C(3, -1)), [])

    def test_large(self):
        self.assertEqual(len(list(C(10, 3))), 120)

    def test_yields_independent_lists(self):
        """每次 yield 都应是独立列表，而不是同一对象的别名。"""
        result = list(C(4, 2))
        self.assertEqual(len({id(x) for x in result}), len(result))
        result[0][0] = 999
        self.assertEqual(list(C(4, 2))[0], [0, 1])  # 修改一个结果不影响后续生成

    def test_start_from(self):
        result = list(C(5, 2, start_from=[2, 3]))
        self.assertTrue(result)
        for combo in result:
            self.assertEqual(len(combo), 2)
            self.assertTrue(all(0 <= x < 5 for x in combo))

    def test_start_from_does_not_mutate_input(self):
        start = [2, 3]
        list(C(5, 2, start_from=start))
        self.assertEqual(start, [2, 3])

    def test_exact_count(self):
        for n in range(1, 9):
            for k in range(0, n + 1):
                self.assertEqual(len(list(C(n, k))), _exact_c(n, k),
                                 f"C({n},{k}) count mismatch")


class TestCNum(unittest.TestCase):
    """C_num(a, b) 组合数计算"""

    def test_basic(self):
        self.assertEqual(C_num(4, 2), 6)
        self.assertEqual(C_num(5, 3), 10)
        self.assertEqual(C_num(6, 1), 6)

    def test_edge_cases(self):
        self.assertEqual(C_num(5, 0), 1)
        self.assertEqual(C_num(3, 3), 1)
        self.assertEqual(C_num(0, 0), 1)

    def test_consistency_with_C(self):
        for n in range(1, 12):
            for k in range(0, n + 1):
                self.assertEqual(int(C_num(n, k)), len(list(C(n, k))),
                                 f"C_num({n},{k}) != len(C({n},{k}))")

    def test_symmetry(self):
        for n in range(1, 10):
            for k in range(0, n + 1):
                self.assertAlmostEqual(C_num(n, k), C_num(n, n - k), places=6)


class TestA(unittest.TestCase):
    """A(ck) 全排列生成器"""

    def test_basic(self):
        result = list(A([2, 3]))
        self.assertEqual(len(result), 6)
        self.assertTrue(np.array_equal(result[0], [0, 0]))
        self.assertTrue(np.array_equal(result[-1], [1, 2]))

    def test_single_dimension(self):
        result = list(A([3]))
        self.assertEqual(len(result), 3)
        for i, arr in enumerate(result):
            self.assertTrue(np.array_equal(arr, [i]))

    def test_empty(self):
        result = list(A([]))
        self.assertEqual(len(result), 1)
        self.assertTrue(np.array_equal(result[0], np.array([], dtype=np.int32)))

    def test_three_dimensions(self):
        self.assertEqual(len(list(A([2, 2, 2]))), 8)

    def test_yields_independent_arrays(self):
        result = list(A([2, 2]))
        self.assertEqual(len({id(x) for x in result}), len(result))

    def test_unique(self):
        result = list(A([3, 3]))
        strs = [arr.tobytes() for arr in result]
        self.assertEqual(len(strs), len(set(strs)))

    def test_count(self):
        for dims in [[2, 2], [2, 3], [3, 2], [2, 2, 2]]:
            expected = 1
            for d in dims:
                expected *= d
            self.assertEqual(len(list(A(dims))), expected, f"A({dims})")


class TestPofC(unittest.TestCase):
    """p_of_c(x, n) 概率计算"""

    def test_basic(self):
        self.assertAlmostEqual(p_of_c(0, 5), 0.1, places=6)
        self.assertAlmostEqual(p_of_c(1, 4), 2 / 3, places=6)

    def test_edge_n_zero(self):
        self.assertEqual(p_of_c(0, 0), 1)

    def test_symmetry(self):
        for n in range(1, 10):
            for x in range(0, n + 1):
                self.assertAlmostEqual(p_of_c(x, n), p_of_c(n - x, n), places=6,
                                       msg=f"p_of_c({x},{n})")

    def test_range(self):
        for n in range(1, 20):
            for x in range(0, n + 1):
                result = p_of_c(x, n)
                self.assertGreaterEqual(result, 0)
                self.assertLessEqual(result, 1)


class TestCombinationRatio(unittest.TestCase):
    """combination_ratio(x, x_min, n) 组合数比值"""

    def test_same_args_is_one(self):
        for n in [5, 20, 100]:
            for x in range(0, min(n, 10) + 1):
                self.assertEqual(combination_ratio(x, x, n), 1.0)

    def test_matches_exact_ratio(self):
        for n in [10, 50, 100]:
            for x_min in range(1, min(n, 40) + 1):
                for x in range(0, x_min + 1):
                    exact = float(Fraction(_exact_c(n, x), _exact_c(n, x_min)))
                    got = combination_ratio(x, x_min, n)
                    self.assertTrue(
                        math.isclose(got, exact, rel_tol=1e-9, abs_tol=1e-12),
                        f"combination_ratio({x},{x_min},{n})={got} != {exact}",
                    )

    def test_consistent_with_p_of_c(self):
        """新公式应与旧的 p_of_c 比值一致（但更精确）。"""
        for n in [10, 50, 100]:
            for x_min in range(1, min(n, 40) + 1):
                for x in range(0, x_min + 1):
                    new = combination_ratio(x, x_min, n)
                    old = p_of_c(x, n) / p_of_c(x_min, n)
                    self.assertAlmostEqual(new, old, places=9)

    def test_range(self):
        # 实际使用场景中 x_min <= n/2（剩余雷数远小于格子数），此时比值应在 (0, 1]
        for n in [10, 100]:
            for x_min in range(1, n // 2 + 1):
                for x in range(0, x_min + 1):
                    result = combination_ratio(x, x_min, n)
                    self.assertGreaterEqual(result, 0)
                    self.assertLessEqual(result, 1)


class TestGetList(unittest.TestCase):
    """get_list(a, num, listnum) 组合索引生成器"""

    def test_basic_total(self):
        gen = get_list(1, 2, 4)
        total = next(gen)
        self.assertEqual(total, 10)

    def test_total_count(self):
        for a in [1, 2]:
            for num in [2, 3, 4]:
                for listnum in [4, 5, 6]:
                    if a <= num < listnum:
                        total = next(get_list(a, num, listnum))
                        expected = sum(C_num(listnum, i) for i in range(a, num + 1))
                        self.assertAlmostEqual(total, expected, places=6)

    def test_output_is_list(self):
        gen = get_list(1, 2, 4)
        next(gen)
        for combo in gen:
            self.assertIsInstance(combo, list)

    def test_valid_combinations(self):
        gen = get_list(2, 2, 5)
        next(gen)
        for combo in gen:
            self.assertTrue(all(0 <= x < 5 for x in combo))
            self.assertEqual(len(set(combo)), len(combo))


if __name__ == "__main__":
    unittest.main(verbosity=2)
