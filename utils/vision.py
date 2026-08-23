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
from .mm0 import ClientToScreen


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
        pyautogui.moveTo(10, 640, _pause=False)
        self._grab_board()

        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                if cell_value[y, x] == 9:
                    cell_value[y, x] = self.compare_img(self.cell_screenshot(x, y), no_10)

        return cell_value

    def rescan_after_click(self, cell_value):
        """点击后重扫：抓取新棋盘，只重新识别发生变化的未开格子。

        扫雷点击空白格会触发 flood-fill，实际打开的区域可能远大于 5×5，
        因此不能固定扫描 5×5 窗口，而是对「所有仍标记为未开(9)」的格子做变更检测，
        只对真正变化的格子重新做模板匹配。
        """
        old_img = self.img
        self._grab_board()
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                if cell_value[y, x] == 9:
                    new_cell = self._slice_cell(self.img, x, y)
                    old_cell = (
                        self._slice_cell(old_img, x, y) if old_img is not None else None
                    )
                    if self._cell_changed(old_cell, new_cell):
                        cell_value[y, x] = self.compare_img(new_cell, no_10=True)
        return cell_value
