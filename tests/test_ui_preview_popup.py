# -*- coding: utf-8 -*-
"""悬浮 7×7 预览浮层 + 紧凑信息区 offscreen 回归测试（UI 批 A/B/C）。

覆盖：
A 批：PreviewPopup 实例化（Qt.Tool/Frameless/置顶/不抢焦点）、7×7 局部棋盘
      取样、边缘定位（右侧/左侧翻转/上下夹取）、Esc 关闭、关闭按钮、固定后
      hover-leave 不关闭、eventFilter 挂接幂等。
B 批：main.MyMainWindow offscreen 可实例化，运行期注入的状态条/决策卡/图例/
      候选详情控件存在，且不依赖 ui/window.py 改动。
C 批：value_kind 三分类正确（尤其 heuristic_win_rate 不得叫安全概率）、
      transitions 缺失时显示"暂无真实状态转移分布"、决策卡/候选详情用真实
      payload 且不外推合成概率。

运行：QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui_preview_popup -v
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402
import numpy as np  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import main  # noqa: E402
from ui.preview_popup import (  # noqa: E402
    PreviewPopup,
    PreviewHoverButton,
    VALUE_KIND_LABELS,
    NO_TRANSITION_TEXT,
    NO_TRANSITION_HINT,
    TRANSITION_REASON_TEXT,
    mini_open_style,
    mini_cell_style,
    transition_boards,
    transition_hint_text,
    compute_popup_position,
    describe_value,
    sample_board,
    summarize_payload,
    transitions_text,
    attach_hover_preview,
)


def _board(h=16, w=30):
    """构造一份合法 cell_value（编码 9=未开, 0-8=已开, 10=推理雷, 11=推理安全）。"""
    cv = np.full((h + 2, w + 2), 9, dtype="int32")
    cv[1, 1] = 2
    cv[2, 1] = 10
    cv[3, 3] = 11
    return cv


class TestSampleBoard(unittest.TestCase):
    """7×7 局部棋盘取样（以目标格为中心 ±3，越界为 None）。"""

    def test_center_cell_returns_49_cells(self):
        rows = sample_board(_board(), (5, 5))
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(len(r) == 7 for r in rows))
        # 中心 = 目标格本身：cv[5][5] 未开 = 9
        self.assertEqual(rows[3][3], 9)

    def test_known_markers_preserved(self):
        rows = sample_board(_board(), (3, 3))
        # 目标 (3,3) 是推理安全 11（cv[3][3]），中心必须是 11
        self.assertEqual(rows[3][3], 11)
        # (1,1)=2 在 dx=-2, dy=-2 → rows[1][1]
        self.assertEqual(rows[1][1], 2)
        # (1,2) 是推理雷 10（cv[2][1]）→ dx=-2, dy=-1 → rows[2][1]
        self.assertEqual(rows[2][1], 10)

    def test_corner_cell_pads_outside_with_none(self):
        rows = sample_board(_board(), (1, 1))
        self.assertIsNone(rows[0][0])       # 棋盘外（0 行/0 列不是合法 1-based）
        # 目标格本身是 cv[1][1]=2（_board 里预设的已开数字）
        self.assertEqual(rows[3][3], 2)

    def test_none_cell_value_returns_none(self):
        self.assertIsNone(sample_board(None, (3, 3)))


class TestValueKindSemantics(unittest.TestCase):
    """C 批核心：三类估值语义不得混淆，启发式胜率绝不叫安全概率。"""

    def test_three_kind_labels_are_distinct(self):
        kinds = {"exact_safety", "uniform_prior", "heuristic_win_rate"}
        labels = {VALUE_KIND_LABELS[k] for k in kinds}
        self.assertEqual(len(labels), 3, "三类必须各有独立文案")

    def test_heuristic_win_rate_label_never_says_safety(self):
        label = VALUE_KIND_LABELS["heuristic_win_rate"]
        self.assertIn("策略胜率", label)
        self.assertIn("非安全概率", label)
        self.assertNotIn("安全概率（", label)

    def test_prior_label_marks_non_exact(self):
        self.assertIn("非精确解", VALUE_KIND_LABELS["uniform_prior"])

    def test_describe_value_uses_kind_label(self):
        text = describe_value(0.62, "heuristic_win_rate")
        self.assertIn("62.00%", text)
        self.assertIn("策略胜率", text)
        self.assertIn("非安全概率", text)
        self.assertNotIn("安全概率（精确解）", text)

    def test_describe_value_none_is_dash_not_zero(self):
        text = describe_value(None, "exact_safety")
        self.assertIn("—", text)
        self.assertNotIn("0.00%", text)


class TestTransitionsPlaceholder(unittest.TestCase):
    """真实状态转移缺失时只能显示占位，禁止合成概率。"""

    def test_missing_transitions_yields_placeholder(self):
        info = summarize_payload({
            "cell": (5, 5), "value": 0.5,
            "value_kind": "exact_safety", "cell_value": _board(),
        })
        self.assertFalse(info["has_transitions"])
        self.assertEqual(info["transition_text"], NO_TRANSITION_TEXT)
        self.assertEqual(info["transition_rows"], [])

    def test_none_transitions_yields_placeholder(self):
        info = summarize_payload({"cell": (5, 5), "transitions": None})
        self.assertFalse(info["has_transitions"])

    def test_real_transitions_are_rendered_verbatim(self):
        info = summarize_payload({
            "cell": (5, 5),
            "transitions": [
                {"label": "打开数字 1", "probability": 0.43},
                {"label": "踩雷", "probability": 0.12},
            ],
        })
        self.assertTrue(info["has_transitions"])
        self.assertEqual(len(info["transition_rows"]), 2)
        self.assertEqual(info["transition_rows"][0], ("打开数字 1", "43.00%"))
        # 概率必须来自 payload，不是合成的：只保留传入的两项
        self.assertEqual(
            [r[1] for r in info["transition_rows"]], ["43.00%", "12.00%"]
        )

    def test_transitions_text_drops_entries_without_probability(self):
        rows = transitions_text([
            {"label": "有概率", "probability": 0.5},
            {"label": "无概率"},
            "not-a-dict",
        ])
        self.assertEqual(rows, [("有概率", "50.00%")])

    def test_two_kind_input_does_not_invent_probability(self):
        """≥3 元素输入断言：三个候选，任何一个缺失 transition 都不得补概率。"""
        infos = [
            summarize_payload({"cell": (i, 1), "value": 0.1 * i,
                               "value_kind": "exact_safety"})
            for i in (1, 2, 3)
        ]
        for info in infos:
            self.assertFalse(info["has_transitions"])
            self.assertEqual(info["transition_text"], NO_TRANSITION_TEXT)


class TestPopupPosition(unittest.TestCase):
    """边缘定位：右侧优先 / 放不下翻左侧 / 四边夹取。"""

    def test_prefers_right_of_anchor(self):
        left, top = compute_popup_position(
            (100, 100, 24, 24), (400, 300), (0, 0, 1280, 800))
        self.assertEqual(left, 100 + 24 + 12)
        self.assertEqual(top, 100)

    def test_flips_left_when_right_overflow(self):
        left, _ = compute_popup_position(
            (1200, 100, 24, 24), (400, 300), (0, 0, 1280, 800))
        self.assertEqual(left, 1200 - 400 - 12)

    def test_flips_up_when_below_overflow(self):
        _, top = compute_popup_position(
            (300, 700, 24, 24), (400, 300), (0, 0, 1280, 800))
        self.assertEqual(top, 700 - 300)

    def test_clamped_inside_screen(self):
        """浮层能装下时必须被夹在屏幕内（上下左右四边）。"""
        popup_w, popup_h = 400, 300
        screen = (0, 0, 1100, 600)
        for anchor in ((0, 0, 24, 24), (1080, 0, 24, 24),
                       (0, 580, 24, 24), (1080, 580, 24, 24)):
            left, top = compute_popup_position(anchor, (popup_w, popup_h),
                                               screen, margin=12)
            self.assertGreaterEqual(left, 12, f"{anchor}: 左越界")
            self.assertGreaterEqual(top, 12, f"{anchor}: 上越界")
            self.assertLessEqual(left + popup_w, screen[2] - 12 + 1,
                                 f"{anchor}: 右越界")
            self.assertLessEqual(top + popup_h, screen[3] - 12 + 1,
                                 f"{anchor}: 下越界")


class TestPreviewPopupWidget(unittest.TestCase):
    """A 批控件行为（offscreen 实例化 + 交互）。"""

    @classmethod
    def setUpClass(cls):
        cls.popup = PreviewPopup()

    def tearDown(self):
        self.popup._pinned = False
        self.popup.pin_button.setChecked(False)
        self.popup.pin_button.setText("固定")
        self.popup.close_popup()

    def test_flags_are_tool_frameless_on_top(self):
        flags = self.popup.windowFlags()
        self.assertTrue(flags & QtCore.Qt.Tool, "必须是 Qt.Tool（不抢焦点/不进任务栏）")
        self.assertTrue(flags & QtCore.Qt.FramelessWindowHint, "必须无边框")
        self.assertTrue(flags & QtCore.Qt.WindowStaysOnTopHint, "必须浮在最上层")

    def test_show_without_activating(self):
        self.assertTrue(
            self.popup.testAttribute(QtCore.Qt.WA_ShowWithoutActivating),
            "hover 浮层不得抢焦点（否则 enter→leave 死循环，风险 R2）",
        )

    def test_set_payload_renders_board_and_value(self):
        info = self.popup.set_payload({
            "cell": (3, 3), "value": 0.875,
            "value_kind": "exact_safety",
            "source": "C++ 三层决策 (optimal)",
            "cell_value": _board(),
        })
        self.assertTrue(info["board_ok"])
        self.assertIn("(3, 3)", self.popup.title_label.text())
        self.assertIn("87.50%", self.popup.value_label.text())
        self.assertIn("安全概率", self.popup.value_label.text())
        self.assertIn("C++ 三层决策", self.popup.source_label.text())

    def test_no_transition_placeholder_visible(self):
        self.popup.set_payload({"cell": (3, 3), "value": 0.5,
                                "value_kind": "exact_safety",
                                "cell_value": _board()})
        self.assertIn(NO_TRANSITION_TEXT, self.popup.transition_label.text())
        self.assertIn("不显示合成概率", self.popup.note_label.text())

    def test_show_for_cell_and_close(self):
        self.popup.show_for_cell(
            (5, 5),
            payload={"cell": (5, 5), "value": 0.5,
                     "value_kind": "uniform_prior", "cell_value": _board()},
            anchor_point=(400, 300), screen=(0, 0, 1280, 800),
        )
        self.assertTrue(self.popup.isVisible())
        self.assertIn("均匀先验", self.popup.value_label.text())
        self.popup.close_popup()
        self.assertFalse(self.popup.isVisible())

    def test_pin_keeps_popup_open_on_leave(self):
        self.popup.show_for_cell(
            (5, 5),
            payload={"cell": (5, 5), "value": 0.5, "cell_value": _board()},
            anchor_point=(400, 300), screen=(0, 0, 1280, 800),
        )
        self.popup.toggle_pin(True)
        self.assertTrue(self.popup.is_pinned)
        self.popup.hide_if_unpinned()          # 固定后移开鼠标不关闭
        self.assertTrue(self.popup.isVisible())
        self.popup.toggle_pin(False)
        self.popup.hide_if_unpinned()          # 取消固定后移开即关闭
        self.assertFalse(self.popup.isVisible())

    def test_escape_key_closes_popup(self):
        self.popup.show_for_cell(
            (5, 5),
            payload={"cell": (5, 5), "value": 0.5, "cell_value": _board()},
            anchor_point=(400, 300), screen=(0, 0, 1280, 800),
        )
        self.assertTrue(self.popup.isVisible())
        ev = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape,
                             QtCore.Qt.NoModifier)
        handled = self.popup.eventFilter(self.popup, ev)
        self.assertTrue(handled, "Esc 必须被浮层拦截（否则会穿透主窗口）")
        self.assertFalse(self.popup.isVisible())

    def test_apply_filter_installed_and_removed(self):
        self.popup.show_for_cell(
            (5, 5),
            payload={"cell": (5, 5), "value": 0.5, "cell_value": _board()},
            anchor_point=(400, 300), screen=(0, 0, 1280, 800),
        )
        self.assertTrue(self.popup._app_filter_installed)
        self.popup.close_popup()
        self.assertFalse(self.popup._app_filter_installed)


class TestAttachHoverPreview(unittest.TestCase):
    """eventFilter 挂接候选按钮：进入显示、离开关闭、重复挂接幂等。"""

    def setUp(self):
        self.btn = QtWidgets.QPushButton("cand")
        self.popup = PreviewPopup()
        self.payload = {
            "cell": (4, 4), "value": 0.7,
            "value_kind": "heuristic_win_rate",
            "source": "胜率计算得出",
            "cell_value": _board(),
        }

    def tearDown(self):
        self.popup.close_popup()
        self.btn.deleteLater()

    def test_attach_sets_attribute_and_filter(self):
        filt = attach_hover_preview(self.btn, self.popup, lambda _o: self.payload)
        self.assertTrue(self.btn.testAttribute(QtCore.Qt.WA_Hover))
        self.assertIs(self.btn._preview_filter, filt, "过滤器必须被按钮持有防 GC")
        self.assertFalse(filt.popup.isVisible())

    def test_enter_shows_and_leave_hides(self):
        filt = attach_hover_preview(self.btn, self.popup, lambda _o: self.payload)
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.popup.isVisible())
        # 关键语义：策略胜率不得被展示成安全概率
        self.assertIn("策略胜率", self.popup.value_label.text())
        self.assertIn("非安全概率", self.popup.value_label.text())
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertFalse(self.popup.isVisible())

    def test_enter_without_payload_shows_nothing(self):
        filt = attach_hover_preview(self.btn, self.popup, lambda _o: None)
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertFalse(self.popup.isVisible(), "无真实估值时宁可不显示")

    def test_provider_exception_does_not_break_ui(self):
        def boom(_o):
            raise RuntimeError("provider 崩了")
        filt = attach_hover_preview(self.btn, self.popup, boom)
        filt.eventFilter(self.btn, QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertFalse(self.popup.isVisible())


class TestMainWindowCompactUI(unittest.TestCase):
    """B/C 批：主窗口 offscreen 可实例化 + 紧凑信息区 + 真实 payload 接线。"""

    @classmethod
    def setUpClass(cls):
        cls.win = main.MyMainWindow()

    def test_offscreen_instantiation_and_widgets(self):
        for attr in ("status_card", "status_label", "decision_card",
                     "legend_card", "candidate_card", "candidate_list",
                     "help_legend_card", "help_candidate_card",
                     "help_candidate_list"):
            self.assertTrue(hasattr(self.win, attr), f"缺少运行期注入控件 {attr}")

    def test_generated_window_py_not_required(self):
        """紧凑布局是运行期注入：生成物里的三页索引保持不变。"""
        self.assertEqual(self.win.stackedWidget.count(), 3)
        self.assertIsNone(
            self.win.findChild(QtWidgets.QWidget, "uiCardCompactPageMark"),
            "不得向生成物塞自定义页面标记",
        )

    def test_decision_card_marks_value_kind(self):
        self.win.update_decision_card((12, 8), 0.62, "heuristic_win_rate",
                                      source="胜率计算得出")
        self.assertIn("(12, 8)", self.win.decision_body.text())
        self.assertIn("62.00%", self.win._decision_value.text())
        self.assertIn("策略胜率", self.win._decision_kind.text())
        self.assertIn("非安全概率", self.win._decision_kind.text())
        self.assertNotIn("安全概率（精确解）", self.win._decision_kind.text())

    def test_decision_card_handles_missing_value(self):
        self.win.update_decision_card(None, None, "unknown", source="本轮无最优格")
        self.assertEqual(self.win._decision_value.text(), "—")
        self.assertIn("无估值数据", self.win._decision_kind.text())

    def test_update_heatmap_feeds_decision_card(self):
        cv = np.full((self.win.h + 2, self.win.w + 2), 9, dtype="int32")
        payload = {"prob": {(2, 1): 0.995}, "best": (2, 1), "best_conf": 0.995,
                   "total": 12.0, "cell_value": cv, "played": 4, "win": 2}
        self.win.update_heatmap(payload)
        self.assertIn("(2, 1)", self.win.decision_body.text())
        self.assertIn("99.50%", self.win._decision_value.text())
        self.assertIn("自动运行中", self.win.status_label.text())
        self.assertIn("胜率 50.00%", self.win.status_label.text())

    def test_update_btn_list_feeds_candidate_list_and_kind(self):
        pos_dict_list = [
            {"pos": (2, 1), "confidence": 0.999, "num": 1, "is_mine": False,
             "is_best": True, "exp": "C++ 三层决策 (certain_safe)",
             "is_recommend": True},
            {"pos": (4, 4), "confidence": 0.62, "num": 2, "is_mine": False,
             "is_best": False, "exp": "胜率计算得出", "is_recommend": True},
            {"pos": (6, 6), "confidence": 0.71, "num": 3, "is_mine": False,
             "is_best": False, "exp": "随机选择。", "is_recommend": False},
        ]
        self.win.update_btn_list(pos_dict_list)
        self.assertEqual(self.win.help_candidate_list.count(), 3)
        texts = [self.win.help_candidate_list.item(i).text()
                 for i in range(self.win.help_candidate_list.count())]
        self.assertIn("安全概率（精确解）", texts[0])
        self.assertIn("策略胜率（启发式，非安全概率）", texts[1])
        self.assertIn("均匀先验（非精确解）", texts[2])

    def test_classify_value_kind_mapping(self):
        self.assertEqual(main.classify_value_kind("C++ 三层决策 (optimal)"),
                         "exact_safety")
        self.assertEqual(main.classify_value_kind("枚举得出, 预计可以确定的方格数：1.0"),
                         "exact_safety")
        self.assertEqual(main.classify_value_kind("胜率计算得出"), "heuristic_win_rate")
        self.assertEqual(main.classify_value_kind("随机选择。"), "uniform_prior")
        self.assertEqual(main.classify_value_kind("未知文案"), "unknown")
        self.assertEqual(main.classify_value_kind(None), "unknown")

    def test_classify_heatmap_kind_empty_prob_is_unknown(self):
        self.assertEqual(main.classify_heatmap_kind({"prob": {}}), "unknown")
        self.assertEqual(main.classify_heatmap_kind({"prob": {(1, 1): 0.5}}),
                         "exact_safety")
        self.assertEqual(
            main.classify_payload_kind({"prob": {}, "value_kind": "heuristic_win_rate"}),
            "heuristic_win_rate",
            "payload 自带 value_kind 时必须尊重，不被推断覆盖",
        )

    def test_preview_payload_never_synthesizes_transitions(self):
        pos_dict = {"pos": (4, 4), "confidence": 0.62, "num": 2,
                    "is_mine": False, "is_best": False,
                    "exp": "胜率计算得出", "is_recommend": True}
        payload = self.win._preview_payload_for_candidate(pos_dict)
        self.assertIsNone(payload["transitions"],
                          "现有 solver 无真实转移分布 ⇒ 必须 None，不得合成")
        self.assertEqual(payload["value"], 0.62)
        self.assertEqual(payload["value_kind"], "heuristic_win_rate")
        info = summarize_payload(payload)
        self.assertFalse(info["has_transitions"])
        self.assertEqual(info["transition_text"], NO_TRANSITION_TEXT)

    def test_heatmap_preview_payload_uses_real_prob(self):
        payload = self.win._preview_payload_for_heatmap_cell(3, 4)
        self.assertEqual(payload["cell"], (3, 4))
        # 未渲染过 payload 时为 None，不得编造数值
        self.assertIsNone(payload["value"])
        self.assertIsNone(payload["transitions"])


class TestPreviewHoverButtonRealEvents(unittest.TestCase):
    """真实 hover 通道：按钮自身 Enter/Leave/Focus 事件（不是 eventFilter）。

    背景：用户指出「悬浮 hover 实际未实现」。旧实现只依赖
    ``attach_hover_preview`` 的 eventFilter + WA_Hover，在 24×24 密集网格上
    Qt 常不派发 Hover 事件。``PreviewHoverButton`` 直接覆写 enterEvent /
    leaveEvent / focusInEvent / focusOutEvent，本类验证这条真实通道。

    ⚠️ 证据边界：offscreen 平台没有真实鼠标，这里验证的是「事件派发到
    enterEvent 之后浮层可见 / Leave 之后延迟关闭」这一层；**物理鼠标 hover
    本身 NOT_RUN**（见 review/fix-help-heatmap-overlap.md 未覆盖项）。
    """

    @classmethod
    def setUpClass(cls):
        cls.popup = PreviewPopup()

    def setUp(self):
        self.popup.close_popup()
        self.popup._hide_timer.stop()
        self.btn = PreviewHoverButton(text="cand")
        self.payload = {
            "cell": (4, 4), "value": 0.7,
            "value_kind": "heuristic_win_rate",
            "source": "胜率计算得出",
            "cell_value": _board(),
        }
        self.btn.bind_preview(self.popup, lambda _o: self.payload)

    def tearDown(self):
        self.popup._hide_timer.stop()
        self.popup.close_popup()
        self.btn.deleteLater()

    def test_enter_event_shows_popup(self):
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.popup.isVisible(),
                        "enterEvent 必须真实弹出浮层（用户报的 hover 不生效）")
        self.assertIn("策略胜率", self.popup.value_label.text())
        self.assertIn("非安全概率", self.popup.value_label.text())
        self.assertEqual(self.popup._current["cell"], (4, 4))

    def test_leave_event_delays_hide_not_immediate(self):
        """Leave 只起定时器：鼠标要有 220ms 跨过间隙走进浮层点「固定」。"""
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertTrue(self.popup.isVisible())
        self.btn.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertTrue(self.popup.isVisible(),
                        "Leave 后必须仍可见（延迟关闭），否则浮层永远点不到")
        self.assertTrue(self.popup._hide_timer.isActive(),
                        "Leave 必须起延迟关闭定时器")
        # 定时器到点后才真正隐藏
        self.popup._hide_timer.stop()
        self.popup._delayed_hide()
        self.assertFalse(self.popup.isVisible())

    def test_popup_enter_cancels_scheduled_hide(self):
        """浮层自身 Enter 必须取消待关闭（否则走进浮层立刻被关）。"""
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.btn.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertTrue(self.popup._hide_timer.isActive())
        self.popup.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertFalse(self.popup._hide_timer.isActive(),
                         "浮层 Enter 必须取消延迟关闭")
        self.assertTrue(self.popup.isVisible())

    def test_popup_leave_reschedules_hide(self):
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.popup.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.popup.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertTrue(self.popup._hide_timer.isActive(),
                        "离开浮层后应重新安排关闭")

    def test_focus_in_shows_popup_and_focus_out_delays(self):
        """纯键盘通道：Tab 聚焦候选格不产生 Enter 事件。"""
        self.btn.focusInEvent(
            QtGui.QFocusEvent(QtCore.QEvent.FocusIn, QtCore.Qt.TabFocusReason))
        self.assertTrue(self.popup.isVisible(), "FocusIn 必须弹出浮层")
        self.btn.focusOutEvent(
            QtGui.QFocusEvent(QtCore.QEvent.FocusOut, QtCore.Qt.TabFocusReason))
        self.assertTrue(self.popup.isVisible(), "FocusOut 走延迟关闭")
        self.assertTrue(self.popup._hide_timer.isActive())

    def test_enter_without_payload_shows_nothing(self):
        self.btn.bind_preview(self.popup, lambda _o: None)
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.assertFalse(self.popup.isVisible(), "无真实估值时宁可不显示")

    def test_provider_exception_does_not_break_ui(self):
        def boom(_o):
            raise RuntimeError("provider 崩了")
        self.btn.bind_preview(self.popup, boom)
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))  # 不得抛
        self.assertFalse(self.popup.isVisible())

    def test_pinned_popup_not_closed_by_delayed_hide(self):
        self.btn.enterEvent(QtCore.QEvent(QtCore.QEvent.Enter))
        self.popup.toggle_pin(True)
        self.popup._delayed_hide()
        self.assertTrue(self.popup.isVisible(), "固定后延迟关闭不得生效")
        self.popup.toggle_pin(False)

    def test_main_window_board_buttons_are_preview_buttons(self):
        """主窗口帮助棋盘/热力图格必须用真实事件按钮（回归：曾全是裸 QPushButton）。

        ⚠️ 2026-11-07 统一帮助棋盘：帮助页第二套网格 ``help_heatmap_btn_list``
        已删除，帮助页只剩 ``btn_list`` 一套可见棋盘；自动页热力图网格
        ``heatmap_btn_list`` 保留。判据语义不变（仍是"这两处网格都是真实事件
        按钮"），只是帮助侧的载体收敛为一套。
        """
        win = main.MyMainWindow()
        for row in win.btn_list:
            for b in row:
                self.assertIsInstance(b, PreviewHoverButton)
        for row in win.heatmap_btn_list:
            for b in row:
                self.assertIsInstance(b, PreviewHoverButton)
        self.assertFalse(hasattr(win, "help_heatmap_btn_list"),
                         "帮助页第二套热力图网格必须已删除（只允许一个可见棋盘）")
        win.deleteLater()


class TestRealTransitionsWiring(unittest.TestCase):
    """真实状态转移（C++ OPTIMAL 精确层）必须贯通到浮层，且标注概率口径。

    需求（用户 2026-11-08）：帮助页预览不能永远显示"暂无真实状态转移"，
    进入精确前瞻阶段应能看到真实分支分布。约束：只在 C++ 已物化世界的
    OPTIMAL 层提供；其它层缺失时必须显示"暂无"，**绝不合成概率**。
    """

    def test_parse_transitions_normalizes_cpp_output(self):
        from utils.probability import ProbabilityMixin
        raw = {
            "transitions": [
                {"probability": 0.5, "new_cells": [(3, 1)], "label": "1 格展开"},
                {"probability": 0.3, "new_cells": [(1, 1), (2, 1)],
                 "label": "2 格展开"},
            ],
            "transition_meta": {"p_click_safe": 0.9, "basis": "exact_worlds",
                                "world_count": 20},
        }
        out = ProbabilityMixin._parse_transitions(raw)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(sum(o["probability"] for o in out), 0.8)
        self.assertEqual(out[0]["new_cells"], [(3, 1)])

    def test_parse_transitions_returns_none_when_absent(self):
        from utils.probability import ProbabilityMixin
        self.assertIsNone(ProbabilityMixin._parse_transitions({}))
        self.assertIsNone(ProbabilityMixin._parse_transitions({"transitions": []}))
        self.assertIsNone(ProbabilityMixin._parse_transitions(None))

    def test_summarize_marks_exact_worlds_basis(self):
        payload = {
            "cell": (3, 2), "value": 0.81, "value_kind": "exact_safety",
            "source": "C++ 三层决策 (optimal)",
            "transitions": [
                {"probability": 0.5, "new_cells": [(3, 1)], "label": "1 格展开"},
                {"probability": 0.5, "new_cells": [(1, 1)], "label": "1 格展开"},
            ],
            "transition_meta": {"p_click_safe": 0.8148, "basis": "exact_worlds",
                                "world_count": 243},
        }
        info = summarize_payload(payload)
        self.assertTrue(info["has_transitions"])
        self.assertEqual(len(info["transition_rows"]), 2)
        # 口径必须写明：条件概率 + 已枚举世界数（避免被读成无条件概率）
        self.assertIn("条件概率", info["transition_basis_text"])
        self.assertIn("243", info["transition_basis_text"])
        self.assertIn("81.48%", info["transition_basis_text"])

    def test_popup_renders_real_transitions_and_basis(self):
        popup = PreviewPopup()
        popup.set_payload({
            "cell": (3, 2), "value": 0.81, "value_kind": "exact_safety",
            "transitions": [
                {"probability": 0.50, "new_cells": [(3, 1)], "label": "1 格展开"},
                {"probability": 0.25, "new_cells": [(1, 1), (2, 1)],
                 "label": "2 格展开"},
            ],
            "transition_meta": {"p_click_safe": 0.8, "basis": "exact_worlds",
                                "world_count": 100},
        })
        self.assertIn("状态转移分支", popup.transition_label.text())
        self.assertIn("50.00%", popup.transition_label.text())
        self.assertNotEqual(popup.transition_label.text(), NO_TRANSITION_TEXT)
        self.assertIn("条件概率", popup.note_label.text())
        popup.close_popup()


class TestTransitionReasonAndNumberColors(unittest.TestCase):
    """2026-11-08 修复：无真实转移时说明准确原因；7x7 棋盘数字按经典扫雷配色。"""

    def test_world_count_exceeded_explains_real_reason(self):
        text = transition_hint_text({"basis": "unavailable",
                                     "reason": "world_count_exceeded"})
        self.assertIn("世界过多", text)
        self.assertIn("不显示合成概率", text)
        self.assertNotIn("暂未产出", text)

    def test_missing_reason_falls_back_to_generic_hint(self):
        self.assertEqual(transition_hint_text({}), NO_TRANSITION_HINT)
        self.assertIn("不显示合成概率", NO_TRANSITION_HINT)

    def test_unknown_reason_is_not_silently_dropped(self):
        text = transition_hint_text({"reason": "some_native_error"})
        self.assertIn("some_native_error", text)
        self.assertIn("不显示合成概率", text)

    def test_popup_uses_reason_hint(self):
        popup = PreviewPopup()
        popup.set_payload({
            "cell": (3, 3), "value": 0.5, "value_kind": "exact_safety",
            "transition_meta": {"basis": "unavailable",
                                "reason": "world_count_exceeded"},
        })
        self.assertIn("世界过多", popup.note_label.text())
        popup.close_popup()

    def test_mini_open_digits_are_colored(self):
        # 0-8 每个编码都必须有专属颜色，且不再沿用旧的单一深色样式。
        styles = [mini_open_style(n) for n in range(0, 9)]
        self.assertEqual(len(set(styles)), 9)
        for s in styles:
            self.assertIn("color:", s)
            self.assertIn("bold", s)
        self.assertIn("rgb(25, 95, 225)", mini_open_style(1))   # 1=蓝
        self.assertIn("rgb(205, 45, 40)", mini_open_style(3))    # 3=红

    def test_transition_boards_carry_full_7x7_boards(self):
        """状态转移 = 多个 7×7 可达局面棋盘 + 概率（不是文字分支列表）。"""
        b1 = [[9] * 7 for _ in range(7)]
        b1[3][3] = 1
        b2 = [[0] * 7 for _ in range(7)]
        b2[3][3] = 2
        boards = transition_boards([
            {"probability": 0.25, "label": "2 格", "board": b2},
            {"probability": 0.75, "label": "1 格", "board": b1},
            {"probability": 0.0, "label": "无局面"},  # 缺 board，跳过
        ])
        self.assertEqual(len(boards), 2)
        self.assertEqual(boards[0]["pct"], "75.00%")  # 按概率降序
        self.assertEqual(boards[0]["board"][3][3], 1)
        self.assertEqual(len(boards[0]["board"]), 7)
        self.assertTrue(all(len(row) == 7 for row in boards[0]["board"]))

    def test_transition_boards_rejects_wrong_shape(self):
        self.assertEqual(transition_boards([
            {"probability": 0.5, "board": [[9] * 3]},
        ]), [])

    def test_popup_renders_multiple_branch_boards(self):
        popup = PreviewPopup()
        b1 = [[9] * 7 for _ in range(7)]
        b1[3][3] = 1
        b2 = [[9] * 7 for _ in range(7)]
        b2[3][3] = 3
        popup.set_payload({
            "cell": (5, 5), "value": 0.8, "value_kind": "exact_safety",
            "transitions": [
                {"probability": 0.6, "label": "1 格", "board": b1},
                {"probability": 0.4, "label": "3 格", "board": b2},
            ],
            "transition_meta": {"p_click_safe": 0.8,
                                "basis": "exact_worlds",
                                "world_count": 20},
        })
        self.assertIn("可达局面", popup.transition_label.text())
        # offscreen 下控件未顶层 show，isVisible() 恒 False；用 isHidden()
        # 断言"已显式 show"。用户裁决「一行一个」后布局为纵向 QVBoxLayout，
        # 每行是独立的 QWidget（概率在左、7×7 棋盘在右）。
        self.assertFalse(popup.branches_scroll.isHidden())
        self.assertEqual(popup.branches_layout.count(), 2)
        self.assertIsInstance(popup.branches_layout, QtWidgets.QVBoxLayout)
        # 每个分支的 7×7 棋盘中心数字必须来自真实 board，概率来自真实 payload
        texts = []
        for i in range(popup.branches_layout.count()):
            row = popup.branches_layout.itemAt(i).widget()
            self.assertIsNotNone(row, "每个分支必须独占一行 QWidget")
            hl = row.layout()
            pct = hl.itemAt(0).widget().text()
            frame = hl.itemAt(1).widget()
            center = frame.layout().itemAtPosition(3, 3).widget()
            texts.append((pct.splitlines()[-1], center.text()))
        self.assertEqual(texts, [("60.00%", "1"), ("40.00%", "3")])
        # 格子放大到 22px，且每行有独立边框（避免挤在一起）
        rows = [popup.branches_layout.itemAt(i).widget()
                for i in range(popup.branches_layout.count())]
        self.assertTrue(all("popupBranchRow" in r.objectName() for r in rows))
        popup.close_popup()

    def test_mini_cell_style_covers_all_codes(self):
        self.assertEqual(mini_cell_style(9)[0], "")
        self.assertEqual(mini_cell_style(10)[0], "雷")
        self.assertEqual(mini_cell_style(11)[0], "✓")
        self.assertEqual(mini_cell_style(2)[0], "2")
        self.assertEqual(mini_cell_style(0)[0], "")
        self.assertEqual(mini_cell_style(-1)[0], "")

    def test_zero_cell_is_blank_like_classic_minesweeper(self):
        popup = PreviewPopup()
        cv = np.full((9, 9), 0, dtype="int32")
        cv[4, 4] = 0
        popup.set_payload({"cell": (5, 5), "cell_value": cv})
        center = popup._mini_labels[3 * 7 + 3]
        self.assertEqual(center.text(), "")
        popup.close_popup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
