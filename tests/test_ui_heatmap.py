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
        """统计栏文案契约 = main.py:776 的「当前格非雷的概率」。

        ⚠️ 文案变更出处（勿再反向改 main.py 迁就本测试）：提交 cfb27da
        （2026-09-15，提交信息原文「…状态栏文案改为『当前格非雷的概率』」）
        已把文案定稿为「当前格非雷的概率」；旧断言「当前点击方格不是雷的
        概率」是未同步的历史断言（review/代码质量域.md 的 F5、
        review/UI交互域.md:281 均记录同一结论）。

        判据语义保持不变：仍校验 87.50% 数值、以及整行「已完局/赢局/胜率」
        三段结构；只同步文案措辞，不放宽任何断言。
        """
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        payload = {"prob": {}, "best": None, "best_conf": 0.875, "total": 0.0,
                   "cell_value": cv, "played": 7, "win": 3}
        self.win.update_heatmap(payload)
        t = self.win.heatmap_label_stats.text()
        self.assertIn("当前格非雷的概率: 87.50%", t)
        self.assertIn("已完局: 7", t)
        self.assertIn("赢局: 3", t)
        self.assertIn("胜率: 42.86%", t)
        # 整行字段顺序与分隔符（防止只改文案时把结构改坏）
        self.assertGreaterEqual(len(t.split("｜")), 4)
        self.assertNotIn("当前点击方格不是雷的概率", t)  # 旧文案必须已消失

    def test_style_dedup_skips_unchanged(self):
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        payload = {"prob": {}, "best": None, "total": 0.0, "cell_value": cv,
                   "played": 3, "win": 1}
        self.win.update_heatmap(payload)                   # 首渲：填充缓存
        before = dict(self.win._hm_last)
        self.win.update_heatmap(payload)                   # 相同 payload：缓存应零变化
        self.assertEqual(self.win._hm_last, before)

    def test_computing_phase_keeps_previous_heatmap(self):
        """win-rate 前瞻开始时的 phase=computing 帧不得清空上一帧热力图。"""
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
            "total": 40.0, "cell_value": cv, "played": 3, "win": 1,
            "phase": "decision"})
        before = (self.win.heatmap_btn_list[0][1].text(),
                  self.win.heatmap_btn_list[0][1].styleSheet())
        # C++ 进入 win-rate 前瞻：只发状态、没有新 prob/best
        self.win.update_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cv, "played": 3, "win": 1, "phase": "computing"})
        after = (self.win.heatmap_btn_list[0][1].text(),
                 self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertEqual(before, after,
                         "computing 帧必须保留上一帧真实概率色与 best 蓝框")
        self.assertIn("正在计算下一步", self.win.heatmap_label_stats.text())

    def test_repeated_empty_middle_frames_keep_heatmap(self):
        """win-rate 期间可连续多帧「无新估值」；热力图必须始终保留。"""
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
            "total": 40.0, "cell_value": cv, "played": 3, "win": 1,
            "phase": "decision", "clear": False})
        for i in range(4):
            self.win.update_heatmap({
                "prob": {}, "best": None, "best_conf": None, "total": 0.0,
                "cell_value": cv, "played": 3, "win": 1,
                "phase": "rescanned", "clear": False})
            style = self.win.heatmap_btn_list[0][1].styleSheet()
            self.assertIn("border: 3px", style,
                          f"第 {i + 1} 个空中间帧不得丢掉 best 蓝框")
            self.assertIn("rgba(0, 255, 0", style,
                          f"第 {i + 1} 个空中间帧不得丢掉概率色")

    def test_explicit_clear_frame_drops_heatmap(self):
        """显式 clear=True（新局/停止）必须真的清掉概率层。"""
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
            "total": 40.0, "cell_value": cv, "played": 3, "win": 1,
            "phase": "decision", "clear": False})
        self.assertIn("rgba(0, 255, 0",
                      self.win.heatmap_btn_list[0][1].styleSheet())
        self.win.update_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cv, "played": 4, "win": 2,
            "phase": "ready", "clear": True})
        self.assertNotIn("border: 3px",
                         self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertIn("rgba(255, 255, 255, 180)",
                      self.win.heatmap_btn_list[0][1].styleSheet())

    def test_computing_phase_without_previous_keeps_screen(self):
        """无历史估值时 computing 帧不得把整盘刷回灰色（win-rate 消失根因）。"""
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
            "total": 40.0, "cell_value": cv, "played": 1, "win": 0})
        before = (self.win.heatmap_btn_list[0][1].text(),
                  self.win.heatmap_btn_list[0][1].styleSheet())
        self.win._last_heatmap_payload = None
        self.win.update_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cv, "played": 1, "win": 0, "phase": "computing"})
        after = (self.win.heatmap_btn_list[0][1].text(),
                 self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertEqual(before, after,
                         "无历史估值时 computing 帧不得重绘成整盘灰色")

    def test_reload_rebuilds_grid_when_w_h_change(self):
        """进程内改 cfg 的 W/H 后，reload() 必须重建自动页热力图网格。

        用户实测 2026-11-08「修改 W 和 H 的值之后，自动界面的热力图的
        长宽没有相应的改变」。根因：网格只在 __init__ 按初始尺寸建一次，
        reload() 只更新数值不重建；_render_heatmap_grid 用
        min(self.h, len(btn_list)) 保护 ⇒ 新尺寸下多出的格子根本不存在。
        """
        from unittest import mock
        import json
        import setting

        win = main.MyMainWindow()
        try:
            orig_w, orig_h = win.w, win.h
            self.assertEqual(len(win.heatmap_btn_list), orig_h)
            self.assertEqual(len(win.heatmap_btn_list[0]), orig_w)

            changed = dict(setting.load_cfg(), w=orig_w + 5, h=orig_h + 3)
            with mock.patch.object(setting, "load_cfg", return_value=changed):
                win.reload()
            self.assertEqual(win.w, orig_w + 5)
            self.assertEqual(win.h, orig_h + 3)
            self.assertEqual(len(win.heatmap_btn_list), orig_h + 3)
            self.assertEqual(len(win.heatmap_btn_list[0]), orig_w + 5)
            self.assertEqual(win.heatmap_frame.minimumHeight(),
                             (orig_h + 3 + 1) * win.heatmap_btn_width)

            # 改回原值也要能缩回去
            restored = dict(changed, w=orig_w, h=orig_h)
            with mock.patch.object(setting, "load_cfg", return_value=restored):
                win.reload()
            self.assertEqual(len(win.heatmap_btn_list), orig_h)
            self.assertEqual(len(win.heatmap_btn_list[0]), orig_w)
        finally:
            win.deleteLater()

    def test_clear_payload_renders_white_transparent(self):
        """显式 clear=True 的空 payload 才清空概率色（新局/停止语义）。"""
        cv = np.full((self.h + 2, self.w + 2), 9, dtype="int32")
        prob = {(i, j): 0.5 for i in range(1, self.w + 1)
                for j in range(1, self.h + 1)}
        self.win.update_heatmap({"prob": prob, "best": None, "total": 5.0,
                                 "cell_value": cv, "played": 1, "win": 1,
                                 "clear": False})
        self.win.update_heatmap({"prob": {}, "best": None, "total": 0.0,
                                 "cell_value": cv, "played": 2, "win": 1,
                                 "clear": True})
        btn = self.win.heatmap_btn_list[0][0]
        # 概率清空后：未开无概率格 → 白色半透明（用户设定的无数据格样式）
        self.assertIn("rgba(255, 255, 255, 180)", btn.styleSheet())
        self.assertEqual(btn.text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
