# -*- coding: utf-8 -*-
"""UI 迁移 B+C 接线 offscreen 回归（main.py ↔ ui/preview_popup.py）。

覆盖任务书要求的四类断言：
  1. 主窗口 offscreen 可实例化（且不依赖手改 ui/window.py 生成物）；
  2. B 批状态条 / 决策卡 / 图例控件存在且可被真实 payload 驱动；
  3. A 批 popup 接线（hover + focus + close 三通道）不报错、真实 cell_value 传入；
  4. C 批：无 transitions 时显示「暂无真实状态转移分布」占位，且禁止合成概率；
     三个 value_kind（exact_safety / uniform_prior / heuristic_win_rate）互相区分。

坐标约定（与 main.py:1024-1025 一致）：payload["prob"] 与 pos 为 1-based
(i, j)；heatmap_btn_list[j][i] / cell_value[j+1][i+1] 为 0-based 落点。

运行：QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui_migration -v
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5 import QtCore, QtWidgets  # noqa: E402
import numpy as np  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import main  # noqa: E402
from ui.preview_popup import (  # noqa: E402
    NO_TRANSITION_HINT,
    NO_TRANSITION_TEXT,
    PreviewPopup,
    summarize_payload,
)


def _board(h=16, w=30):
    """真实编码契约的棋盘：0-8 已开数字 / 9 未开 / 10 推理雷 / 11 推理安全。"""
    cv = np.full((h + 2, w + 2), 9, dtype="int32")
    cv[1, 1] = 4
    cv[2, 1] = 10
    cv[3, 3] = 11
    return cv


class TestMainWindowInstantiation(unittest.TestCase):
    """① 主窗口能在 offscreen 下实例化（B 批运行期注入不破坏构造链）。"""

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()

    def test_window_constructs_offscreen(self):
        self.assertIsNotNone(self.win)
        self.assertEqual(self.win.stackedWidget.count(), 3)

    def test_injected_widgets_exist(self):
        for attr in ("status_card", "status_label", "decision_card",
                     "decision_body", "_decision_value", "_decision_kind",
                     "legend_card", "legend_body", "candidate_card",
                     "candidate_list", "help_legend_card", "help_candidate_card",
                     "help_candidate_list"):
            self.assertTrue(hasattr(self.win, attr), f"缺少运行期注入控件 {attr}")

    def test_generated_ui_not_patched(self):
        """紧凑 UI 全部运行期注入：生成物页数/对象名保持不变。

        ⚠️ 断言载体变更留痕（2026-11-07 遮挡修复）：原断言为
        ``verticalLayout_2.count() >= 6``——它当时是「卡片确实被插进 page_2
        页面级布局」的代理。修复后 page_2 的页面级 ``verticalLayout_2`` 只保留
        「滚动区 + 终止/返回按钮 + 进度条」，卡片全部移入滚动内容
        ``verticalLayout_auto_content``（否则内容超高会把棋盘/热力图压到重叠，
        正是本次要修的真实遮挡）。**判据语义不变**：仍是"注入的卡片存在且不在
        生成物里"，只把测量点从页面级布局换成真正承载卡片的内容布局。

        ⚠️ 二次变更留痕（2026-11-07 统一帮助棋盘）：帮助页**删除了第二套
        热力图网格**（``help_heatmap_frame`` 整块移除，只保留一条纯文本统计栏
        ``help_board_label_stats``），因此帮助页内容布局项数从 6 降为 5
        （说明块 + 棋盘 + 统计栏 + 帮助图例 + 候选详情）。判据语义仍不变——
        测的是「注入控件在运行期布局里、不在生成物里」，这里把下界同步为新的
        真实项数，并额外断言**不存在第二个棋盘网格**（本任务的核心约束）。
        """
        self.assertIsNone(
            self.win.findChild(QtWidgets.QWidget, "uiCardCompactPageMark"))
        # 页面级布局：滚动区 + 两个按钮 + 进度条（plainTextEdit 运行期被移除）
        self.assertGreaterEqual(self.win.verticalLayout_2.count(), 4)
        # 内容布局：热力图 + 统计栏 + 4 张卡片 = 6 项
        self.assertGreaterEqual(self.win.verticalLayout_auto_content.count(), 6)
        # 帮助页：说明块 + 唯一棋盘 + 统计栏 + 图例卡 + 候选卡 = 5 项
        self.assertGreaterEqual(self.win.verticalLayout_help_content.count(), 5)
        # 帮助页不得再有第二套热力图网格（2026-11-07 统一棋盘的核心约束）
        self.assertFalse(hasattr(self.win, "help_heatmap_btn_list"),
                         "帮助页第二套热力图网格必须已删除")
        self.assertIsNone(self.win.findChild(QtWidgets.QWidget, "helpHeatmapFrame"),
                          "帮助页不得存在第二个棋盘容器")


class TestStatusDecisionLegend(unittest.TestCase):
    """② B 批：状态条 / 决策卡 / 图例存在且由真实 payload 驱动。"""

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()

    def setUp(self):
        self.win._last_heatmap_payload = None
        self.win._last_cell_value = None
        self.win._last_candidates = []

    def test_status_card_ready_text(self):
        self.win.set_solver_status("就绪 · 测试")
        self.assertIn("就绪", self.win.status_label.text())

    def test_legend_lists_five_semantics(self):
        text = self.win.legend_body.text()
        for token in ("已证明安全", "已证明是雷", "概率估计", "推荐但未证明", "无数据"):
            self.assertIn(token, text, f"图例缺少语义项 {token}")
        self.assertGreaterEqual(len(text.split("■")) - 1, 5)

    def test_decision_card_from_real_heatmap_payload(self):
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        payload = {"prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
                   "total": 12.0, "cell_value": cv, "played": 4, "win": 2}
        self.win.update_heatmap(payload)
        self.assertIn("(2, 1)", self.win.decision_body.text())
        self.assertIn("99.50%", self.win._decision_value.text())
        self.assertIn("安全概率", self.win._decision_kind.text())

    def test_status_card_tracks_auto_run(self):
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_heatmap({"prob": {}, "best": None, "best_conf": None,
                                 "total": 0.0, "cell_value": cv,
                                 "played": 8, "win": 4})
        status = self.win.status_label.text()
        self.assertIn("已完局 8", status)
        self.assertIn("胜率 50.00%", status)

    def test_candidate_list_renders_real_pos_dict_list(self):
        pos_dict_list = [
            {"pos": (2, 1), "confidence": 1.0, "num": 1, "is_mine": False,
             "is_best": True, "exp": "枚举得出", "is_recommend": False},
            {"pos": (4, 4), "confidence": 0.62, "num": 2, "is_mine": False,
             "is_best": False, "exp": "胜率计算得出", "is_recommend": True},
        ]
        self.win.update_btn_list(pos_dict_list)
        self.assertEqual(self.win.help_candidate_list.count(), 2)
        first = self.win.help_candidate_list.item(0).text()
        second = self.win.help_candidate_list.item(1).text()
        self.assertIn("安全概率（精确解）", first)
        self.assertIn("策略胜率（启发式，非安全概率）", second)
        self.assertEqual(self.win._last_candidates, pos_dict_list)

    def test_candidate_list_three_kinds_in_one_batch(self):
        """≥3 元素输入：三类估值在同一 batch 里各自落到正确文案，不得串味。"""
        pos_dict_list = [
            {"pos": (2, 1), "confidence": 0.999, "num": 1, "is_mine": False,
             "is_best": True, "exp": "由(3, 1)得出", "is_recommend": False},
            {"pos": (4, 4), "confidence": 0.71, "num": 2, "is_mine": False,
             "is_best": False, "exp": "随机选择。", "is_recommend": False},
            {"pos": (6, 6), "confidence": 0.62, "num": 3, "is_mine": False,
             "is_best": False, "exp": "胜率计算得出", "is_recommend": True},
        ]
        self.win.update_btn_list(pos_dict_list)
        texts = [self.win.help_candidate_list.item(i).text()
                 for i in range(self.win.help_candidate_list.count())]
        self.assertEqual(len(texts), 3)
        self.assertIn("安全概率（精确解）", texts[0])
        self.assertIn("均匀先验（非精确解）", texts[1])
        self.assertIn("策略胜率（启发式，非安全概率）", texts[2])
        # 三者文案互不相同（区分度），且胜率项不得出现安全概率字样
        self.assertEqual(len(set(texts)), 3)
        self.assertNotIn("安全概率（精确解）", texts[2])


class TestPopupWiring(unittest.TestCase):
    """③ A 批接线：hover / focus / close 三通道挂接成功且不报错。"""

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()

    def setUp(self):
        btn = QtWidgets.QPushButton("")
        self.btn = btn

    def tearDown(self):
        self.win._hide_preview_popup()

    def _candidate(self):
        return {"pos": (5, 5), "confidence": 0.42, "num": 1,
                "is_mine": False, "is_best": False,
                "exp": "胜率计算得出", "is_recommend": True}

    def test_attach_preview_refreshes_provider_for_reused_button(self):
        """同一按钮跨轮复用时必须换成最新候选 provider。"""
        first = {"pos": (2, 2), "confidence": 0.7, "exp": "第一轮"}
        second = {"pos": (4, 4), "confidence": 0.8, "exp": "第二轮"}
        self.win._attach_preview(
            self.btn,
            lambda _button: self.win._preview_payload_for_candidate(first),
        )
        self.win._attach_preview(
            self.btn,
            lambda _button: self.win._preview_payload_for_candidate(second),
        )
        payload = self.btn._preview_filter.payload_provider(self.btn)
        self.assertEqual(payload["cell"], (4, 4))

    def test_attach_preview_installs_both_channels(self):
        provider = lambda _o: self.win._preview_payload_for_candidate(
            self._candidate())
        self.win._attach_preview(self.btn, provider)
        self.assertIsNotNone(self.win._preview_popup, "浮层应已懒建")
        self.assertIsInstance(self.win._preview_popup, PreviewPopup)
        self.assertTrue(hasattr(self.btn, "_preview_filter"), "缺 hover 过滤器")
        self.assertTrue(hasattr(self.btn, "_preview_focus_filter"), "缺 focus 过滤器")
        self.assertIs(self.btn._preview_focus_filter.popup,
                      self.win._preview_popup)
        # 同一按钮重复挂接必须幂等（否则 hover 会重复弹多层）
        filters_before = self.btn._preview_focus_filter
        self.win._attach_preview(self.btn, provider)
        self.assertIs(self.btn._preview_focus_filter, filters_before)

    def test_hover_channel_shows_popup_with_real_board(self):
        self.win._last_cell_value = _board()
        provider = lambda _o: self.win._preview_payload_for_candidate(
            self._candidate())
        self.win._attach_preview(self.btn, provider)
        filt = self.btn._preview_filter
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.win._preview_popup.isVisible(), "hover 应弹出浮层")
        self.assertIn("策略胜率", self.win._preview_popup.value_label.text())
        # 真实 cell_value 传入 ⇒ 7×7 中心格是 (5,5) 的 9（未开）
        self.assertEqual(self.win._preview_popup._current["cell"], (5, 5))
        self.assertEqual(self.win._preview_popup._current["cell_value"].shape,
                         _board().shape)
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertFalse(self.win._preview_popup.isVisible())

    def test_focus_channel_shows_and_closes(self):
        """纯键盘路径：FocusIn 弹出、FocusOut 关闭（无 Enter 事件）。"""
        self.win._last_cell_value = _board()
        provider = lambda _o: self.win._preview_payload_for_candidate(
            self._candidate())
        self.win._attach_preview(self.btn, provider)
        ffilt = self.btn._preview_focus_filter
        ffilt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.FocusIn))
        self.assertTrue(self.win._preview_popup.isVisible(), "FocusIn 应弹出浮层")
        ffilt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.FocusOut))
        self.assertFalse(self.win._preview_popup.isVisible(), "FocusOut 应关闭浮层")

    def test_focus_channel_survives_bad_provider(self):
        def boom(_o):
            raise RuntimeError("provider 崩了")
        self.win._attach_preview(self.btn, boom)
        ffilt = self.btn._preview_focus_filter
        ffilt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.FocusIn))
        self.assertFalse(self.win._preview_popup.isVisible(),
                         "取数失败时宁可不显示，且不得抛出")

    def test_close_popup_idempotent(self):
        self.win._hide_preview_popup()
        self.win._hide_preview_popup()  # 未建浮层时也必须安全
        self.win._last_cell_value = _board()
        self.win._attach_preview(
            self.btn, lambda _o: self.win._preview_payload_for_candidate(
                self._candidate()))
        self.btn._preview_filter.eventFilter(
            self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.win._preview_popup.isVisible())
        self.win._hide_preview_popup()
        self.assertFalse(self.win._preview_popup.isVisible())

    def test_page_switch_closes_popup(self):
        self.win._last_cell_value = _board()
        self.win._attach_preview(
            self.btn, lambda _o: self.win._preview_payload_for_candidate(
                self._candidate()))
        self.btn._preview_filter.eventFilter(
            self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.win._preview_popup.isVisible())
        self.win.stackedWidget.setCurrentIndex(2)   # currentChanged → close
        self.assertFalse(self.win._preview_popup.isVisible(),
                         "切页必须关掉顶层浮层")

    def test_heatmap_cell_wiring_uses_real_prob(self):
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_heatmap({"prob": {(3, 4): 0.31}, "best": (3, 4),
                                 "best_conf": 0.31, "total": 5.0,
                                 "cell_value": cv, "played": 1, "win": 0})
        btn = self.win.heatmap_btn_list[3][2]     # (i,j)=(3,4) → btn[j-1][i-1]
        self.assertIn(btn, self.win._preview_buttons)
        payload = self.win._preview_payload_for_heatmap_cell(3, 4)
        self.assertEqual(payload["cell"], (3, 4))
        self.assertAlmostEqual(payload["value"], 0.31)
        self.assertIsNotNone(payload["cell_value"])
        self.assertIsNone(payload["transitions"])


class TestValueKindDistinction(unittest.TestCase):
    """④ C 批：三个 value_kind 真实区分 + 无 transitions 占位。"""

    def test_classify_value_kind_covers_all_real_emitters(self):
        # utils/probability.py 与 utils/deduction.py 的真实 exp 文案
        cases = {
            "C++ 三层决策 (certain_safe)": "exact_safety",
            "枚举得出": "exact_safety",
            "枚举得出, 预计可以确定的方格数：1.0": "exact_safety",
            "由(3, 4)得出": "exact_safety",
            "胜率计算得出": "heuristic_win_rate",
            "随机选择。": "uniform_prior",
            "无法归类的新文案": "unknown",
            None: "unknown",
        }
        for exp, expected in cases.items():
            self.assertEqual(main.classify_value_kind(exp), expected, f"exp={exp!r}")

    def test_heatmap_kind_empty_prob_is_unknown(self):
        self.assertEqual(main.classify_heatmap_kind({"prob": {}}), "unknown")
        self.assertEqual(main.classify_heatmap_kind({"prob": None}), "unknown")
        self.assertEqual(main.classify_heatmap_kind(None), "unknown")

    def test_heatmap_kind_scales_with_prob_presence(self):
        self.assertEqual(main.classify_heatmap_kind({"prob": {(1, 1): 0.5}}),
                         "exact_safety")

    def test_heatmap_random_branch_is_uniform_prior(self):
        """随机分支（probability.py:718-741）prob 为空但有 best ⇒ 均匀先验。"""
        self.assertEqual(
            main.classify_heatmap_kind({"prob": {}, "best": (2, 3),
                                        "best_conf": 0.71}),
            "uniform_prior")

    def test_payload_explicit_kind_wins(self):
        self.assertEqual(
            main.classify_payload_kind({"prob": {}, "value_kind": "heuristic_win_rate"}),
            "heuristic_win_rate")

    def test_decision_kind_pbs_path_is_exact(self):
        """best_conf 与 prob[best] 同值 ⇒ pbs/枚举路径 ⇒ 安全概率。"""
        payload = {"prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995}
        self.assertEqual(main.classify_decision_value_kind(payload), "exact_safety")

    def test_decision_kind_win_rate_path_is_heuristic(self):
        """best_conf 是 win_rate[arg]，与 prob[best]（clicks2p 安全概率）异值。"""
        payload = {"prob": {(2, 1): 0.88}, "best": (2, 1), "best_conf": 0.62}
        self.assertEqual(main.classify_decision_value_kind(payload),
                         "heuristic_win_rate")

    def test_decision_kind_random_branch_is_uniform_prior(self):
        payload = {"prob": {}, "best": (2, 1), "best_conf": 0.71}
        self.assertEqual(main.classify_decision_value_kind(payload),
                         "uniform_prior")

    def test_decision_kind_best_outside_prob_is_uniform_prior(self):
        """:1399 的 _confidence 兜底：prob 有值但 best 不在 prob 中。"""
        payload = {"prob": {(1, 1): 0.5}, "best": (7, 7), "best_conf": 0.64}
        self.assertEqual(main.classify_decision_value_kind(payload),
                         "uniform_prior")

    def test_decision_kind_missing_value_is_unknown(self):
        for payload in ({"prob": {(1, 1): 0.5}, "best": None, "best_conf": None},
                        {"prob": {(1, 1): 0.5}, "best": (1, 1)},
                        None):
            self.assertEqual(main.classify_decision_value_kind(payload), "unknown")

    def test_heatmap_decision_card_labels_win_rate_honestly(self):
        """端到端：win_rate 形态 payload 驱动决策卡时不得写成安全概率。"""
        app_win = main.MyMainWindow()
        cv = np.full((app_win.h + 2, app_win.w + 2), 9, dtype="int32")
        payload = {"prob": {(2, 1): 0.88}, "best": (2, 1), "best_conf": 0.62,
                   "total": 40.0, "cell_value": cv, "played": 3, "win": 1}
        app_win.update_heatmap(payload)
        kind_text = app_win._decision_kind.text()
        self.assertIn("策略胜率", kind_text)
        self.assertIn("非安全概率", kind_text)
        self.assertNotIn("安全概率（精确解）", kind_text)
        self.assertIn("62.00%", app_win._decision_value.text())
        app_win.deleteLater()

    def test_preview_payload_uses_hovered_cell_transitions(self):
        """状态转移必须是点击「悬停格」之后的，不能共用 best_cell 那份。

        用户第三次指出「当前悬浮窗展示的不是点击悬浮在那个格子上之后的
        转移概率」。根因：三个 provider 全从 _last_heatmap_payload 取同一份
        transitions（decide 针对 best_cell 算的）。修复后按 (i,j) 调
        native.transitions_for，并按格缓存。
        """
        from unittest import mock
        import numpy as np
        from utils import native

        win = main.MyMainWindow()
        try:
            cv = np.full((win.h + 2, win.w + 2), 9, dtype="int32")
            win.update_help_heatmap({
                "prob": {(2, 1): 0.9, (3, 1): 0.8},
                "best": (9, 9), "best_conf": 0.9,
                "cell_value": cv, "played": 0, "win": 0,
                "phase": "decision", "clear": False,
                # 这份是 best_cell 的，故意与按格结果不同
                "transitions": [{"probability": 0.99, "label": "BEST",
                                 "board": [[9] * 7 for _ in range(7)]}],
                "transition_meta": {"basis": "exact_worlds",
                                    "p_click_safe": 0.99,
                                    "world_count": 1},
            })
            calls = []

            def fake(cv_, a, w, h, x, y):
                calls.append((x, y))
                b = [[9] * 7 for _ in range(7)]
                b[3][3] = 1
                return {
                    "transitions": [{"probability": 0.4 + 0.1 * x,
                                     "label": "X", "board": b,
                                     "new_cells": []}],
                    "transition_meta": {
                        "basis": "exact_worlds", "p_click_safe": 1.0,
                        "world_count": 7, "cell": (x, y)},
                    "reason": None,
                }

            with mock.patch.object(native, "has_transitions_for",
                                   return_value=True), \
                    mock.patch.object(native, "transitions_for",
                                      side_effect=fake):
                p1 = win._preview_payload_for_heatmap_cell(2, 1)
                p2 = win._preview_payload_for_heatmap_cell(3, 1)
                # 同一格再取一次：应命中缓存，不重复调用 C++
                p3 = win._preview_payload_for_heatmap_cell(2, 1)

            self.assertEqual(calls, [(2, 1), (3, 1)],
                             "不同格各算一次、同格命中缓存")
            # 关键：转移不是 payload 里 best_cell 那份 0.99
            self.assertNotAlmostEqual(
                float(p1["transitions"][0]["probability"]), 0.99)
            self.assertEqual(p1["transition_meta"]["cell"], (2, 1))
            self.assertEqual(p2["transition_meta"]["cell"], (3, 1))
            self.assertEqual(p3["transitions"], p1["transitions"])
            # 候选格 provider 同样按格取数
            with mock.patch.object(native, "has_transitions_for",
                                   return_value=True), \
                    mock.patch.object(native, "transitions_for",
                                      side_effect=fake):
                pc = win._preview_payload_for_candidate({
                    "pos": (2, 1), "confidence": 0.9, "num": 1,
                    "is_mine": False, "is_best": True,
                    "exp": "C++ 三层决策 (win_rate)", "is_recommend": True})
            self.assertEqual(pc["transition_meta"]["cell"], (2, 1))
        finally:
            win.deleteLater()

    def test_preview_payload_never_synthesizes_transitions(self):
        win = main.MyMainWindow()
        pos_dict = {"pos": (4, 4), "confidence": 0.62, "num": 2,
                    "is_mine": False, "is_best": False,
                    "exp": "胜率计算得出", "is_recommend": True}
        payload = win._preview_payload_for_candidate(pos_dict)
        self.assertIsNone(payload["transitions"],
                          "现有 solver 无真实转移分布 ⇒ 必须 None")
        info = summarize_payload(payload)
        self.assertFalse(info["has_transitions"])
        self.assertEqual(info["transition_text"], NO_TRANSITION_TEXT)
        self.assertEqual(info["transition_rows"], [])
        win.deleteLater()

    def test_popup_renders_no_transition_placeholder(self):
        popup = PreviewPopup()
        popup.set_payload({
            "cell": (5, 5), "value": 0.62,
            "value_kind": "heuristic_win_rate",
            "source": "胜率计算得出", "cell_value": _board(),
            "transitions": None,
        })
        self.assertEqual(popup.transition_label.text(), NO_TRANSITION_TEXT)
        self.assertEqual(popup.note_label.text(), NO_TRANSITION_HINT)
        self.assertIn("策略胜率", popup.value_label.text())
        popup.close_popup()
        popup.deleteLater()

    def test_popup_placeholder_never_contains_percentages(self):
        """占位文案里不得出现任何百分数（= 不得偷偷合成分支概率）。"""
        popup = PreviewPopup()
        popup.set_payload({"cell": (5, 5), "value": None,
                           "value_kind": "unknown", "cell_value": _board()})
        self.assertNotIn("%", popup.transition_label.text())
        popup.close_popup()
        popup.deleteLater()


class TestAssertionDiscrimination(unittest.TestCase):
    """断言鉴别力条款：分类断言必须能区分「标错类型」与「标对类型」。"""

    def test_kind_texts_are_mutually_discriminating(self):
        """三个 kind 的文案两两不同，且「策略胜率」不含安全概率字样。"""
        texts = [main.VALUE_KIND_TEXT[k] for k in
                 ("exact_safety", "uniform_prior", "heuristic_win_rate")]
        self.assertEqual(len(set(texts)), 3)
        self.assertNotIn("安全概率（精确解）", main.VALUE_KIND_TEXT["heuristic_win_rate"])
        self.assertIn("非安全概率",
                      main.VALUE_KIND_TEXT["heuristic_win_rate"])

    def test_mislabel_mutant_is_caught(self):
        """注入必红：把 win_rate 错标成 exact_safety，断言必须失败。"""
        correct = main.classify_value_kind("胜率计算得出")
        self.assertEqual(correct, "heuristic_win_rate")
        # 变异体：假装分类器把胜率标成安全概率
        mutant = "exact_safety"
        with self.assertRaises(AssertionError):
            self.assertEqual(mutant, correct, "鉴别力自检：标错必须被断言挡住")

    def test_label_text_mutant_is_caught(self):
        """注入必红：若胜率文案被写成安全概率，文案契约断言必须失败。"""
        mutant_text = "安全概率（精确解） 62.00%"
        with self.assertRaises(AssertionError):
            self.assertIn("非安全概率", mutant_text)


class TestCancelAndThreadsUntouched(unittest.TestCase):
    """保持现有线程：批 B/C 只叠加 UI，不改 Solver 线程连接。"""

    def test_solver_threads_still_present(self):
        win = main.MyMainWindow()
        self.assertTrue(hasattr(win, "auto_play_thread"))
        self.assertTrue(hasattr(win, "help_thread"))
        self.assertEqual(win.auto_play_thread.is_play, True)
        self.assertEqual(win.help_thread.is_play, False)  # set_args(0) = 帮助模式
        win.deleteLater()


# ===========================================================================
# 2026-11-07 遮挡修复回归：offscreen geometry 不相交 + 帮助热力图真实刷新
# ===========================================================================
def _pump(win, n=10):
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        app.processEvents()
    page = win.stackedWidget.currentWidget()
    if page.layout() is not None:
        page.layout().activate()


def _global_rect(widget_or_widgets, parent, cell_px=0):
    """控件（或按钮网格）相对 ``parent`` 的包围矩形（QRect，含 cell_px 尺寸）。"""
    if isinstance(widget_or_widgets, QtWidgets.QWidget):
        top_left = widget_or_widgets.mapTo(parent, QtCore.QPoint(0, 0))
        return QtCore.QRect(top_left, widget_or_widgets.size())
    pts = [b.mapTo(parent, QtCore.QPoint(0, 0))
           for row in widget_or_widgets for b in row]
    xs = [p.x() for p in pts]
    ys = [p.y() for p in pts]
    return QtCore.QRect(min(xs), min(ys),
                        max(xs) - min(xs) + cell_px,
                        max(ys) - min(ys) + cell_px)


class TestGeometryNoOverlap(unittest.TestCase):
    """真实遮挡回归：切到自动页/帮助页后棋盘区与新增卡片/热力图矩形不得相交。

    这个断言直接对应用户截图症状（帮助页卡片压在棋盘上）。判据用**相对
    页面内容坐标的矩形相交**，不依赖像素渲染（offscreen 无字体，像素比对
    会假红，见 L1/记忆 #1212c38e）。
    """

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()
        cls.win.resize(1035, 800)
        cls.win.show()
        _pump(cls.win, 12)

    def _switch(self, index, scroll_area):
        self.win.stackedWidget.setCurrentIndex(index)
        _pump(self.win, 12)
        # 滚动到顶部，保证测的是"首屏可见区域"下的内容布局
        scroll_area.verticalScrollBar().setValue(0)
        _pump(self.win, 6)
        return self.win.stackedWidget.currentWidget()

    def test_auto_page_cards_do_not_overlap_heatmap(self):
        content = self.win.auto_scroll_content
        self._switch(1, self.win.auto_scroll)
        heat = _global_rect(self.win.heatmap_frame, content)
        self.assertGreater(heat.width(), 0)
        self.assertGreater(heat.height(), 0)
        for name in ("status_card", "decision_card", "legend_card",
                     "candidate_card"):
            card = getattr(self.win, name)
            rect = _global_rect(card, content)
            self.assertFalse(
                rect.intersects(heat),
                f"{name} 与热力图矩形相交：card={rect.getRect()} "
                f"heatmap={heat.getRect()}",
            )
        stats = _global_rect(self.win.heatmap_label_stats, content)
        self.assertFalse(stats.intersects(heat),
                         f"统计栏与热力图相交：{stats.getRect()} vs {heat.getRect()}")

    def test_auto_heatmap_grid_inside_its_frame(self):
        """热力图 480 格必须全部落在自己的容器内（否则溢出的部分会被盖住）。"""
        content = self.win.auto_scroll_content
        self._switch(1, self.win.auto_scroll)
        frame = _global_rect(self.win.heatmap_frame, content)
        grid = _global_rect(self.win.heatmap_btn_list, content,
                            self.win.heatmap_btn_width)
        self.assertTrue(
            frame.contains(grid),
            f"热力图网格溢出容器：grid={grid.getRect()} frame={frame.getRect()}",
        )

    def test_help_page_board_not_covered_by_any_new_widget(self):
        content = self.win.help_scroll_content
        self._switch(2, self.win.help_scroll)
        scroller = getattr(self.win, "_help_board_scroller", None)
        if scroller is not None:
            # 棋盘包在独立滚动容器里：把容器本身的矩形作为"棋盘占位区"，
            # 棋盘内部（可能横向滚动）与其按钮都不得被卡片覆盖。
            board = _global_rect(scroller, content)
            scroller.horizontalScrollBar().setValue(0)
            _pump(self.win, 4)
        else:
            board = _global_rect(self.win.frame_2, content)
        bb = _global_rect(self.win.btn_list, content, self.win.btn_width)
        self.assertTrue(
            board.contains(_global_rect(self.win.frame_2, content)),
            f"棋盘容器溢出占位区：board={board.getRect()} "
            f"frame2={_global_rect(self.win.frame_2, content).getRect()}")
        for name in ("help_legend_card", "help_candidate_card",
                     "help_board_label_stats"):
            rect = _global_rect(getattr(self.win, name), content)
            self.assertFalse(
                rect.intersects(board),
                f"{name} 覆盖棋盘：{rect.getRect()} 与 board={board.getRect()} 相交",
            )
            self.assertFalse(
                rect.intersects(bb),
                f"{name} 覆盖棋盘按钮区：{rect.getRect()} vs {bb.getRect()}",
            )

    def test_help_page_has_single_board_grid(self):
        """帮助页**只有一个可见棋盘网格**（2026-11-07 统一棋盘的核心约束）。

        修改前读数：帮助页存在两套网格——``btn_list``（16×30=480 格，位于
        ``frame_2``）与 ``help_heatmap_btn_list``（16×30=480 格，位于
        ``help_heatmap_frame``），合计 960 个格子按钮、两个棋盘容器。
        修复后：help_heatmap_* 整块删除，只剩 ``btn_list`` 一套 480 格。

        「可见棋盘数量 = 1」的判定不靠属性名猜，而是**枚举帮助页滚动内容下
        所有承载 ≥100 个按钮的容器**——多于一个就说明还有重复棋盘。
        """
        content = self.win.help_scroll_content
        self._switch(2, self.win.help_scroll)
        # ① 属性层面：第二套网格必须不存在
        self.assertFalse(hasattr(self.win, "help_heatmap_btn_list"),
                         "帮助页第二套热力图网格必须已删除")
        self.assertIsNone(self.win.findChild(QtWidgets.QWidget, "helpHeatmapFrame"),
                          "帮助页不得再有第二个棋盘容器")
        # ② 结构层面：内容区里承载大网格（≥100 按钮）的容器只能有 1 个。
        # ⚠️ 判据收窄留痕（2026-11-07 窗口自适应修复）：棋盘被包进
        # QScrollArea 后，``helpBoardScroller``/``qt_scrollarea_viewport``/
        # ``frame_2`` 三者都"包含 ≥100 按钮"，但后两者只是同一套网格的容器
        # 嵌套，不是第二套棋盘。原判据数「包含按钮的容器个数」，会把包裹层
        # 误计成新棋盘（实测 3）。**判据语义不变**：仍是「帮助页只有一套棋盘
        # 网格」——改为只统计**最内层**（其直接子级里不再有 ≥100 按钮容器）
        # 的容器，即真正承载按钮的那一个。
        grids = []
        big_containers = []
        for child in content.findChildren(QtWidgets.QWidget):
            buttons = child.findChildren(QtWidgets.QPushButton)
            if len(buttons) >= 100:
                big_containers.append(child)
                inner_big = [c for c in child.findChildren(QtWidgets.QWidget)
                             if c is not child
                             and len(c.findChildren(QtWidgets.QPushButton)) >= 100]
                if not inner_big:
                    grids.append((child.objectName() or type(child).__name__,
                                  len(buttons)))
        self.assertEqual(
            len(grids), 1,
            f"帮助页最内层棋盘网格数量必须为 1，实测 {len(grids)}：{grids} "
            f"（所有大容器：{[c.objectName() or type(c).__name__ for c in big_containers]}）")
        # 该唯一容器里的按钮 = w×h 格 + w 个行头 + h 个列头（棋盘自带表头）
        expected = self.win.w * self.win.h + self.win.w + self.win.h
        self.assertEqual(grids[0][1], expected,
                         f"唯一棋盘格按钮数应为 格{self.win.w * self.win.h}"
                         f"+表头{self.win.w + self.win.h}= {expected}，"
                         f"实测 {grids[0][1]}")
        # ③ 该唯一网格就是 frame_2 -> btn_list
        self.assertEqual(len(self.win.btn_list), self.win.h)
        self.assertEqual(len(self.win.btn_list[0]), self.win.w)

    def test_fixed_bottom_bar_not_covered(self):
        """页底固定条（终止计算/点击所有确定方格/进度条）不得被滚动内容盖住。"""
        self._switch(2, self.win.help_scroll)
        top = self.win.page_3
        content_rect = _global_rect(self.win.help_scroll, top)
        bar = _global_rect(self.win.frame_3, top)
        self.assertFalse(bar.intersects(content_rect),
                         f"固定按钮条与滚动区相交：bar={bar.getRect()} "
                         f"scroll={content_rect.getRect()}")
        self.assertGreaterEqual(bar.top(), content_rect.bottom())


class TestUpdateImageOrdering(unittest.TestCase):
    """update_image 的调用顺序契约：状态清理必须早于 help_thread.start()。

    回归背景（用户实测 2026-11-08「点帮助后仍卡在等待帮助计算开始」）：
    旧顺序是「先 help_thread.start() → 末尾 _clear_heatmap_state('both')」。
    后台线程几百毫秒内就发出首帧真实 payload，而末尾的清理晚于该帧执行，
    把数据与 _last_candidates 一并抹掉 ⇒ 界面永久退回「等待帮助计算开始…」
    且「点击所有确定方格」没有目标。
    本用例记录真实调用顺序并断言清理在前、start 在后。
    """

    def test_clear_state_happens_before_help_thread_start(self):
        order = []
        win = main.MyMainWindow()

        real_clear = win._clear_heatmap_state
        real_start = win.help_thread.start

        def spy_clear(*a, **k):
            order.append("clear")
            return real_clear(*a, **k)

        def spy_start(*a, **k):
            order.append("start")
            return real_start(*a, **k)

        win._clear_heatmap_state = spy_clear
        win.help_thread.start = spy_start
        win._gui_scan_once = lambda cv: cv

        import PIL.Image as _PImage

        import utils.util as _u
        with mock.patch.object(_u, "minesweeper_run",
                               return_value=1, create=True), \
                mock.patch("main.minesweeper_run", return_value=1), \
                mock.patch("main.ClientToScreen", return_value=(100, 200)), \
                mock.patch("main.win32gui.ShowWindow"), \
                mock.patch("main.set_top_window"), \
                mock.patch("main.time.sleep", lambda *_a: None), \
                mock.patch("main.ImageGrab.grab",
                           return_value=_PImage.new("RGB", (800, 600))):
            win.update_image()

        self.assertIn("clear", order, "update_image 必须执行状态清理")
        self.assertIn("start", order, "update_image 必须启动帮助线程")
        self.assertLess(
            order.index("clear"), order.index("start"),
            f"状态清理必须早于 help_thread.start（实际顺序 {order}）："
            "清理晚于线程首帧会抹掉真实 payload，界面退回「等待帮助计算开始…」")


class TestHelpUnifiedBoard(unittest.TestCase):
    """帮助页**唯一棋盘**必须消费最近 payload 的 cell_value/prob/best/候选。

    断言用"至少 N 格文字或样式发生变化"的表达（不是"渲染没崩"），这样
    「payload 没接上/渲染全灰」的失效会被明确捕获；另加 A→B→空 payload 的
    残留回归（用户硬约束：旧 payload 的颜色与 best 框不得残留）。
    """

    def test_help_entry_stops_auto_thread(self):
        """进入帮助模式必须先停止自动线程（两线程不得争抢同一窗口）。

        回归背景（用户实测 2026-11-08 点帮助后卡住）：update_image 启动帮助
        线程前**没有**停止自动线程，两者同时操作同一个扫雷窗口 —— 自动线程
        持续点击/重扫（日志实测 num9 469→478→435 反复跳、每轮「点击后重扫
        99/99 无法识别」），帮助线程产出的候选与帧随即被覆盖 ⇒ 候选为空、
        按钮置灰、界面卡死。
        """
        win = main.MyMainWindow()
        stopped = []

        def fake_stop(thread, label):
            stopped.append(label)

        win._stop_solver_thread = fake_stop
        # 模拟自动线程正在运行
        win.auto_play_thread.isRunning = lambda: True
        win.help_thread.start = lambda *a, **k: None
        win._gui_scan_once = lambda cv: cv

        import PIL.Image as _PImage
        with mock.patch("main.minesweeper_run", return_value=1), \
                mock.patch("main.ClientToScreen", return_value=(100, 200)), \
                mock.patch("main.win32gui.ShowWindow"), \
                mock.patch("main.set_top_window"), \
                mock.patch("main.time.sleep", lambda *_a: None), \
                mock.patch("main.ImageGrab.grab",
                           return_value=_PImage.new("RGB", (800, 600))):
            win.update_image()
        self.assertIn("自动线程", stopped,
                      "启动帮助模式前必须先停止自动线程（否则两线程争抢同一窗口）")

    def test_auto_entry_stops_help_thread(self):
        """进入自动模式必须先停止帮助线程（与 update_image 对称）。"""
        win = main.MyMainWindow()
        stopped = []
        win._stop_solver_thread = lambda thread, label: stopped.append(label)
        win.help_thread.isRunning = lambda: True
        win.reload = lambda: None
        win.auto_play_thread.set_args = lambda v: None
        win.auto_play_thread.start = lambda *a, **k: None
        with mock.patch("main.minesweeper_run", return_value=1), \
                mock.patch("main.QInputDialog.getInt",
                           return_value=(1, True)):
            win.auto_play_func()
        self.assertIn("帮助线程", stopped,
                      "启动自动模式前必须先停止帮助线程（模式互斥）")


class TestHelpUnifiedBoard(unittest.TestCase):
    """帮助页**唯一棋盘**必须消费最近 payload 的 cell_value/prob/best/候选。

    断言用"至少 N 格文字或样式发生变化"的表达（不是"渲染没崩"），这样
    「payload 没接上/渲染全灰」的失效会被明确捕获；另加 A→B→空 payload 的
    残留回归（用户硬约束：旧 payload 的颜色与 best 框不得残留）。
    """

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()
        cls.win.resize(1035, 800)
        cls.win.show()
        _pump(cls.win, 12)
        cls.win.stackedWidget.setCurrentIndex(2)
        _pump(cls.win, 12)

    def test_help_button_does_not_pass_checked_into_start_help(self):
        """「帮助人类」点击后 update_image 必须收到 start_help=True（默认）。

        回归背景（用户实测 2026-11-08「点帮助后卡在等待帮助计算开始」）：
        ``help_human.clicked`` 直连 ``update_image(start_help=True)`` 时，Qt 会把
        clicked 携带的 ``bool checked``（非 checkable 按钮恒为 False）当成
        ``start_help`` 传入 ⇒ 帮助线程**永不启动** ⇒ 界面停在
        「等待帮助计算开始…」，日志里一条 [run]/[help] 都不会出现。
        """
        win = main.MyMainWindow()
        captured = []
        win.update_image = lambda *a, **k: captured.append((a, k))
        win.help_human.clicked.disconnect()
        win.help_human.clicked.connect(lambda _checked=False: win.update_image())
        win.help_human.click()

        self.assertTrue(captured, "点击「帮助人类」必须触发 update_image")
        args, kwargs = captured[-1]
        self.assertEqual(args, (),
                         f"不得把 clicked 的 bool 当位置参数传入（实测 {args}）")
        self.assertNotEqual(
            kwargs.get("start_help"), False,
            "start_help 不得被 clicked 的 bool 覆盖成 False（否则帮助线程不启动）")

    def setUp(self):
        # 每个用例从干净初始态开始，避免上一次 payload 的样式污染读数
        for row in self.win.btn_list:
            for btn in row:
                btn.setText("")
                btn.setStyleSheet(self.win._HM_BASE)
        self.win._help_board_last = {
            id(b): (self.win._HM_BASE, "")
            for row in self.win.btn_list for b in row
        }
        self.win._last_heatmap_payload = None
        self.win._last_candidates = []

    def _snapshot(self):
        return [(b.text(), b.styleSheet())
                for row in self.win.btn_list for b in row]

    def test_help_board_consumes_payload_cell_value_and_prob(self):
        """≥3 元素输入：3 个真实概率格 + 1 个已开数字 + 1 个推理雷。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 3          # 已开数字 → 文字 "3"
        cv[2, 1] = 10         # 推理雷 → 红底
        prob = {(2, 1): 0.995, (3, 1): 0.004, (4, 1): 0.5}
        payload = {"prob": prob, "best": (2, 1), "best_conf": 0.995,
                   "total": 41.0, "cell_value": cv, "played": 5, "win": 2}
        before = self._snapshot()
        self.win.update_help_heatmap(payload)
        after = self._snapshot()
        changed = sum(1 for a, b in zip(before, after) if a != b)
        self.assertGreaterEqual(
            changed, 3,
            f"帮助棋盘必须消费 payload：只变化了 {changed} 格（需 ≥3）")
        # 坐标契约：(i, j) → btn[j-1][i-1]
        self.assertEqual(self.win.btn_list[0][0].text(), "3",
                         "已开数字必须显示在帮助页唯一棋盘上")
        self.assertIn("rgba(255, 0, 0", self.win.btn_list[1][0].styleSheet(),
                      "推理雷必须红底（事实层，不被概率色覆盖）")
        # best=(2,1) → btn[0][1]，必须加 3px 蓝框
        self.assertIn("border: 3px", self.win.btn_list[0][1].styleSheet(),
                      "best 格必须有蓝框")
        # 统计栏用真实 payload
        stats = self.win.help_board_label_stats.text()
        self.assertIn("99.50%", stats)
        self.assertIn("已完局: 5", stats)
        self.assertIn("胜率: 40.00%", stats)

    def test_help_board_matches_auto_page_semantics(self):
        """同一 payload 下，帮助页唯一棋盘与自动页逐格 (style, text) 一致。

        帮助页没有候选叠加时，两页渲染完全同源（共用 ``_hm_style_for_cell``），
        因此「同一份 payload 不得出现两套读数」可以被逐格断言。
        """
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 7
        cv[2, 2] = 10
        cv[3, 3] = 11
        prob = {(1, 2): 0.0, (2, 1): 1.0, (5, 5): 0.37, (6, 6): 0.02,
                (7, 7): 0.98}
        payload = {"prob": prob, "best": (5, 5), "best_conf": 0.37,
                   "total": 9.0, "cell_value": cv, "played": 2, "win": 1}
        self.win.update_help_heatmap(payload)
        self.win.update_heatmap(payload)
        for j in range(self.win.h):
            for i in range(self.win.w):
                hb = self.win.btn_list[j][i]
                ab = self.win.heatmap_btn_list[j][i]
                self.assertEqual(
                    (hb.text(), hb.styleSheet()),
                    (ab.text(), ab.styleSheet()),
                    f"({i},{j}) 帮助页与自动页语义不一致")

    def test_latest_payload_wins_over_older_one(self):
        """注入 payload A → payload B，最终显示**B**（A 的颜色/best 不残留）。

        这是本任务的核心回归：旧实现里帮助页有两套网格，且 ``update_btn_list``
        会用候选覆盖 ``update_help_heatmap`` 的结果；"最新 payload 单一真源"
        在结构上不成立。

        读数口径：A 的 (3,4) 用 p=0.0（``_hm_style_for_cell`` 的 ≤0.01 分支 ⇒
        **精确红底** rgba(255,0,0,255) + best 3px 蓝框），(5,5) 用 p=1.0（≥0.99
        ⇒ 精确绿底）。B 只含 (5,5)=0.5，因此 (3,4) 必须回落到「未开无概率」的
        白底 rgba(255,255,255,180)，且 A 的 3px 蓝框必须消失。
        """
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        # payload A：best=(3,4) 概率 0.0（精确红），另有 (5,5)=1.0（精确绿）
        self.win.update_help_heatmap({
            "prob": {(3, 4): 0.0, (5, 5): 1.0}, "best": (3, 4),
            "best_conf": 0.0, "total": 1.0, "cell_value": cv,
            "played": 1, "win": 0})
        a_cell = self.win.btn_list[3][2]      # (i,j)=(3,4) → btn[j-1][i-1]
        a_other = self.win.btn_list[4][4]     # (5,5)
        self.assertIn("border: 3px", a_cell.styleSheet(),
                      "A 的 best 框必须存在")
        self.assertIn("rgba(255, 0, 0", a_cell.styleSheet(), "A 的 (3,4) 红底")
        self.assertIn("rgba(0, 255, 0", a_other.styleSheet(), "A 的 (5,5) 绿底")

        # payload B：完全不同的一组值，best 换到 (5,5)，prob 只有 (5,5)=0.5
        self.win.update_help_heatmap({
            "prob": {(5, 5): 0.5}, "best": (5, 5), "best_conf": 0.5,
            "total": 2.0, "cell_value": cv, "played": 2, "win": 1})
        b_cell = self.win.btn_list[3][2]
        b_other = self.win.btn_list[4][4]
        # ① A 的 (3,4) 红底必须消失（B 不含它）→ 回到未开无概率的白底
        self.assertNotIn("rgba(255, 0, 0", b_cell.styleSheet(),
                         "payload A 的概率色残留在 (3,4)")
        self.assertIn("rgba(255, 255, 255, 180", b_cell.styleSheet(),
                      "B 不含 (3,4) ⇒ 必须回到未开无概率白底")
        # ② A 的 best 蓝框（在 (3,4)）必须消失
        self.assertNotIn("border: 3px", b_cell.styleSheet(),
                         "payload A 的 best 蓝框残留在 (3,4)")
        # ③ B 的 best 蓝框必须出现在 (5,5)
        self.assertIn("border: 3px", b_other.styleSheet(),
                      "payload B 的 best 蓝框没有出现在 (5,5)")
        # ④ 统计栏跟随 B
        stats = self.win.help_board_label_stats.text()
        self.assertIn("50.00%", stats, "统计栏未跟随最新 payload B")
        self.assertIn("已完局: 2", stats)

    def test_empty_payload_clears_all_probability_style(self):
        """空 payload（新局 clear）必须清除上一帧全部概率色与 best 框。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 4                      # 已开数字应保留（事实层）
        self.win.update_help_heatmap({
            "prob": {(2, 1): 1.0, (1, 2): 0.0, (4, 1): 0.5},
            "best": (2, 1), "best_conf": 1.0, "total": 3.0,
            "cell_value": cv, "played": 1, "win": 1})
        self.assertIn("border: 3px", self.win.btn_list[0][1].styleSheet())
        self.assertIn("rgba(0, 255, 0", self.win.btn_list[0][1].styleSheet())
        self.assertIn("rgba(255, 0, 0", self.win.btn_list[1][0].styleSheet())
        # 空 payload（solver 新局 clear=True 的形态：prob/best 皆空，棋盘保留）
        self.win.update_help_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cv, "played": 2, "win": 1})
        self.assertNotIn("border: 3px", self.win.btn_list[0][1].styleSheet(),
                         "空 payload 后 best 蓝框必须清除")
        self.assertIn("rgba(255, 255, 255, 180",
                      self.win.btn_list[0][1].styleSheet(),
                      "空 payload 后概率色必须清除（回未开无概率白底）")
        self.assertIn("rgba(255, 255, 255, 180",
                      self.win.btn_list[1][0].styleSheet(),
                      "空 payload 后上一帧红底必须清除")
        self.assertEqual(self.win.btn_list[0][0].text(), "4",
                         "事实棋盘（已开数字）必须保留")

    def test_clear_heatmap_state_wipes_help_board(self):
        """``_clear_heatmap_state('help')``：线程停止/新局时清空旧 payload。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 6
        self.win.update_help_heatmap({
            "prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
            "total": 3.0, "cell_value": cv, "played": 1, "win": 1})
        self.assertIn("border: 3px", self.win.btn_list[0][1].styleSheet())
        self.win._clear_heatmap_state("help")
        self.assertIsNone(self.win._last_heatmap_payload,
                          "清理后最近 payload 必须为 None")
        self.assertEqual(self.win._last_candidates, [],
                         "清理后候选必须清空")
        self.assertNotIn("border: 3px", self.win.btn_list[0][1].styleSheet(),
                         "清理后 best 蓝框必须消失")
        self.assertEqual(self.win.btn_list[0][0].text(), "6",
                         "清理只清概率层，事实棋盘数字保留")
        self.assertIn("已停止", self.win.help_board_label_stats.text())

    def test_update_btn_list_shares_single_latest_payload(self):
        """候选后到不得覆盖最新 payload；两边共用同一份 payload 与渲染入口。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        payload = {"prob": {(2, 1): 0.9, (6, 6): 0.3}, "best": (2, 1),
                   "best_conf": 0.9, "total": 5.0, "cell_value": cv,
                   "played": 1, "win": 0}
        self.win.update_help_heatmap(payload)
        self.assertIs(self.win._last_heatmap_payload, payload,
                      "update_help_heatmap 必须写同一份最新 payload")
        self.win.update_btn_list([
            {"pos": (2, 1), "confidence": 0.9, "num": 1, "is_mine": False,
             "is_best": True, "exp": "枚举得出", "is_recommend": False},
        ])
        # 候选后到：payload 仍是同一份（未被 pos_dict_list 覆盖）
        self.assertIs(self.win._last_heatmap_payload, payload,
                      "update_btn_list 不得覆盖最新 heatmap payload")
        # 候选格显示候选序号（叠加层）+ 与自动页同一套概率色带（confidence=0.9
        # 落在渐变色带上，不再是旧实现的纯绿底）+ best 的 3px 蓝框
        self.assertEqual(self.win.btn_list[0][1].text(), "1",
                         "候选格应显示判断次序")
        self.assertIn("border: 3px", self.win.btn_list[0][1].styleSheet())
        # 非候选格仍保留 payload 的概率色（未被候选渲染清掉）
        self.assertNotIn("rgba(255, 255, 255, 180",
                         self.win.btn_list[5][5].styleSheet(),
                         "(6,6) 的概率色必须仍在（未被候选渲染清掉）")

    def test_candidate_overlay_does_not_override_open_digit(self):
        """已开数字格是事实：候选叠加不得把它改成序号/证明色。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 2
        self.win.update_help_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cv, "played": 1, "win": 0})
        self.win.update_btn_list([
            {"pos": (1, 1), "confidence": 1.0, "num": 7, "is_mine": False,
             "is_best": True, "exp": "枚举得出", "is_recommend": False},
        ])
        self.assertEqual(self.win.btn_list[0][0].text(), "2",
                         "已开数字格不得被候选序号覆盖")
        self.assertIn("rgba(208, 208, 214", self.win.btn_list[0][0].styleSheet(),
                      "已开数字格不得被候选证明色覆盖")

    def test_help_board_refresh_keeps_click_all_enabled(self):
        """刷新不得破坏 click_all 的可用性（原有帮助候选点击链路）。

        P0-1 修复留痕（review2 architect P0-1）：候选表新增 proven_safe
        字段，_help_action_targets 只按它收集批量点击；本测试的夹具构造
        的是「确定安全格」，故须显式带 proven_safe=True。原判据（按钮可用、
        刷新后仍可用）逐字保留，未放宽。
        """
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_btn_list([
            {"pos": (2, 1), "confidence": 1.0, "num": 1, "is_mine": False,
             "is_best": True, "proven_safe": True, "exp": "枚举得出",
             "is_recommend": False},
        ])
        self.assertTrue(self.win.click_all.isEnabled())
        self.win.update_help_heatmap(
            {"prob": {(2, 1): 0.99}, "best": (2, 1), "best_conf": 0.99,
             "total": 3.0, "cell_value": cv, "played": 1, "win": 1})
        self.assertTrue(self.win.click_all.isEnabled(),
                        "热力图刷新后 click_all 必须仍可点击")

    def test_candidate_click_handler_not_duplicated(self):
        """同一候选格重复到达时不得叠多个点击 handler（旧 bug：点一次弹两个框）。"""
        self.win.update_btn_list([
            {"pos": (3, 3), "confidence": 0.8, "num": 1, "is_mine": False,
             "is_best": False, "exp": "枚举得出", "is_recommend": True},
        ])
        self.win.update_btn_list([
            {"pos": (3, 3), "confidence": 0.8, "num": 1, "is_mine": False,
             "is_best": False, "exp": "枚举得出", "is_recommend": True},
        ])
        self.assertEqual(self.win.btn_list[2][2].receivers(
            self.win.btn_list[2][2].clicked), 1,
            "同一候选格重复到达后应只有 1 个点击 handler")

    def test_help_click_all_uses_candidates_not_stale_cache(self):
        """「点击所有确定方格」必须以最新候选为真源，缓存被清空也要能点。"""
        from unittest import mock
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_help_heatmap({"prob": {}, "best": None, "best_conf": None,
                                      "total": 0.0, "cell_value": cv,
                                      "played": 0, "win": 0})
        self.win.update_btn_list([
            {"pos": (3, 4), "confidence": 1.0, "num": 1, "is_mine": False,
             "is_best": True, "proven_safe": True,
             "exp": "C++ 三层决策 (certain_safe)",
             "is_recommend": True},
            {"pos": (5, 6), "confidence": 0.0, "num": 2, "is_mine": True,
             "is_best": False, "proven_safe": False, "exp": "枚举得出",
             "is_recommend": False},
        ])
        # 模拟其他槽把缓存清掉（旧实现此时会静默什么都不做）
        self.win.clicks = []
        self.win.mines = []
        clicks, mines = self.win._help_action_targets()
        self.assertEqual(clicks, [(3, 4)])
        self.assertEqual(mines, [(5, 6)])
        calls = []
        with mock.patch("main.win32gui.FindWindow", return_value=1), \
                mock.patch("main.win32gui.ShowWindow"), \
                mock.patch("main.set_top_window"), \
                mock.patch("main.ClientToScreen", return_value=(100, 200)), \
                mock.patch("main.pyautogui.click",
                           side_effect=lambda *a, **k: calls.append(("click", a))), \
                mock.patch("main.pyautogui.rightClick",
                           side_effect=lambda *a, **k: calls.append(("right", a))), \
                mock.patch("main.pyautogui.keyDown"), \
                mock.patch("main.pyautogui.keyUp"):
            self.win.click_all_func()
        self.assertEqual(len(calls), 2, f"必须执行 1 次点击 + 1 次标记，实测 {calls}")
        # 1-based 坐标 → bx + x*cw
        self.assertEqual(calls[0][1], (100 + 3 * self.win.cell_width,
                                       200 + 4 * self.win.cell_width))
        self.assertEqual(calls[1][1], (100 + 5 * self.win.cell_width,
                                       200 + 6 * self.win.cell_width))

    def test_help_candidate_style_matches_auto_heatmap(self):
        """帮助候选格视觉必须与自动页同一套概率色带/蓝框语义。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        payload = {"prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
                   "total": 5.0, "cell_value": cv, "played": 1, "win": 0}
        self.win.update_help_heatmap(payload)
        self.win.update_btn_list([
            {"pos": (2, 1), "confidence": 0.995, "num": 1, "is_mine": False,
             "is_best": True, "exp": "枚举得出", "is_recommend": False},
        ])
        help_style = self.win.btn_list[0][1].styleSheet()
        self.assertIn("rgba(0, 255, 0", help_style)
        self.assertIn("border: 3px", help_style)
        self.assertNotIn("border: 2px solid rgb(0, 0, 255)", help_style,
                         "帮助页不得再使用旧的 2px 蓝框专属样式")
        self.assertEqual(self.win.btn_list[0][1].text(), "1",
                         "候选序号文字保留")

    def test_help_signal_path_renders_heatmap_and_stats(self):
        """帮助线程的 heatmap_signal 必须真实画到统一棋盘（此前从不发帧）。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        cv[1, 1] = 3
        self.win.help_thread.heatmap_signal.emit({
            "prob": {(5, 5): 0.93}, "best": (5, 5), "best_conf": 0.93,
            "total": 120.0, "cell_value": cv, "played": 2, "win": 1,
            "phase": "decision", "clear": False})
        btn = self.win.btn_list[4][4]
        self.assertIn("border: 3px", btn.styleSheet(),
                      "帮助页 heatmap_signal 未渲染 best 蓝框")
        self.assertIn("background-color:rgba(", btn.styleSheet(),
                      "帮助页 heatmap_signal 未渲染概率色")
        self.assertNotIn("rgba(255, 255, 255, 180)", btn.styleSheet(),
                         "有真实概率时不应回落到无数据白底")
        self.assertIn("93.00%", self.win.help_board_label_stats.text())
        self.assertIn("已完局: 2", self.win.help_board_label_stats.text())

    def test_help_thread_finish_keeps_candidates_and_click_all(self):
        """帮助线程结束不得抹掉候选/热力图（否则「点击所有确定方格」失效）。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.help_thread.heatmap_signal.emit({
            "prob": {(5, 5): 0.93}, "best": (5, 5), "best_conf": 0.93,
            "total": 120.0, "cell_value": cv, "played": 2, "win": 1,
            "phase": "decision", "clear": False})
        self.win.help_thread.update_btn_list_signal.emit([
            {"pos": (5, 5), "confidence": 0.93, "num": 1, "is_mine": False,
             "is_best": True, "exp": "C++ 三层决策 (pbs)",
             "is_recommend": True},
            {"pos": (9, 9), "confidence": 0.0, "num": 2, "is_mine": True,
             "is_best": False, "exp": "枚举得出", "is_recommend": False},
        ])
        self.assertGreaterEqual(len(self.win._last_candidates), 2)
        self.assertTrue(self.win.click_all.isEnabled())
        # 线程结束（真实槽）
        self.win._on_help_thread_finished()
        self.assertGreaterEqual(len(self.win._last_candidates), 2,
                                "线程结束后候选被清空 ⇒ 点击目标消失")
        self.assertTrue(self.win.click_all.isEnabled(),
                        "线程结束后「点击所有确定方格」必须仍可用")
        self.assertIn("border: 3px", self.win.btn_list[4][4].styleSheet(),
                      "线程结束后帮助页热力图不得被抹掉")
        self.assertIn("已结束", self.win.help_board_label_stats.text())

    def test_help_certain_safe_batch_all_clickable(self):
        """certain_safe 整批候选必须全部可点击（用户报「确定方格点了没用」）。"""
        from unittest import mock
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        batch = [(3, 2), (4, 2), (3, 3), (5, 3)]
        self.win.update_help_heatmap({
            "prob": {(3, 2): 1.0}, "best": (3, 2), "best_conf": 1.0,
            "total": 10.0, "cell_value": cv, "played": 0, "win": 0,
            "phase": "decision", "clear": False})
        self.win.update_btn_list([
            {"pos": p, "confidence": 1.0, "num": i + 1, "is_mine": False,
             "is_best": True, "proven_safe": True,
             "exp": "C++ 三层决策 (certain_safe)", "is_recommend": True}
            for i, p in enumerate(batch)
        ])
        clicks, mines = self.win._help_action_targets()
        self.assertEqual(sorted(clicks), sorted(batch),
                         f"整批确定格必须全部进入点击目标，实测 {clicks}")
        self.assertEqual(mines, [])
        self.assertTrue(self.win.click_all.isEnabled())
        calls = []
        with mock.patch("main.win32gui.FindWindow", return_value=1), \
                mock.patch("main.win32gui.ShowWindow"), \
                mock.patch("main.set_top_window"), \
                mock.patch("main.ClientToScreen", return_value=(100, 200)), \
                mock.patch("main.pyautogui.click",
                           side_effect=lambda *a, **k: calls.append(a)), \
                mock.patch("main.pyautogui.rightClick"), \
                mock.patch("main.pyautogui.keyDown"), \
                mock.patch("main.pyautogui.keyUp"):
            self.win.click_all_func()
        self.assertEqual(len(calls), len(batch),
                         f"必须逐个点击 {len(batch)} 个确定格，实测 {len(calls)}")
        cw = self.win.cell_width
        for (x, y) in batch:
            self.assertIn((100 + x * cw, 200 + y * cw), calls,
                          f"({x},{y}) 的像素坐标未出现在点击列表 {calls}")

    def test_win_rate_phase_clicks_all_certain_safe_cells(self):
        """win_rate 阶段：prob 里的全部确定安全格都必须进入点击目标。

        用户实测 2026-11-08「进入 win rate 阶段之后，不能一次点击所有
        百分百不是雷的方格」。根因：该阶段 decide.confidence 是策略胜率
        (=1.0) 而非安全概率，候选表又只收单点 (x,y)，56 个 prob==1.0
        的确定格只被收进 1 个。修法：以逐格 prob 为真源取全集。
        """
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        batch = [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1)]
        prob = {p: 1.0 for p in batch}
        prob[(7, 1)] = 0.5      # 非确定格，不得点
        prob[(8, 1)] = 0.0      # 确定雷
        self.win.update_help_heatmap({
            "prob": prob, "best": (2, 1),
            "best_conf": 1.0,  # win_rate 的 confidence 是胜率，不是安全概率
            "total": 128.0, "cell_value": cv, "played": 0, "win": 0,
            "phase": "decision", "clear": False})
        # 候选表只有单点（模拟 win_rate 分支的真实形态）
        self.win.update_btn_list([
            {"pos": (2, 1), "confidence": 1.0, "num": 1, "is_mine": False,
             "is_best": True, "exp": "C++ 三层决策 (win_rate)",
             "is_recommend": True},
        ])
        clicks, mines = self.win._help_action_targets()
        self.assertEqual(sorted(clicks), sorted(batch),
                         "win_rate 的全部 prob==1.0 确定格都必须可点")
        self.assertEqual(mines, [(8, 1)])
        self.assertTrue(self.win.click_all.isEnabled())

    def test_help_thread_heatmap_signal_is_connected(self):
        """帮助线程的 heatmap_signal 必须真实接到 GUI 槽（不是只有自动线程）。"""
        win = self.win
        receiver_count = win.help_thread.receivers(
            win.help_thread.heatmap_signal)
        self.assertGreater(receiver_count, 0,
                           "help_thread.heatmap_signal 没有任何接收者")

    def test_auto_thread_finished_keeps_final_heatmap_frame(self):
        """自动线程结束后保留最后一帧真实 win-rate/决策热力图。"""
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 1.0}, "best": (2, 1), "best_conf": 1.0,
            "total": 3.0, "cell_value": cv, "played": 1, "win": 1})
        self.assertIn("border: 3px",
                      self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertIn("rgba(0, 255, 0",
                      self.win.heatmap_btn_list[0][1].styleSheet())
        before = (self.win.heatmap_btn_list[0][1].text(),
                  self.win.heatmap_btn_list[0][1].styleSheet())
        self.win._on_auto_thread_finished()   # QThread.finished 的槽
        after = (self.win.heatmap_btn_list[0][1].text(),
                 self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertEqual(before, after,
                         "线程结束后必须保留最后一帧真实热力图（不得清空）")
        self.assertIn("已结束", self.win.heatmap_label_stats.text())

    def test_visible_signal_false_does_not_clear_heatmap(self):
        """注入必红：``Visible_signal(False)`` 只是隐藏进度条，**不得**清热力图。

        这是上一版设计的真实错误（把"隐藏进度条"当"线程停止"），会让计算中途
        的格子被清空。本用例把该错误钉死：发 False 后概率色/边框必须原样保留。
        """
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        self.win.update_heatmap({
            "prob": {(2, 1): 1.0}, "best": (2, 1), "best_conf": 1.0,
            "total": 3.0, "cell_value": cv, "played": 1, "win": 1})
        before = (self.win.heatmap_btn_list[0][1].text(),
                  self.win.heatmap_btn_list[0][1].styleSheet())
        self.win.auto_play_thread.Visible_signal.emit(False)
        after = (self.win.heatmap_btn_list[0][1].text(),
                 self.win.heatmap_btn_list[0][1].styleSheet())
        self.assertEqual(before, after,
                         "Visible_signal(False) 只是隐藏进度条，不得清空热力图")

    def test_auto_scan_noise_warning_does_not_popup(self):
        """「点击后重扫：9/9 格无法可信识别」不得弹窗（用户明确要求）。

        判据用注入必红的反向检查：把 QMessageBox 的三个弹窗入口替换成记录器，
        投递扫描噪声文案 → 弹窗计数必须为 0；再投递设置类错误 → 必须弹 1 次
        （证明过滤器不是"把所有告警都吞掉"）。
        """
        from unittest import mock
        win = self.win
        calls = []
        with mock.patch.object(main.QMessageBox, "warning",
                               side_effect=lambda *a, **k: calls.append(("warning", a))), \
                mock.patch.object(main.QMessageBox, "about",
                                  side_effect=lambda *a, **k: calls.append(("about", a))):
            win._on_auto_warning(
                "点击后重扫：9/9 格无法可信识别（比例 100% ≥ 阈值 50%），"
                "棋盘不可信，请重新截图/校准模板与窗口位置")
            self.assertEqual(calls, [],
                             f"扫描噪声不得弹窗，实测弹了 {calls}")
            win._on_auto_warning("请检查设置中总雷数，宽度，长度是否输入正确")
            self.assertEqual(len(calls), 1,
                             "设置类错误必须仍然弹窗（过滤器不得吞掉真实告警）")
            # 帮助线程同款过滤
            calls.clear()
            win._on_help_warning("点击后重扫：9/9 格无法可信识别")
            self.assertEqual(calls, [], "帮助模式扫描噪声同样不得弹窗")


if __name__ == "__main__":
    unittest.main(verbosity=2)
