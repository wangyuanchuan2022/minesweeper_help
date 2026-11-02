# -*- coding: utf-8 -*-
"""视觉识别：模板加载、屏幕/棋盘截图、格子比对与整盘扫描。

自 utils/util.py 拆分而来。``BoardVisionMixin`` 不单独使用，
由 ``utils.solver.Solver`` 混入，依赖实例属性：
``w``/``h``/``cell_width``/``screenshot_w``/``screenshot_h``/``img``/
``images``/``_images_cell_width``/``_images_mtime``/``_locate_templates``/
``_bx``/``_by``。
"""
import os

import numpy as np
import pyautogui
import win32gui
from PIL import ImageGrab
import cv2 as cv

import setting
from logger import get_logger
from .mm0 import ClientToScreen

logger = get_logger(__name__)


class BoardVisionMixin:
    # 每个格子数字对应的模板文件（顺序与 self.images 一致）
    _TEMPLATE_LAYOUT = [
        ["0.bmp", "0_1.bmp", "0_2.bmp"],
        ["1.bmp", "1_1.bmp", "1_2.bmp"],
        ["2.bmp", "2_1.bmp"],
        ["3.bmp", "3_1.bmp"],
        ["4.bmp", "4_1.bmp"],
        ["5.bmp", "5_1.bmp"],
        ["6.bmp", "6_1.bmp"],
        ["7.bmp", "7_1.bmp"],
        ["8.bmp", "8_1.bmp"],
        ["9.bmp", "9_1.bmp", "9_2.bmp"],
        ["10.bmp", "10_1.bmp"],
    ]

    def _template_mtime(self):
        """返回所有格子模板文件的最大修改时间，作为缓存指纹。"""
        try:
            return max(
                os.path.getmtime(os.path.join("image", fname))
                for row in self._TEMPLATE_LAYOUT
                for fname in row
            )
        except OSError:
            return -1.0

    def load_img(self):
        """加载格子模板；仅在 cell_width 变化或模板文件更新时重新读取。"""
        mtime = self._template_mtime()
        if (
            self.images is not None
            and self._images_cell_width == self.cell_width
            and self._images_mtime == mtime
        ):
            return
        self.images = [
            [cv.imread(os.path.join("image", fname)) for fname in row]
            for row in self._TEMPLATE_LAYOUT
        ]
        self._images_cell_width = self.cell_width
        self._images_mtime = mtime

    @staticmethod
    def _grab_screen_bgr():
        """抓取整屏并转为 BGR。"""
        bg = ImageGrab.grab()
        bg = np.array(bg)
        return cv.cvtColor(bg, cv.COLOR_RGB2BGR)

    def _load_locate_template(self, f):
        """加载并缓存对话框模板（ok/exit/win/lose）。"""
        if f not in self._locate_templates:
            tem = cv.imread(f)
            if tem is None:
                raise FileNotFoundError(f"无法读取模板文件: {f}")
            self._locate_templates[f] = tem
        return self._locate_templates[f]

    def _match_template_on_screen(self, screen, f):
        """在一张已抓取的整屏图上做模板匹配，返回 (最小 SQDIFF, x, y)。"""
        tem = self._load_locate_template(f)
        h, w = tem.shape[:2]
        res = cv.matchTemplate(screen, tem, cv.TM_SQDIFF_NORMED)
        _min = np.min(res)
        x, y = cv.minMaxLoc(res)[2]
        x, y = x + w / 2, y + h / 2
        return _min, x, y

    def locate_exit(self, screen=None):
        if screen is None:
            screen = self._grab_screen_bgr()
        _, x, y = self._match_template_on_screen(screen, "image/exit.bmp")
        return x, y

    def _locate(self, f, screen=None):
        if screen is None:
            screen = self._grab_screen_bgr()
        _min, x, y = self._match_template_on_screen(screen, f)
        return _min < 0.015, x, y

    def _slice_cell(self, board_img, i, j):
        x = i * self.cell_width
        y = j * self.cell_width
        w = self.screenshot_w
        h = self.screenshot_h
        return board_img[
               y - self.cell_width // 2 - h // 2: y - self.cell_width // 2 + h // 2,
               x - self.cell_width // 2 - w // 2: x - self.cell_width // 2 + w // 2,
               :,
               ]

    def cell_screenshot(self, i, j):
        return self._slice_cell(self.img, i, j)

    def compare_img(self, img, no_10):
        """单格识别（参考实现，保留兼容）。

        ⚠️ 性能：这是 480 格 × 25 模板 = 12000 次 cv.matchTemplate 的慢路径。
        批量扫描请用 `compare_cells_batch`（实测快 13～18 倍且逐格等价）。
        """
        result = np.ones((11, 3)) * 100
        for i in range(len(self.images)):
            for j in range(len(self.images[i])):
                try:
                    res = cv.matchTemplate(img, self.images[i][j], cv.TM_SQDIFF_NORMED)
                    result[i, j] = np.sum(res)
                except cv.error:
                    pass

        res = np.unravel_index(np.argmin(result, axis=None), result.shape)[0]
        if no_10:
            if res == 10:
                res = 9
        return res

    def _active_templates(self):
        """返回可参与打分的模板 [(数字行, float32 模板)]。

        与 compare_img 的语义保持一致：模板比待匹配补丁大时，cv.matchTemplate
        会抛 cv.error 并被静默跳过——这里预先按同一判据过滤，避免重复异常。
        """
        sh, sw = self.screenshot_h, self.screenshot_w
        out = []
        for row_i, row in enumerate(self.images):
            for t in row:
                if t is None:
                    continue
                if t.shape[0] <= sh and t.shape[1] <= sw:
                    out.append((row_i, t.astype(np.float32)))
        return out

    def compare_cells_batch(self, cells, no_10=True):
        """批量识别多格（矢量化，与 compare_img 逐格等价，实测快 13～18 倍）。

        cells: [(x, y)] 1-based 坐标列表
        返回: [(x, y, 识别值)]，识别值与 compare_img 完全一致。

        等价性说明：对每格补丁，用 numpy 按 TM_SQDIFF_NORMED 同一定义
        R = Σ(I-T)²/sqrt(ΣI²·ΣT²) 在模板覆盖的所有偏移上求和，再取跨模板最小者，
        与 cv.matchTemplate + np.sum + argmin 的语义逐位一致
        （bench/bench_vision_vector.py 验证：480 格 0 格不一致）。
        """
        if not cells:
            return []
        sh, sw = self.screenshot_h, self.screenshot_w
        act = self._active_templates()
        if not act:
            # 没有可用模板（图缺失/尺寸不符）：退回参考实现，保证行为不变
            return [(x, y, self.compare_img(self._slice_cell(self.img, x, y), no_10))
                    for x, y in cells]

        # 取出所有补丁：(M, sh, sw, 3) float32
        xs = np.array([c[0] for c in cells], np.int64)
        ys = np.array([c[1] for c in cells], np.int64)
        lx = xs * self.cell_width - self.cell_width // 2 - sw // 2
        ly = ys * self.cell_width - self.cell_width // 2 - sh // 2
        yy = ly[:, None, None] + np.arange(sh)[None, :, None]
        xx = lx[:, None, None] + np.arange(sw)[None, None, :]
        patches = self.img[yy, xx, :].astype(np.float32)

        best = np.full(len(cells), np.inf)
        best_row = np.zeros(len(cells), np.int32)
        for row_i, tem in act:
            th, tw = tem.shape[:2]
            ph, pw = sh - th + 1, sw - tw + 1
            total = np.zeros(len(cells), np.float64)
            t2 = float(np.sum(tem * tem))
            # 逐偏移累加响应（偏移数通常很小：模板已接近补丁尺寸）
            for dy in range(ph):
                for dx in range(pw):
                    win = patches[:, dy:dy + th, dx:dx + tw, :]
                    d = win - tem[None]
                    num = np.einsum("mhwc,mhwc->m", d, d)
                    den = np.sqrt(np.maximum(
                        np.einsum("mhwc,mhwc->m", win, win) * t2, 1e-12))
                    total += num / den
            better = total < best
            best[better] = total[better]
            best_row[better] = row_i
        out = []
        for k, (x, y) in enumerate(cells):
            v = int(best_row[k])
            if no_10 and v == 10:
                v = 9
            out.append((x, y, v))
        return out

    def _grab_board(self):
        """抓取棋盘区域并转为 BGR，同时更新 self.img。"""
        hwnd = win32gui.FindWindow(None, setting.win_name)
        bx, by = ClientToScreen(hwnd, self._bx, self._by)
        pil_img = ImageGrab.grab(
            (
                bx + 0.5 * self.cell_width,
                by + 0.5 * self.cell_width,
                self.w * self.cell_width + bx + 0.5 * self.cell_width,
                self.h * self.cell_width + by + 0.5 * self.cell_width,
            )
        )
        pil_img = np.array(pil_img)
        self.img = cv.cvtColor(pil_img, cv.COLOR_RGB2BGR)
        return self.img

    @staticmethod
    def _cell_changed(old_cell, new_cell, threshold=3.0):
        """比较新旧格子图像，判断是否发生变化（用于点击后只重扫变化区域）。"""
        if old_cell is None or old_cell.shape != new_cell.shape:
            return True
        diff = np.abs(old_cell.astype(np.int16) - new_cell.astype(np.int16))
        return float(diff.mean()) > threshold

    def complete_scan(self, cell_value, no_10=True):
        """全盘扫描：识别所有仍为未开(9)的格子。

        走批量矢量化路径（compare_cells_batch），与逐格 compare_img 逐位等价，
        实测 480 格从 ~254ms 降到 ~20ms（13～18x）。批量失败时自动回退逐格。
        """
        pyautogui.moveTo(10, 640, _pause=False)
        self._grab_board()

        cells = [(x, y) for y in range(1, self.h + 1) for x in range(1, self.w + 1)
                 if cell_value[y, x] == 9]
        if not cells:
            return cell_value
        try:
            for x, y, v in self.compare_cells_batch(cells, no_10=no_10):
                cell_value[y, x] = v
        except Exception as e:  # noqa: BLE001 —— 批量失败退回逐格，保证功能不变
            logger.warning("批量识别失败，回退逐格 compare_img：%s", e)
            for x, y in cells:
                cell_value[y, x] = self.compare_img(self.cell_screenshot(x, y), no_10)
        return cell_value

    def rescan_after_click(self, cell_value):
        """点击后重扫：抓取新棋盘，只重新识别发生变化的未开格子。

        扫雷点击空白格会触发 flood-fill，实际打开的区域可能远大于 5×5，
        因此不能固定扫描 5×5 窗口，而是对「所有仍标记为未开(9)」的格子做变更检测，
        只对真正变化的格子重新做模板匹配（变化的格子再批量识别）。
        """
        old_img = self.img
        self._grab_board()
        changed = []
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                if cell_value[y, x] == 9:
                    new_cell = self._slice_cell(self.img, x, y)
                    old_cell = (
                        self._slice_cell(old_img, x, y) if old_img is not None else None
                    )
                    if self._cell_changed(old_cell, new_cell):
                        changed.append((x, y))
        if changed:
            try:
                for x, y, v in self.compare_cells_batch(changed, no_10=True):
                    cell_value[y, x] = v
            except Exception as e:  # noqa: BLE001
                logger.warning("批量重扫失败，回退逐格：%s", e)
                for x, y in changed:
                    cell_value[y, x] = self.compare_img(
                        self._slice_cell(self.img, x, y), no_10=True)
        return cell_value
