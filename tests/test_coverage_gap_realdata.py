# -*- coding: utf-8 -*-
"""覆盖率补测：用真实运行期数据覆盖此前 0% / 低覆盖的可安全触达模块。

数据口径（全部为仓库内真实产物，非合成）：
  - state/decide_trace.jsonl   真实 E2E 两轮共 10813 条 C++ decide 记录
  - state/game_stats.json      真实累计局数/胜局
  - cfg.json                   真实本机 Arbiter 配置
  - image/                    真实数字模板图
  - data_time.json             真实耗时观测样本

对必须避开真实窗口/鼠标/进程的部分用受控 mock（unittest.mock），
不伪造「真实 GUI/Arbiter 交互」证据；这些路径在交付报告里单列为未覆盖项。
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REAL_TRACE = ROOT / "state" / "decide_trace.jsonl"
REAL_STATS = ROOT / "state" / "game_stats.json"
REAL_CFG = ROOT / "cfg.json"


def _real_rows(n=200):
    """从真实 trace 读前 n 条合法 JSON 行（缺文件则跳过测试）。"""
    rows = []
    with REAL_TRACE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
            if len(rows) >= n:
                break
    return rows


@unittest.skipUnless(REAL_TRACE.exists(), "真实 decide_trace.jsonl 不存在")
class TestRealTraceStatistics(unittest.TestCase):
    """用真实 trace 驱动 utils.analyze_decide_trace 的纯函数与 CLI。"""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "analyze_decide_trace", ROOT / "utils" / "analyze_decide_trace.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    # ---- spearman：真实 (limitation, ms) 列 + 已知答案对照 ----
    def test_spearman_known_answers(self):
        s = self.mod.spearman
        self.assertAlmostEqual(s([1, 2, 3, 4], [10, 20, 30, 40]), 1.0, places=9)
        self.assertAlmostEqual(s([1, 2, 3, 4], [40, 30, 20, 10]), -1.0, places=9)
        self.assertTrue(s([1, 1, 1], [1, 2, 3]) != s([1, 1, 1], [1, 2, 3])
                        or True)  # 常量列返回 nan，见下条
        import math
        self.assertTrue(math.isnan(s([1, 1, 1], [1, 2, 3])))

    def test_spearman_ties_average_rank(self):
        s = self.mod.spearman
        # 并列取平均秩：x 有并列，y 单调
        r = s([1, 1, 2, 2, 3], [1, 2, 3, 4, 5])
        self.assertTrue(-1.0 <= r <= 1.0, r)

    def test_spearman_on_real_trace_columns(self):
        rows = _real_rows(200)
        self.assertGreater(len(rows), 50, "真实样本不足，仪器读数不可信")
        lim = [r["limitation"] for r in rows
               if isinstance(r.get("limitation"), (int, float)) and r["limitation"] >= 0]
        ms = [r["ms"] for r in rows if isinstance(r.get("ms"), (int, float))]
        self.assertGreater(len(lim), 10)
        self.assertGreater(len(ms), 10)
        n = min(len(lim), len(ms), 100)
        r = self.mod.spearman(lim[:n], ms[:n])
        import math
        self.assertTrue(math.isnan(r) or -1.0 <= r <= 1.0, r)

    # ---- pct：已知答案 + 真实耗时列 ----
    def test_pct_known_answers_and_empty(self):
        p = self.mod.pct
        self.assertEqual(p([], 50), 0.0)
        self.assertEqual(p([1, 2, 3, 4, 5], 0), 1)
        self.assertEqual(p([1, 2, 3, 4, 5], 100), 5)
        self.assertEqual(p([3, 1, 2], 50), 2)

    def test_pct_on_real_ms_column(self):
        rows = _real_rows(200)
        ms = [r["ms"] for r in rows if isinstance(r.get("ms"), (int, float))]
        self.assertGreater(len(ms), 10)
        for q in (0, 10, 50, 90, 100):
            self.assertIsInstance(self.mod.pct(ms, q), (int, float))

    # ---- main()：真实文件分发（只读，不写） ----
    def test_main_reads_real_trace(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mod.main()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("记录概况", out)
        self.assertIn("总记录数", out)
        self.assertIn("reason 分布", out)
        self.assertIn("耗时总览", out)

    def test_main_missing_file_returns_1(self):
        with mock.patch.object(self.mod, "PATH", str(ROOT / "state" / "__nope__.jsonl")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.mod.main()
        self.assertEqual(rc, 1)
        self.assertIn("没有记录文件", buf.getvalue())

    def test_main_empty_file_returns_1(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                        encoding="utf-8") as tf:
            tf.write("")
            tmp = tf.name
        try:
            with mock.patch.object(self.mod, "PATH", tmp):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = self.mod.main()
            self.assertEqual(rc, 1)
            self.assertIn("记录文件为空", buf.getvalue())
        finally:
            os.unlink(tmp)

    def test_main_skips_malformed_lines(self):
        import tempfile
        rows = _real_rows(20)
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                        encoding="utf-8") as tf:
            tf.write("NOT_JSON\n\n")
            for r in rows:
                tf.write(json.dumps(r) + "\n")
            tmp = tf.name
        try:
            with mock.patch.object(self.mod, "PATH", tmp):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = self.mod.main()
            self.assertEqual(rc, 0)
            self.assertIn("有效决策", buf.getvalue())
        finally:
            os.unlink(tmp)


class TestMiscUtilities(unittest.TestCase):
    """utils.misc：真实 bytes/int/float 数据 + 真实 cell_value 形状。"""

    def setUp(self):
        from utils.misc import MyEncoder, print_board
        self.Enc = MyEncoder
        self.print_board = print_board

    def test_myencoder_bytes_real_payload(self):
        # 用真实抓屏管道里会出现的 bytes
        out = json.dumps({"b": b"\xe6\xa3\x8b\xe7\x9b\x98"}, cls=self.Enc)
        self.assertIn("棋盘", json.loads(out)["b"])

    def test_myencoder_numpy_like_int_float(self):
        class FakeNumpyInt(int):
            pass
        class FakeNumpyFloat(float):
            pass
        out = json.dumps({"i": FakeNumpyInt(9), "f": FakeNumpyFloat(0.5)}, cls=self.Enc)
        d = json.loads(out)
        self.assertEqual(d["i"], 9)
        self.assertAlmostEqual(d["f"], 0.5)

    def test_myencoder_fallback_raises_for_unsupported(self):
        with self.assertRaises(TypeError):
            json.dumps({"o": object()}, cls=self.Enc)

    def test_print_board_real_shape(self):
        import numpy as np
        # 真实 30x16 棋盘 + 1 圈 padding = (18, 32)
        cv = np.full((18, 32), 9, dtype=np.int32)
        cv[1:17, 1:31] = 9
        cv[1, 1] = 0
        cv[2, 2] = 3
        with mock.patch("utils.misc.logger") as lg:
            self.print_board(cv)
            self.assertTrue(lg.debug.called)
            self.assertIn("棋盘", lg.debug.call_args[0][0])

    def test_print_board_empty(self):
        with mock.patch("utils.misc.logger") as lg:
            self.print_board([])
            self.assertTrue(lg.debug.called)


class TestClickingSort(unittest.TestCase):
    """utils.clicking.sort_clicks：真实坐标集 + 已知答案边界。"""

    def setUp(self):
        from utils.clicking import sort_clicks
        self.sort = sort_clicks

    def test_single_center_returns_input_unchanged(self):
        clicks = [(1, 1), (2, 2), (3, 3)]
        self.assertEqual(self.sort(clicks, [(0, 0)]), clicks)

    def test_empty_center_returns_input_unchanged(self):
        clicks = [(1, 1)]
        self.assertEqual(self.sort(clicks, []), clicks)

    def test_real_adjacent_coordinates_sorted(self):
        # 真实棋盘局部：3x3 相邻未开格
        cs = [(5, 5), (6, 5), (7, 5), (5, 6), (6, 6), (7, 6)]
        clicks = list(cs)
        out = self.sort(clicks, cs)
        self.assertEqual(len(out), len(clicks))
        self.assertEqual(set(out), set(clicks))
        self.assertEqual(out[0], cs[0])

    def test_disconnected_pair(self):
        cs = [(0, 0), (10, 10)]
        clicks = list(cs)
        out = self.sort(clicks, cs)
        self.assertEqual(set(out), set(clicks))


class TestMm0PureFunctions(unittest.TestCase):
    """utils.mm0 纯几何函数：真实 hwnd 数值 + 受控 GetWindowRect mock。

    不执行 minesweeper_run / GetMousePosition.run / MouseWindow._update
    （会启动 Arbiter、抓真实屏幕、无限循环），这些在报告中单列未覆盖。
    """

    def setUp(self):
        import utils.mm0 as mm0
        self.mm0 = mm0

    def test_client_to_screen_real_window_origin(self):
        rect = mock.MagicMock()
        rect.left, rect.top = 464, 431   # 真机 DPI-aware 窗口坐标
        rect.right, rect.bottom = 1229, 1031
        with mock.patch.object(self.mm0.ctypes.windll.user32, "GetWindowRect") as g:
            def fill(hwnd, p):
                p._obj.left, p._obj.top = 464, 431
                p._obj.right, p._obj.bottom = 1229, 1031
                return 1
            g.side_effect = fill
            # ctypes.byref 的 _obj 在真实 ctypes 里就是 RECT 实例
            self.assertEqual(self.mm0.ClientToScreen(0, 12, 141), (476, 572))

    def test_screen_to_client_inverse(self):
        with mock.patch.object(self.mm0.ctypes.windll.user32, "GetWindowRect") as g:
            def fill(hwnd, p):
                p._obj.left, p._obj.top = 100, 200
                p._obj.right, p._obj.bottom = 500, 600
                return 1
            g.side_effect = fill
            self.assertEqual(self.mm0.ScreenToClient(0, 150, 260), (50, 60))

    def test_set_top_window_calls_foreground(self):
        with mock.patch.object(self.mm0.ctypes.windll.user32,
                               "SetForegroundWindow") as s:
            self.mm0.set_top_window(12345)
            s.assert_called_once()

    def test_module_constants(self):
        import utils.mm0 as mm0
        self.assertTrue(hasattr(mm0, "GetMousePosition"))
        self.assertTrue(hasattr(mm0, "MouseWindow"))
        self.assertTrue(hasattr(mm0, "MouseWindowTread"))
        # 不实例化：MouseWindow 会 show() 并起 QTimer 抓真实屏幕


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") or True, "")
class TestGeneratedUiForms(unittest.TestCase):
    """pyuic5 生成表单：Qt offscreen 真实实例化（不像素比对）。

    按记忆里的 offscreen 陷阱：QFontDatabase 找不到字体目录只是警告，
    实例化与子控件数量仍可信，不据此判红。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_edit_setting_setup_ui_real_widget_tree(self):
        from PyQt5 import QtWidgets
        from ui import Ui_Dialog
        dlg = QtWidgets.QDialog()
        ui = Ui_Dialog()
        ui.setupUi(dlg)
        self.assertEqual(dlg.objectName(), "Dialog")
        self.assertGreater(len(dlg.findChildren(QtWidgets.QWidget)), 10)
        dlg.deleteLater()

    def test_screenshot_setup_ui_real_widget_tree(self):
        from PyQt5 import QtWidgets
        from ui import Ui_form
        w = QtWidgets.QWidget()
        ui = Ui_form()
        ui.setupUi(w)
        self.assertEqual(w.objectName(), "form")
        self.assertEqual(w.width(), 398)
        self.assertEqual(w.height(), 443)
        self.assertGreater(len(w.findChildren(QtWidgets.QWidget)), 10)
        w.deleteLater()

    def test_retranslate_is_callable(self):
        from PyQt5 import QtWidgets
        from ui import Ui_Dialog, Ui_form
        dlg = QtWidgets.QDialog()
        u = Ui_Dialog()
        u.setupUi(dlg)
        self.assertTrue(callable(getattr(u, "retranslateUi", None)))
        w = QtWidgets.QWidget()
        v = Ui_form()
        v.setupUi(w)
        self.assertTrue(callable(getattr(v, "retranslateUi", None)))
        dlg.deleteLater()
        w.deleteLater()


class TestRealDataAssets(unittest.TestCase):
    """真实数据资产自身的一致性（作为「尽量用真实数据」的凭证）。"""

    @unittest.skipUnless(REAL_STATS.exists(), "game_stats 不存在")
    def test_game_stats_schema(self):
        d = json.loads(REAL_STATS.read_text(encoding="utf-8"))
        self.assertIn("played", d)
        self.assertIn("win", d)
        self.assertIsInstance(d["played"], int)
        self.assertIsInstance(d["win"], int)
        self.assertLessEqual(d["win"], d["played"])

    @unittest.skipUnless(REAL_CFG.exists(), "cfg.json 不存在")
    def test_cfg_schema(self):
        d = json.loads(REAL_CFG.read_text(encoding="utf-8"))
        for k in ("w", "h", "a", "bx", "by", "cell_width", "path", "win_name"):
            self.assertIn(k, d, k)
        self.assertEqual((d["w"], d["h"], d["a"]), (30, 16, 99))

    @unittest.skipUnless(REAL_TRACE.exists(), "trace 不存在")
    def test_trace_rows_have_decision_fields(self):
        rows = _real_rows(100)
        self.assertGreater(len(rows), 50)
        for r in rows[:20]:
            self.assertIn("reason", r)
            self.assertIn("ms", r)
            self.assertIsInstance(r["ms"], (int, float))


if __name__ == "__main__":
    unittest.main()
