# -*- coding: utf-8 -*-
"""视觉识别与缓存相关测试：_slice_cell、_cell_changed、rescan_after_click、
模板缓存（load_img）、统计缓存（_load_stats/_flush_stats）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.util import Solver
from tests.helpers import make_solver


class TestSliceCell(unittest.TestCase):
    def test_shape(self):
        solver = make_solver(w=5, h=5)
        board = np.zeros((solver.h * solver.cell_width, solver.w * solver.cell_width, 3), dtype=np.uint8)
        cell = solver._slice_cell(board, 3, 3)
        # 切片在两侧各用 w//2、h//2，故实际宽高为 2*(w//2)、2*(h//2)
        expected_w = 2 * (solver.screenshot_w // 2)
        expected_h = 2 * (solver.screenshot_h // 2)
        self.assertEqual(cell.shape, (expected_h, expected_w, 3))

    def test_returns_view(self):
        """_slice_cell 返回视图，修改它应反映到原棋盘（变更检测依赖这一点）。"""
        solver = make_solver(w=5, h=5)
        board = np.full((solver.h * solver.cell_width, solver.w * solver.cell_width, 3), 128, dtype=np.uint8)
        cell = solver._slice_cell(board, 2, 2)
        cell[:, :] = 255
        # 该格子区域应被修改，其它区域保持 128
        other = solver._slice_cell(board, 4, 4)
        self.assertEqual(int(other.mean()), 128)
        self.assertEqual(int(cell.mean()), 255)


class TestCellChanged(unittest.TestCase):
    def test_same_returns_false(self):
        a = np.full((18, 13, 3), 100, dtype=np.uint8)
        self.assertFalse(Solver._cell_changed(a, a.copy()))

    def test_different_returns_true(self):
        a = np.full((18, 13, 3), 100, dtype=np.uint8)
        b = a.copy()
        b[:] = 255
        self.assertTrue(Solver._cell_changed(a, b))

    def test_shape_mismatch_returns_true(self):
        a = np.full((18, 13, 3), 100, dtype=np.uint8)
        b = np.full((10, 10, 3), 100, dtype=np.uint8)
        self.assertTrue(Solver._cell_changed(a, b))

    def test_none_returns_true(self):
        self.assertTrue(Solver._cell_changed(None, np.zeros((18, 13, 3), dtype=np.uint8)))


class TestRescanAfterClick(unittest.TestCase):
    def test_reclassifies_only_changed_cells(self):
        """点击后只对真正变化的未开格重新识别（正确处理 flood-fill 大面积更新）。"""
        solver = make_solver(w=5, h=5)
        board_h = solver.h * solver.cell_width
        board_w = solver.w * solver.cell_width

        old_board = np.full((board_h, board_w, 3), 128, dtype=np.uint8)
        new_board = old_board.copy()
        # 两个相距较远的格子发生变化（模拟 flood-fill 波及远处）
        for col, row in [(2, 2), (4, 4)]:
            solver._slice_cell(new_board, col, row)[:] = 255

        solver.img = old_board

        def fake_grab():
            solver.img = new_board
            return new_board

        solver._grab_board = fake_grab

        called = []

        def fake_compare(img, no_10=True):
            called.append(tuple(img.shape))
            return 3

        solver.compare_img = fake_compare

        cell_value = np.full((solver.h + 2, solver.w + 2), 9, dtype=np.int32)
        result = solver.rescan_after_click(cell_value)

        self.assertEqual(len(called), 2, "只应识别 2 个变化格子")
        self.assertEqual(result[2, 2], 3)
        self.assertEqual(result[4, 4], 3)
        self.assertEqual(result[1, 1], 9, "未变化格子应保持未开")


class TestLoadImgCache(unittest.TestCase):
    def test_cache_hit_and_invalidate(self):
        solver = make_solver(w=5, h=5)
        solver.images = None
        solver._images_cell_width = None
        solver._images_mtime = None

        dummy = np.zeros((18, 13, 3), dtype=np.uint8)
        with mock.patch("utils.vision.cv.imread", return_value=dummy) as imread:
            solver.load_img()
            first = imread.call_count
            self.assertEqual(first, 25)

            # 相同 cell_width，应命中缓存，不重复读取
            solver.load_img()
            self.assertEqual(imread.call_count, first)

            # 改变 cell_width，应重新加载
            solver.cell_width = 30
            solver.load_img()
            self.assertEqual(imread.call_count, first + 25)


class TestStatsCache(unittest.TestCase):
    def test_load_stats(self):
        solver = make_solver()
        m = mock.mock_open(read_data='{"50": {"win": 1, "lose": 0}}')
        with mock.patch("builtins.open", m):
            data = solver._load_stats()
            self.assertEqual(data, {"50": {"win": 1, "lose": 0}})
            # 第二次调用命中缓存，不再读文件
            solver._load_stats()
            self.assertEqual(m.call_count, 1)

    def test_load_stats_missing_file_returns_empty(self):
        solver = make_solver()
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(solver._load_stats(), {})

    def test_flush_stats(self):
        solver = make_solver()
        solver.stats_data = {"50": {"win": 1, "lose": 0}}
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".json")
        tmp.close()
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "data.json":
                return real_open(tmp.name, *args, **kwargs)
            return real_open(path, *args, **kwargs)

        try:
            with mock.patch("builtins.open", side_effect=fake_open):
                solver._flush_stats()
            with real_open(tmp.name, encoding="utf-8") as f:
                written = f.read()
        finally:
            os.unlink(tmp.name)
        self.assertIn("50", written)

    def test_flush_noop_when_none(self):
        solver = make_solver()
        m = mock.mock_open()
        with mock.patch("builtins.open", m):
            solver._flush_stats()
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
