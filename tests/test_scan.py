# -*- coding: utf-8 -*-
"""视觉识别与缓存相关测试：_slice_cell、_cell_changed、rescan_after_click、
模板缓存与模板校验（load_img）、识别契约（UNKNOWN/低置信度/batch-ref 等价）、
统计缓存（_load_stats/_flush_stats）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.util import Solver
from utils import vision
from utils.vision import (
    UNKNOWN,
    LowConfidenceScanError,
    TemplateValidationError,
)
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


    def test_auto_rescan_can_suppress_normal_end_of_game_warning(self):
        """自动局末重扫可抑制告警，但仍保留低置信度异常语义。"""
        solver = make_solver(w=2, h=2)
        board_h = solver.h * solver.cell_width
        board_w = solver.w * solver.cell_width
        image = np.zeros((board_h, board_w, 3), dtype=np.uint8)
        solver.img = image
        solver._grab_board = lambda: image
        solver._slice_cell = lambda _img, _x, _y: np.zeros((2, 2, 3), dtype=np.uint8)
        solver._cell_changed = lambda _old, _new: True
        solver.compare_cells_batch = lambda cells, no_10=True: [
            (x, y, UNKNOWN) for x, y in cells
        ]
        solver.warning_signal = mock.Mock()
        board = np.full((solver.h + 2, solver.w + 2), 9, dtype=np.int32)
        with mock.patch.object(vision, "STRICT_LOW_CONFIDENCE", True):
            with self.assertRaises(LowConfidenceScanError):
                solver.rescan_after_click(board, emit_warning=False)
        solver.warning_signal.emit.assert_not_called()

    def test_default_rescan_still_emits_low_confidence_warning(self):
        """帮助/默认调用不传标记时仍保留模板校准诊断。"""
        solver = make_solver(w=2, h=2)
        image = np.zeros((solver.h * solver.cell_width,
                          solver.w * solver.cell_width, 3), dtype=np.uint8)
        solver.img = image
        solver._grab_board = lambda: image
        solver._slice_cell = lambda _img, _x, _y: np.zeros((2, 2, 3), dtype=np.uint8)
        solver._cell_changed = lambda _old, _new: True
        solver.compare_cells_batch = lambda cells, no_10=True: [
            (x, y, UNKNOWN) for x, y in cells
        ]
        solver.warning_signal = mock.Mock()
        board = np.full((solver.h + 2, solver.w + 2), 9, dtype=np.int32)
        with mock.patch.object(vision, "STRICT_LOW_CONFIDENCE", True):
            with self.assertRaises(LowConfidenceScanError):
                solver.rescan_after_click(board)
        solver.warning_signal.emit.assert_called_once()


class TestLoadImgCache(unittest.TestCase):
    def test_cache_hit_and_invalidate(self):
        solver = make_solver(w=5, h=5)
        solver.images = None
        solver._images_cell_width = None
        solver._images_mtime = None

        # dummy 尺寸必须是生产补丁的真实尺寸 (18, 12)：旧测试用 (18, 13) 会把
        # "模板比补丁宽 1px" 这整类缺陷掩盖掉（见 review/视觉域.md P1-2）。
        dummy = np.zeros((18, 12, 3), dtype=np.uint8)
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


class TestLoadImgValidation(unittest.TestCase):
    """模板加载校验：缺失/通道/dtype/尺寸必须可诊断，不得静默带病运行。"""

    @staticmethod
    def _fresh_solver():
        solver = make_solver(w=5, h=5)
        solver.images = None
        solver._images_cell_width = None
        solver._images_mtime = None
        return solver

    def test_missing_template_raises_with_filename(self):
        solver = self._fresh_solver()
        good = np.zeros((18, 12, 3), dtype=np.uint8)

        def fake_imread(path, *args, **kwargs):
            return None if path.endswith("3.bmp") else good

        with mock.patch("utils.vision.cv.imread", side_effect=fake_imread):
            with self.assertRaises(TemplateValidationError) as ctx:
                solver.load_img()
        msg = str(ctx.exception)
        self.assertIn("3.bmp", msg, "错误信息必须点名具体模板文件")
        self.assertIn("读取失败", msg)
        self.assertIsNone(solver.images, "校验失败不得把坏模板缓存进 self.images")

    def test_wrong_channel_count_raises(self):
        solver = self._fresh_solver()
        four_channel = np.zeros((18, 12, 4), dtype=np.uint8)
        with mock.patch("utils.vision.cv.imread", return_value=four_channel):
            with self.assertRaises(TemplateValidationError) as ctx:
                solver.load_img()
        self.assertIn("3 通道", str(ctx.exception))

    def test_wrong_dtype_raises(self):
        solver = self._fresh_solver()
        float_img = np.zeros((18, 12, 3), dtype=np.float32)
        with mock.patch("utils.vision.cv.imread", return_value=float_img):
            with self.assertRaises(TemplateValidationError) as ctx:
                solver.load_img()
        self.assertIn("uint8", str(ctx.exception))

    def test_other_calibration_family_is_diagnostic_not_fatal(self):
        """仓库实际存在两族模板（18x12 / 28x20）：另一族只记诊断，不致命。

        这是必要约束——若直接 raise，cw=24 的生产初始化（Solver.__init__ →
        load_img）会整个坏掉，把"可见失败"做成"启动即崩"。
        """
        solver = self._fresh_solver()

        def real_shape_imread(path, *args, **kwargs):
            if "_" in os.path.basename(path):
                return np.zeros((28, 20, 3), dtype=np.uint8)
            return np.zeros((18, 12, 3), dtype=np.uint8)

        with mock.patch("utils.vision.cv.imread", side_effect=real_shape_imread):
            solver.load_img()
        self.assertIsNotNone(solver.images)
        active = solver._active_templates()
        self.assertEqual(len(active), 11, "只有与当前补丁尺寸相符的 11 个模板可用")
        self.assertEqual(sorted({row for row, _ in active}), list(range(11)))

    def test_all_templates_wrong_size_raises(self):
        solver = self._fresh_solver()
        too_big = np.zeros((28, 20, 3), dtype=np.uint8)
        with mock.patch("utils.vision.cv.imread", return_value=too_big):
            with self.assertRaises(TemplateValidationError) as ctx:
                solver.load_img()
        self.assertIn("18x12", str(ctx.exception), "必须报出当前补丁尺寸与恢复指引")
        self.assertIn("cell_width", str(ctx.exception))


def _load_real_templates():
    """读取仓库真实模板（image/*.bmp）；缺失时返回 None 让用例 skip。"""
    import cv2 as cv

    images = []
    for row in Solver._TEMPLATE_LAYOUT:
        loaded = []
        for fname in row:
            path = os.path.join("image", fname)
            if not os.path.exists(path):
                return None
            img = cv.imread(path)
            if img is None:
                return None
            loaded.append(img)
        images.append(loaded)
    return images


def _board_with_templates(solver, labels, images):
    """构造与真实模板逐像素一致的合成棋盘，返回 (board, cells)。

    labels: [(x, y, 数字行)]，1-based 坐标；数字行对应 _TEMPLATE_LAYOUT 的下标。
    直接复用 _slice_cell 的取窗几何，保证与生产识别路径完全对齐。
    """
    board_h = (solver.h + 1) * solver.cell_width
    board_w = (solver.w + 1) * solver.cell_width
    board = np.zeros((board_h, board_w, 3), dtype=np.uint8)
    cells = []
    for x, y, row_i in labels:
        template = images[row_i][0]  # 每族的第一个模板即生产补丁尺寸
        region = solver._slice_cell(board, x, y)
        self_h, self_w = region.shape[:2]
        if template.shape[:2] != (self_h, self_w):
            raise AssertionError(
                f"模板 {template.shape[:2]} 与补丁 {(self_h, self_w)} 尺寸不符")
        region[:, :] = template
        cells.append((x, y))
    return board, cells


class TestRecognitionContract(unittest.TestCase):
    """识别契约：UNKNOWN / 低置信度 / batch 与参考实现等价。"""

    def setUp(self):
        self.images = _load_real_templates()
        if self.images is None:
            self.skipTest("image/ 下真实模板缺失，跳过真实模板契约用例")

    def _solver_with_real_templates(self):
        solver = make_solver(w=5, h=5)
        solver.images = self.images
        solver._images_cell_width = solver.cell_width
        solver._images_mtime = solver._template_mtime()
        return solver

    def test_real_template_self_match_unchanged(self):
        """不改真实模板识别语义：每个可用模板贴回原位仍识别为自己的数字。"""
        solver = self._solver_with_real_templates()
        for row_i, row in enumerate(self.images):
            for template in row:
                if template.shape[:2] != (18, 12):
                    continue  # 另一标定族，当前 cw 不参与识别
                got = solver.compare_img(template.copy(), no_10=False)
                self.assertEqual(got, row_i, f"模板行 {row_i} 自匹配错判为 {got}")

    def test_noise_patch_returns_unknown(self):
        solver = self._solver_with_real_templates()
        rng = np.random.default_rng(20261106)
        noise = rng.integers(0, 256, (18, 12, 3), dtype=np.uint8)
        self.assertEqual(solver.compare_img(noise, no_10=True), UNKNOWN)

    def test_flat_colors_return_unknown(self):
        solver = self._solver_with_real_templates()
        for name, value in (("black", 0), ("white", 255), ("gray", 128)):
            patch = np.full((18, 12, 3), value, dtype=np.uint8)
            self.assertEqual(solver.compare_img(patch, no_10=True), UNKNOWN, name)

    def test_compare_img_without_templates_returns_unknown(self):
        """模板全部不可用（None）→ UNKNOWN，而不是静默返回 0。"""
        solver = make_solver(w=5, h=5)
        solver.images = [[None] * len(row) for row in Solver._TEMPLATE_LAYOUT]
        self.assertEqual(solver.compare_img(np.zeros((18, 12, 3), np.uint8), True), UNKNOWN)

    def test_batch_matches_reference_on_real_template_board(self):
        """>=3 个格子的批量识别必须与逐格参考实现逐格一致（含 UNKNOWN 判据）。"""
        solver = self._solver_with_real_templates()
        labels = [(1, 1, 0), (2, 1, 1), (3, 2, 4), (4, 3, 9), (5, 4, 10)]
        board, cells = _board_with_templates(solver, labels, self.images)
        solver.img = board

        expected = [(x, y, solver.compare_img(solver.cell_screenshot(x, y), True))
                    for x, y in cells]
        got = solver.compare_cells_batch(cells, no_10=True)
        self.assertEqual(len(got), len(labels))
        self.assertEqual(got, expected, "批量与参考实现必须逐格等价")
        # 与真值比对：贴入的真实模板必须被正确分类（no_10 把 10 映射成 9）
        self.assertEqual([v for _, _, v in got],
                         [0, 1, 4, 9, 9])

    def test_batch_and_ref_agree_on_mixed_board(self):
        """混合板（真模板格 + 噪声格）上 batch 与 ref 必须逐格一致。

        这条防止「全 UNKNOWN 导致等价性假绿」：至少 3 个格有真实类别、
        至少 2 个格是低置信度，两边都必须给出同样的值与同样的 UNKNOWN 位置。
        """
        solver = self._solver_with_real_templates()
        labels = [(1, 1, 0), (2, 1, 3), (3, 2, 7)]
        board, real_cells = _board_with_templates(solver, labels, self.images)
        rng = np.random.default_rng(99)
        noise_cells = [(4, 3), (5, 4)]
        for x, y in noise_cells:
            solver._slice_cell(board, x, y)[:] = rng.integers(
                0, 256, (18, 12, 3), dtype=np.uint8)
        solver.img = board
        cells = real_cells + noise_cells

        ref = [(x, y, solver.compare_img(solver.cell_screenshot(x, y), True))
               for x, y in cells]
        got = solver.compare_cells_batch(cells, no_10=True)
        self.assertEqual(got, ref, "批量与参考实现必须逐格等价（含 UNKNOWN 位置）")
        self.assertEqual([v for _, _, v in got], [0, 3, 7, UNKNOWN, UNKNOWN])

    def test_batch_low_confidence_returns_unknown(self):
        solver = self._solver_with_real_templates()
        board_h = (solver.h + 1) * solver.cell_width
        board_w = (solver.w + 1) * solver.cell_width
        rng = np.random.default_rng(42)
        solver.img = rng.integers(0, 256, (board_h, board_w, 3), dtype=np.uint8)
        cells = [(1, 1), (2, 2), (3, 3)]
        got = solver.compare_cells_batch(cells, no_10=True)
        self.assertEqual([v for _, _, v in got], [UNKNOWN, UNKNOWN, UNKNOWN])

    def test_batch_without_templates_raises(self):
        solver = make_solver(w=5, h=5)
        solver.images = [[None] * len(row) for row in Solver._TEMPLATE_LAYOUT]
        solver.img = np.zeros(((solver.h + 1) * solver.cell_width,
                               (solver.w + 1) * solver.cell_width, 3), dtype=np.uint8)
        with self.assertRaises(TemplateValidationError):
            solver.compare_cells_batch([(1, 1)], no_10=True)


class TestScanUnknownContract(unittest.TestCase):
    """complete_scan/rescan：UNKNOWN 不得写入棋盘；坏模板必须显式可见。"""

    @staticmethod
    def _solver():
        solver = make_solver(w=5, h=5)
        solver.img = np.zeros(((6) * solver.cell_width,
                               (6) * solver.cell_width, 3), dtype=np.uint8)
        return solver

    @staticmethod
    def _fresh_board(solver):
        return np.full((solver.h + 2, solver.w + 2), 9, dtype=np.int32)

    def test_all_unknown_is_visible_and_board_untouched(self):
        """模板全坏：不得静默返回 0 —— 必须告警 + 棋盘保持未开，不写假数据。"""
        solver = self._solver()
        solver._grab_board = lambda: solver.img
        solver.compare_cells_batch = (
            lambda cells, no_10=True: [(x, y, UNKNOWN) for x, y in cells])
        board = self._fresh_board(solver)
        with mock.patch("utils.vision.pyautogui.moveTo"):
            with self.assertLogs("utils.vision", level="ERROR") as logs:
                result = solver.complete_scan(board, no_10=True)
        self.assertIn("无法可信识别", "\n".join(logs.output))
        self.assertTrue(np.all(result[1:-1, 1:-1] == 9),
                        "不可信时不得写入任何识别结果")
        self.assertFalse(np.any(result < 0), "UNKNOWN 不得写进棋盘")

    def test_all_unknown_emits_warning_signal(self):
        """有 Qt 信号的真实实例：告警必须通过 warning_signal 对用户可见。"""
        solver = self._solver()
        emitted = []
        solver.warning_signal = mock.Mock()
        solver.warning_signal.emit = emitted.append
        solver._grab_board = lambda: solver.img
        solver.compare_cells_batch = (
            lambda cells, no_10=True: [(x, y, UNKNOWN) for x, y in cells])
        board = self._fresh_board(solver)
        with mock.patch("utils.vision.pyautogui.moveTo"):
            with self.assertLogs("utils.vision", level="ERROR"):
                solver.complete_scan(board, no_10=True)
        self.assertEqual(len(emitted), 1)
        self.assertIn("无法可信识别", emitted[0])

    def test_strict_mode_raises(self):
        """STRICT_LOW_CONFIDENCE=True（自动化环境）升级为硬失败。"""
        solver = self._solver()
        solver._grab_board = lambda: solver.img
        solver.compare_cells_batch = (
            lambda cells, no_10=True: [(x, y, UNKNOWN) for x, y in cells])
        board = self._fresh_board(solver)
        with mock.patch("utils.vision.pyautogui.moveTo"), \
                mock.patch.object(vision, "STRICT_LOW_CONFIDENCE", True):
            with self.assertLogs("utils.vision", level="ERROR"):
                with self.assertRaises(LowConfidenceScanError) as ctx:
                    solver.complete_scan(board, no_10=True)
        self.assertIn("无法可信识别", str(ctx.exception))
        self.assertTrue(np.all(board[1:-1, 1:-1] == 9),
                        "中止时不得把 UNKNOWN 写进棋盘")

    def test_small_unknown_ratio_keeps_cells_unopened(self):
        """少量低置信度：告警并保持未开(9)，不中断正常扫描。"""
        solver = self._solver()
        board = self._fresh_board(solver)
        total = solver.w * solver.h

        def fake_batch(cells, no_10=True):
            out = []
            for k, (x, y) in enumerate(cells):
                out.append((x, y, UNKNOWN if k < 3 else 2))
            return out

        solver._grab_board = lambda: solver.img
        solver.compare_cells_batch = fake_batch
        with mock.patch("utils.vision.pyautogui.moveTo"):
            with self.assertLogs("utils.vision", level="WARNING") as logs:
                solver.complete_scan(board, no_10=True)
        unknown_cells = [(x, y) for y in range(1, solver.h + 1)
                         for x in range(1, solver.w + 1) if board[y, x] == 9]
        self.assertEqual(len(unknown_cells), 3, "3 个不可信格保持未开")
        self.assertTrue(any("无法可信识别" in line for line in logs.output))
        # 其余格正常写入识别值
        self.assertEqual(int(np.sum(board[1:-1, 1:-1] == 2)), total - 3)
        self.assertFalse(np.any(board < 0), "棋盘上不得出现 UNKNOWN 负值")

    def test_batch_failure_falls_back_to_per_cell(self):
        solver = self._solver()
        board = self._fresh_board(solver)
        calls = []

        def bad_batch(cells, no_10=True):
            raise RuntimeError("模拟批量失败")

        solver._grab_board = lambda: solver.img
        solver.compare_cells_batch = bad_batch
        solver.compare_img = lambda img, no_10=True: calls.append(1) or 3
        with mock.patch("utils.vision.pyautogui.moveTo"):
            solver.complete_scan(board, no_10=True)
        self.assertEqual(len(calls), solver.w * solver.h, "批量失败必须回退逐格识别")
        self.assertTrue(np.all(board[1:-1, 1:-1] == 3))


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
        try:
            # 契约变更（配置/资源路径修复）：_flush_stats 现在写**绝对路径**
            # setting.DATA_PATH（不再依赖 cwd 的裸名 "data.json"）。因此把该
            # 绝对常量 patch 到临时文件——断言仍是「真实落盘且内容正确」。
            with mock.patch("utils.solver._DATA_PATH", tmp.name):
                solver._flush_stats()
            with open(tmp.name, encoding="utf-8") as f:
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
