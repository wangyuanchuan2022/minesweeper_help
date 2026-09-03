# -*- coding: utf-8 -*-
"""热力图 UI 测试（offscreen 实例化主窗口）：渲染/统计栏/磨砂样式/坐标映射/去重。

需要 PyQt5；QT_QPA_PLATFORM=offscreen 保证无显示环境也可运行。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5 import QtWidgets
import numpy as np

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import main


class TestHeatmapUI(unittest.TestCase):
    """自动模式热力图：磨砂卡片、概率配色、数字不遮挡、统计栏、样式去重。"""

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()
        cls.h, cls.w = cls.win.h, cls.win.w

    def setUp(self):
        # 每个用例从干净初始状态开始（共享窗口实例，避免用例间样式污染）
        self.win.heatmap_frame.setUpdatesEnabled(False)
        for row in self.win.heatmap_btn_list:
            for btn in row:
                btn.setText("")
                btn.setStyleSheet(self.win._HM_BASE)
        self.win.heatmap_frame.setUpdatesEnabled(True)
        self.win._hm_last = {
            id(b): (self.win._HM_BASE, "")
            for row in self.win.heatmap_btn_list for b in row
        }

    def test_grid_built_with_transparent_base(self):
        self.assertEqual(len(self.win.heatmap_btn_list), self.h)
        self.assertEqual(len(self.win.heatmap_btn_list[0]), self.w)
        base = self.win.heatmap_btn_list[0][0].styleSheet()
        self.assertIn("rgba(208, 208, 214, 100)", base)   # 默认浅灰半透明
        self.assertIn("rgb(255, 255, 255)", base)         # 白字

    def test_frosted_glass_styles(self):
        self.assertIn("rgba(44, 44, 44, 170)", self.win.heatmap_frame.styleSheet())
        self.assertIn("border-radius: 10px", self.win.heatmap_frame.styleSheet())
        s = self.win.heatmap_label_stats.styleSheet()
        self.assertIn("rgb(255, 255, 255)", s)            # 纯白文字
        self.assertIn("border-radius: 8px", s)

    def test_open_cells_show_number_without_color(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        cv[1, 1] = 5                                       # 已开数字
        cv[2, 1] = 10                                      # 推理确定的雷
        prob = {(i, j): 0.5 for i in range(2, self.w + 1)
                for j in range(2, self.h + 1)}             # (1,*) 无概率
        payload = {"prob": prob, "best": None, "total": 12.0,
                   "cell_value": cv, "played": 2, "win": 1}
        self.win.update_heatmap(payload)
        btn_num = self.win.heatmap_btn_list[0][0]          # cv[1,1]=5
        self.assertEqual(btn_num.text(), "5")              # 数字正常显示
        self.assertIn("rgba(208, 208, 214, 100)", btn_num.styleSheet())  # 不被概率色遮挡
        btn_mine = self.win.heatmap_btn_list[1][0]         # cv[2,1]=10
        self.assertIn("rgba(255, 0, 0", btn_mine.styleSheet())           # 推理雷红底

    def test_probability_colors_and_best_frame(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        prob = {(1, 1): 0.001, (2, 1): 0.995}
        payload = {"prob": prob, "best": (2, 1), "total": 3.0,
                   "cell_value": cv, "played": 1, "win": 0}
        self.win.update_heatmap(payload)
        # 坐标：键 (i+1, j+1) → btn[j][i]；键 (1,1)→btn[0][0]，键 (2,1)→btn[0][1]
        self.assertIn("rgba(255, 0, 0", self.win.heatmap_btn_list[0][0].styleSheet())
        self.assertIn("rgba(0, 255, 0", self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertIn("border: 3px", self.win.heatmap_btn_list[0][1].styleSheet())

    def test_stats_line_content(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        payload = {"prob": {}, "best": None, "total": 0.0, "cell_value": cv,
                   "played": 7, "win": 3}
        self.win.update_heatmap(payload)
        t = self.win.heatmap_label_stats.text()
        self.assertIn("已完局: 7", t)
        self.assertIn("赢局: 3", t)
        self.assertIn("胜率: 42.86%", t)

    def test_style_dedup_skips_unchanged(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        payload = {"prob": {}, "best": None, "total": 0.0, "cell_value": cv,
                   "played": 3, "win": 1}
        self.win.update_heatmap(payload)                   # 首渲：填充缓存
        before = dict(self.win._hm_last)
        self.win.update_heatmap(payload)                   # 相同 payload：缓存应零变化
        self.assertEqual(self.win._hm_last, before)

    def test_clear_payload_renders_white_transparent(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        prob = {(i, j): 0.5 for i in range(1, self.w + 1)
                for j in range(1, self.h + 1)}
        self.win.update_heatmap({"prob": prob, "best": None, "total": 5.0,
                                 "cell_value": cv, "played": 1, "win": 1})
        self.win.update_heatmap({"prob": {}, "best": None, "total": 0.0,
                                 "cell_value": cv, "played": 2, "win": 1})
        btn = self.win.heatmap_btn_list[0][0]
        # 概率清空后：未开无概率格 → 白色半透明（用户设定的无数据格样式）
        self.assertIn("rgba(255, 255, 255, 180)", btn.styleSheet())
        self.assertEqual(btn.text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
