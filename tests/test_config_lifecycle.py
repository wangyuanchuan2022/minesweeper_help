# -*- coding: utf-8 -*-
"""配置/资源路径与停止生命周期测试。

覆盖本轮修复的三件事（见 review/fix-config-lifecycle.md）：

1. **路径解析不依赖 cwd**：``setting`` 的 ``PROJECT_ROOT``/``CFG_PATH``/
   ``project_path()`` 与 ``Solver`` 的 cfg/data 读写都基于显式资源根；
   在**非项目目录**启动时行为一致（子进程实测，不是 mock）。
2. **reload() 重算派生字段**：改配置后 ``p``（雷密度）与截图补丁尺寸必须
   跟着变，不能留构造期旧值。
3. **协作式取消**：``request_cancel()`` 置位后 ``run()``/``play()`` 在安全点
   自行退出并走 ``finally``；GUI 的 ``terminate()`` 只在超时兜底。

运行::

    python -m unittest tests.test_config_lifecycle -v

⚠️ 本模块用 ``MINESWEEPER_HELP_NO_CHDIR=1`` 关掉 setting 导入期的 cwd 归一，
才能在子进程里真正验证「cwd 与资源根解耦」，否则归一化会把断言变成同义反复。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 本模块必须在导入 setting 之前设置：关闭 cwd 归一，让「路径解析」独立可测。
os.environ.setdefault("MINESWEEPER_HELP_NO_CHDIR", "1")

import setting  # noqa: E402
from tests.helpers import make_full_solver, make_solver  # noqa: E402

PY = sys.executable


def _mk_tmp_dir(prefix, parent=None):
    """在工作区内建临时目录（DSH 沙箱下系统 Temp 清理会 PermissionError）。

    ⚠️ 不用 ``tempfile.mkdtemp``：它在工作区内创建的目录后续无法在其下建子目录
    （实测 WinError 5，沙箱 ACE 只对 mkdtemp 之前存在的路径生效）。
    用 ``os.makedirs`` 显式建目录，行为可控。
    """
    parent = parent or os.path.join(_ROOT, ".tools", "tmp")
    os.makedirs(parent, exist_ok=True)
    path = os.path.join(parent, f"{prefix}{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=True)
    return path


def _run_in_dir(cwd, code):
    """在指定 cwd 的子进程里执行代码，返回 (returncode, stdout, stderr)。

    子进程通过 PYTHONPATH 指向项目根，cwd 指向临时目录——这样「能 import 到
    项目模块」与「进程 cwd 是别处」两件事同时成立，正是要验证的场景。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = _ROOT
    env["MINESWEEPER_HELP_NO_CHDIR"] = "1"
    env.pop("QT_QPA_PLATFORM", None)
    p = subprocess.run(
        [PY, "-c", code], cwd=cwd, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout or "", p.stderr or ""


class TestProjectRootResolution(unittest.TestCase):
    """资源根解析：绝对路径、与 cwd 无关、可被显式覆盖。"""

    def test_project_root_is_this_repo(self):
        self.assertEqual(os.path.normcase(setting.PROJECT_ROOT),
                         os.path.normcase(_ROOT))

    def test_derived_paths_are_absolute_and_exist(self):
        for path in (setting.CFG_PATH, setting.IMAGE_DIR, setting.STATE_DIR):
            self.assertTrue(os.path.isabs(path), f"{path} 必须是绝对路径")
            self.assertTrue(os.path.exists(path), f"{path} 应存在")
        self.assertTrue(os.path.isabs(setting.DATA_PATH))

    def test_project_path_three_inputs(self):
        """≥3 元素输入：相对路径、多级相对路径、已绝对路径。"""
        cases = [
            ("cfg.json", os.path.join(setting.PROJECT_ROOT, "cfg.json")),
            ("image/exit.bmp", os.path.join(setting.PROJECT_ROOT, "image", "exit.bmp")),
            (setting.CFG_PATH, setting.CFG_PATH),  # 绝对路径原样返回
        ]
        self.assertGreaterEqual(len(cases), 3)
        for rel, expected in cases:
            got = setting.project_path(rel)
            self.assertTrue(os.path.isabs(got))
            self.assertEqual(os.path.normcase(got), os.path.normcase(expected))

    def test_load_cfg_reads_absolute_path(self):
        cfg = setting.load_cfg()
        self.assertEqual(set(cfg), set(setting.cfg))
        self.assertIn("win_name", cfg)

    def test_load_cfg_explicit_path(self):
        # 临时目录放工作区（.tools/tmp）：系统 Temp 在 DSH 沙箱下 cleanup 会被拒绝
        parent = os.path.join(_ROOT, ".tools", "tmp")
        os.makedirs(parent, exist_ok=True)
        alt = os.path.join(parent, f"cfg_alt_{uuid.uuid4().hex}.json")
        self.addCleanup(os.unlink, alt)
        with open(alt, "w", encoding="utf-8") as f:
            json.dump({"win_name": "x", "w": 3, "h": 2, "a": 1,
                       "bx": 0, "by": 0, "cell_width": 20,
                       "limit": 1, "speed": 1.0, "path": "p"}, f)
        self.assertEqual(setting.load_cfg(alt)["w"], 3)


class TestForeignCwdResolution(unittest.TestCase):
    """非项目目录启动：路径解析必须与 cwd 无关（子进程实测）。"""

    def setUp(self):
        self.foreign = _mk_tmp_dir("msh_foreign_")
        self.addCleanup(shutil.rmtree, self.foreign, True)

    def test_setting_imports_from_foreign_cwd(self):
        code = (
            "import os, setting;"
            "print('ROOT', setting.PROJECT_ROOT);"
            "print('CFG', setting.CFG_PATH);"
            "print('CWD', os.getcwd());"
            "print('WIN', setting.cfg['win_name'])"
        )
        rc, out, err = _run_in_dir(self.foreign, code)
        self.assertEqual(rc, 0, f"从非项目 cwd 导入 setting 失败：{err[-800:]}")
        lines = dict(line.split(" ", 1) for line in out.strip().splitlines() if " " in line)
        self.assertEqual(os.path.normcase(lines["ROOT"]), os.path.normcase(_ROOT))
        self.assertEqual(os.path.normcase(lines["CFG"]),
                         os.path.normcase(setting.CFG_PATH))
        # cwd 确实不是项目根——否则这条用例退化成同义反复
        self.assertNotEqual(os.path.normcase(lines["CWD"]), os.path.normcase(_ROOT))
        self.assertIn("Minesweeper", lines["WIN"])

    def test_solver_cfg_from_foreign_cwd(self):
        """Solver 构造/reload 在非项目 cwd 下能读到同一份配置。"""
        code = (
            "import json;"
            "from utils.solver import Solver;"
            "Solver.load_img = lambda self: None;"  # 模板路径属 vision.py（范围外）
            "s = Solver();"
            "print('W', s.w, 'H', s.h, 'A', s.a, 'CW', s.cell_width);"
            "print('P', repr(s.p))"
        )
        rc, out, err = _run_in_dir(self.foreign, code)
        self.assertEqual(rc, 0, f"非项目 cwd 构造 Solver 失败：{err[-800:]}")
        first, second = out.strip().splitlines()[:2]
        w, h, a, cw = [int(x) for x in first.split()[1::2]]
        self.assertEqual((w, h, a, cw),
                         (setting.cfg["w"], setting.cfg["h"],
                          setting.cfg["a"], setting.cfg["cell_width"]))
        self.assertAlmostEqual(float(second.split()[1]), a / (w * h))

    def test_relative_asset_name_resolves_to_repo(self):
        code = (
            "import os, setting;"
            "p = setting.project_path('image/exit.bmp');"
            "print('ABS', os.path.isabs(p));"
            "print('EXISTS', os.path.exists(p))"
        )
        rc, out, err = _run_in_dir(self.foreign, code)
        self.assertEqual(rc, 0, err[-800:])
        self.assertIn("ABS True", out)
        self.assertIn("EXISTS True", out)


class TestStatsPathsIndependentOfCwd(unittest.TestCase):
    """data.json / state 落盘用绝对路径：cwd 变了也不会写错位置。"""

    def setUp(self):
        self.tmpdir = _mk_tmp_dir("msh_stats_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.data_path = os.path.join(self.tmpdir, "data.json")
        p1 = mock.patch("utils.solver._DATA_PATH", self.data_path)
        p2 = mock.patch("utils.solver._GS_STATE_PATH",
                        os.path.join(self.tmpdir, "state", "game_stats.json"))
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_load_and_flush_use_patched_absolute_path(self):
        solver = make_solver()
        solver.stats_data = {"50": {"win": 1, "lose": 0}}
        cwd_before = os.getcwd()
        foreign = os.path.join(self.tmpdir, "cwd_probe")
        os.makedirs(foreign, exist_ok=True)
        try:
            os.chdir(foreign)
            solver._flush_stats()
        finally:
            os.chdir(cwd_before)
        self.assertTrue(os.path.exists(self.data_path),
                        "统计必须写进项目绝对路径，而不是 cwd 下的 data.json")
        self.assertFalse(os.path.exists(os.path.join(foreign, "data.json")))
        # 读回也走同一绝对路径
        fresh = make_solver()
        self.assertEqual(fresh._load_stats(), {"50": {"win": 1, "lose": 0}})

    def test_missing_stats_returns_empty(self):
        solver = make_solver()
        self.assertEqual(solver._load_stats(), {})

    def test_game_stats_roundtrip_in_absent_dir(self):
        """state 目录不存在时应自建（绝对路径），而不是崩在 cwd 上。"""
        from utils.solver import _gs_load, _gs_save
        self.assertEqual(_gs_load(), {})
        _gs_save(7, 3)
        self.assertEqual(_gs_load(), {"played": 7, "win": 3})


class TestReloadRecomputesDerivedFields(unittest.TestCase):
    """reload() 必须重算依赖配置的派生字段（至少 p）。"""

    def _light_solver(self):
        solver = make_solver(w=5, h=5, a=3)
        solver.speed = 1.0
        solver.limit = 10
        solver.cfg = {}
        return solver

    def test_p_recomputed_after_reload(self):
        solver = self._light_solver()
        new_cfg = {"w": 20, "h": 10, "a": 40, "bx": 1, "by": 2,
                   "cell_width": 36, "limit": 5, "speed": 2.0,
                   "path": "x", "win_name": "w"}
        with mock.patch("setting.load_cfg", return_value=new_cfg), \
                mock.patch("utils.solver.Solver.load_img", lambda self: None):
            solver.reload()
        self.assertEqual((solver.w, solver.h, solver.a), (20, 10, 40))
        self.assertAlmostEqual(solver.p, 40 / (20 * 10))
        # 派生补丁尺寸随 cell_width=36 重算（24 时是 18x12）
        self.assertEqual((solver.screenshot_h, solver.screenshot_w),
                         (int(36 * 7 / 9), int(36 * 5 / 9)))
        self.assertEqual(solver.cell_width, 36)
        self.assertEqual(solver.limit, 5)
        self.assertEqual(solver.speed, 2.0)

    def test_reload_changes_p_from_constructor_value(self):
        """对照：构造期 p 与 reload 后 p 不同，证明真的重算了（非恒等）。"""
        solver = self._light_solver()
        solver.p = 3 / 25  # 构造期值
        new_cfg = {"w": 10, "h": 10, "a": 50, "bx": 0, "by": 0,
                   "cell_width": 24, "limit": 1, "speed": 1.0,
                   "path": "x", "win_name": "w"}
        with mock.patch("setting.load_cfg", return_value=new_cfg), \
                mock.patch("utils.solver.Solver.load_img", lambda self: None):
            solver.reload()
        self.assertAlmostEqual(solver.p, 0.5)
        self.assertNotAlmostEqual(solver.p, 3 / 25)

    def test_zero_area_cfg_does_not_crash(self):
        solver = self._light_solver()
        zero = {"w": 0, "h": 0, "a": 0, "bx": 0, "by": 0,
                "cell_width": 24, "limit": 1, "speed": 1.0,
                "path": "x", "win_name": "w"}
        with mock.patch("setting.load_cfg", return_value=zero), \
                mock.patch("utils.solver.Solver.load_img", lambda self: None):
            solver.reload()
        self.assertEqual(solver.p, 0.0)


class TestCooperativeCancel(unittest.TestCase):
    """协作式取消：标志位、run/play 安全点、强杀只作兜底。"""

    def test_flag_set_clear_cycle(self):
        solver = make_solver()
        self.assertFalse(solver.cancel_requested())
        solver.request_cancel()
        self.assertTrue(solver.cancel_requested())
        self.assertTrue(solver._stop_requested())
        solver.clear_cancel()
        self.assertFalse(solver.cancel_requested())

    def test_light_instance_supports_cancel(self):
        """Solver.__new__ 轻量实例（无 __init__）也必须支持取消。"""
        solver = make_solver()
        solver.request_cancel()  # 不应抛 AttributeError
        self.assertTrue(solver.cancel_requested())

    def test_run_preserves_cancel_flag_set_after_start(self):
        """run() 不清标志：start() 之后、run() 之前的取消必须生效（不被吞掉）。

        清除时机只有 ``start()`` 一处；若在 ``run()`` 里清，「刚点启动、线程还没
        跑到 run()，用户就点了停止」这一窗口的取消会被静默吞掉。
        """
        solver = make_solver(is_play=True)
        solver.value = 1
        called = []
        solver.request_cancel()
        with mock.patch.object(type(solver), "reload", lambda self: None), \
                mock.patch.object(type(solver), "play",
                                  lambda self, limit: called.append(limit)):
            solver.run()
        self.assertEqual(called, [1])
        self.assertTrue(solver.cancel_requested(),
                        "run() 不得吞掉用户在 start() 之后点的取消")

    def test_start_clears_stale_flag(self):
        """start() 负责清上一轮残留标志（真实 QThread 生命周期）。"""
        solver = make_full_solver(w=3, h=3, a=1)
        solver.value = 1
        solver.is_play = False
        solver.request_cancel()
        self.assertTrue(solver.cancel_requested())
        with mock.patch.object(type(solver), "run", lambda self: None):
            solver.start()
            solver.wait(3000)
        self.assertFalse(solver.cancel_requested(),
                         "start() 必须清掉上一轮的取消标志")

    def test_run_dispatches_help_when_not_play(self):
        solver = make_solver(is_play=False)
        called = []
        with mock.patch.object(type(solver), "reload", lambda self: None), \
                mock.patch.object(type(solver), "help",
                                  lambda self: called.append("help")):
            solver.run()
        self.assertEqual(called, ["help"])

    def test_play_exits_at_safety_point_when_cancelled(self):
        """取消标志已置位时，play() 应在主循环安全点立即返回（不抓屏不推理）。"""
        solver = make_solver(w=5, h=5, a=3)
        solver.is_play = True
        solver.stats_data = None  # 不让 finally 的 _flush_stats 碰真实 data.json
        solver._last_heatmap = {}
        solver.request_cancel()
        scanned = []
        with mock.patch("utils.solver.win32gui.FindWindow", return_value=0), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)), \
                mock.patch("utils.solver.pyautogui.click", lambda *a, **k: None), \
                mock.patch("utils.solver.time.sleep", lambda *a, **k: None), \
                mock.patch.object(type(solver), "complete_scan",
                                  lambda self, cv, no_10=True: scanned.append(1) or cv):
            solver.play(1)
        self.assertEqual(scanned, [], "已取消时不该再进入扫描/推理链")
        # play() 自身不清标志（清理由 start()/run() 负责），供调用方判读
        self.assertTrue(solver.cancel_requested())

    def test_play_scan_safety_point(self):
        """扫描后安全点：这一轮扫描完成即退出，不再进入推理决策链。"""
        solver = make_solver(w=3, h=3, a=1)
        solver.is_play = True
        solver.stats_data = None  # finally 的 _flush_stats 不写真实 data.json
        solver._last_heatmap = {}
        events = []

        def fake_scan(self_, cell_value, no_10=True):
            events.append("scan")
            self_.request_cancel()  # 模拟扫描期间用户点了停止
            return cell_value

        with mock.patch("utils.solver.win32gui.FindWindow", return_value=0), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)), \
                mock.patch("utils.solver.pyautogui.click", lambda *a, **k: None), \
                mock.patch("utils.solver.time.sleep", lambda *a, **k: None), \
                mock.patch.object(type(solver), "_grab_screen_bgr",
                                  staticmethod(lambda: None)), \
                mock.patch("utils.solver.Solver._locate",
                           lambda self, f, screen=None: (False, 0, 0)), \
                mock.patch.object(type(solver), "complete_scan", fake_scan), \
                mock.patch.object(type(solver), "mine_clear1",
                                  lambda self, cv, *a, **k: events.append("mine_clear1") or cv):
            solver.play(1)
        self.assertEqual(events, ["scan"], "扫描后安全点必须拦住后续推理/决策")

    def test_help_exits_at_entry_when_cancelled(self):
        """帮助模式入口安全点：已请求停止时不再抓屏/扫描/推理。"""
        solver = make_solver(w=3, h=3, a=1)
        solver.request_cancel()
        scanned = []
        with mock.patch("utils.solver.win32gui.FindWindow", return_value=0), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)), \
                mock.patch.object(type(solver), "complete_scan",
                                  lambda self, cv, no_10=True: scanned.append(1) or cv):
            solver.help()
        self.assertEqual(scanned, [], "已取消时帮助模式不该进入扫描")

    def test_help_scan_safety_point_stops_before_reasoning(self):
        """帮助模式扫描后安全点：扫描完成即退出，不再进入推理链。"""
        solver = make_solver(w=3, h=3, a=1)
        events = []

        def fake_scan(self_, cell_value, no_10=True):
            events.append("scan")
            self_.request_cancel()  # 模拟扫描期间用户点了停止
            return cell_value

        with mock.patch("utils.solver.win32gui.FindWindow", return_value=0), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)), \
                mock.patch.object(type(solver), "complete_scan", fake_scan), \
                mock.patch.object(type(solver), "mine_clear1",
                                  lambda self, cv, *a, **k: events.append("mine_clear1") or cv):
            solver.help()
        self.assertEqual(events, ["scan"], "帮助模式扫描后安全点必须拦住后续推理")


class TestGuiStopHelper(unittest.TestCase):
    """main._stop_solver_thread：协作取消优先，超时才 terminate 兜底。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import main
        cls.main = main

    def _win(self):
        return self.main.MyMainWindow.__new__(self.main.MyMainWindow)

    def test_cooperative_path_does_not_terminate(self):
        class FakeThread:
            def __init__(self):
                self.cancelled = 0
                self.terminated = 0

            def isRunning(self):
                return True

            def request_cancel(self):
                self.cancelled += 1

            def wait(self, ms=0):
                return True  # 协作退出成功

            def terminate(self):
                self.terminated += 1

        t = FakeThread()
        self._win()._stop_solver_thread(t, "测试线程")
        self.assertEqual(t.cancelled, 1)
        self.assertEqual(t.terminated, 0, "协作退出成功时不得强杀")

    def test_timeout_falls_back_to_terminate_with_warning(self):
        class StubbornThread:
            def __init__(self):
                self.cancelled = 0
                self.terminated = 0

            def isRunning(self):
                return True

            def request_cancel(self):
                self.cancelled += 1

            def wait(self, ms=0):
                return False  # 永不协作退出

            def terminate(self):
                self.terminated += 1

        t = StubbornThread()
        with self.assertLogs("main", level="WARNING") as logs:
            self._win()._stop_solver_thread(t, "顽固线程")
        self.assertEqual(t.cancelled, 1)
        self.assertEqual(t.terminated, 1, "超时必须强杀兜底")
        self.assertIn("terminate()", "\n".join(logs.output))

    def test_idle_thread_is_noop(self):
        class Idle:
            def __init__(self):
                self.calls = []

            def isRunning(self):
                return False

            def request_cancel(self):
                self.calls.append("cancel")

            def wait(self, ms=0):
                self.calls.append("wait")
                return True

            def terminate(self):
                self.calls.append("terminate")

        t = Idle()
        self._win()._stop_solver_thread(t, "空闲线程")
        self.assertEqual(t.calls, [], "未运行的线程不该被取消/强杀")


class TestNoCwdRelativeConfigPaths(unittest.TestCase):
    """静态门：本轮范围内不得再出现 cwd 相对的 cfg/data/image 裸路径。

    配负向对照（``_scan_bad_patterns``）：把已知坏样本注入同一扫描器，
    必须被报出来——否则「零命中」可能只是扫描器失效（假绿）。
    """

    _TARGETS = ("setting.py", "utils/solver.py", "utils/mm0.py", "main.py")
    # 只列「裸相对名直接进 IO 调用」的形态；经 setting.project_path() 包裹的
    # 字面量（如 project_path("image/temp.bmp")）属于正确写法，不算命中。
    _BAD = (
        'open("cfg.json"', "open('cfg.json'", 'open(r"cfg.json"',
        'open("data.json"', "'data.json'\"", '"./image/', "'./image/",
        'QPixmap("image/', 'pil_img.save("image/', 'cv2.imwrite("image/',
        'open(f"image/',
    )

    def _scan_bad_patterns(self, text):
        return [pat for pat in self._BAD if pat in text]

    def test_negative_control_catches_bad_sample(self):
        bad = 'with open("cfg.json", encoding="utf-8") as f:\n' \
              '    _locate("./image/ok.png")\n'
        hits = self._scan_bad_patterns(bad)
        self.assertGreaterEqual(len(hits), 2, "扫描器必须能报出注入的坏样本")

    def test_no_cwd_relative_config_paths(self):
        for rel in self._TARGETS:
            path = os.path.join(_ROOT, rel)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(self._scan_bad_patterns(text), [],
                             f"{rel} 仍残留 cwd 相对的 cfg/data/image 裸路径")

    def test_solver_cfg_goes_through_setting(self):
        with open(os.path.join(_ROOT, "utils", "solver.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("setting.load_cfg()", text)
        self.assertIn("setting.project_path(", text)

    def test_main_cfg_write_uses_cfg_path(self):
        with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("setting.CFG_PATH", text)
        self.assertNotIn('open(r"cfg.json", "w")', text)


class TestStopHotkey(unittest.TestCase):
    """紧急终止热键：鼠标被自动点击占用时，用户仍可用物理键终止。

    2026-11-09 用户指令：「增加一个hotkey终止，要不然现在鼠标一动起来之后，
    用户就无法终止了」。锁住四条契约：默认 ESC / 可配置可关闭 / 边沿触发
    （按住不重复请求）/ 与 GUI 停止按钮共用 request_cancel 通道。
    """
    def setUp(self):
        os.environ.pop("MSW_STOP_HOTKEY", None)
        self.s = make_solver(w=3, h=3, a=1)
        self.s.is_play = True
        self.s.warning_signal = mock.Mock()

    def tearDown(self):
        try:
            self.s._stop_stop_hotkey()
        except Exception:  # noqa: BLE001
            pass
        os.environ.pop("MSW_STOP_HOTKEY", None)

    def test_default_is_esc_and_label(self):
        self.assertEqual(self.s._stop_hotkey_vk(), 0x1B)
        self.assertEqual(type(self.s).stop_hotkey_label(), "ESC")

    def test_configurable_and_disable(self):
        os.environ["MSW_STOP_HOTKEY"] = "0x7B"
        self.assertEqual(self.s._stop_hotkey_vk(), 0x7B)
        self.assertEqual(type(self.s).stop_hotkey_label(), "F12")
        os.environ["MSW_STOP_HOTKEY"] = "0"
        self.assertEqual(self.s._stop_hotkey_vk(), 0)
        self.assertFalse(self.s._start_stop_hotkey(), "关闭时不应启动线程")

    def test_help_mode_does_not_grab_keys(self):
        self.s.is_play = False
        self.assertFalse(self.s._start_stop_hotkey())

    def test_edge_trigger_sets_cancel_and_no_repeat_while_held(self):
        import threading
        self.s._cancel_event = threading.Event()
        down = [False]
        self.s._hotkey_pressed = lambda vk: down[0]
        self.assertTrue(self.s._start_stop_hotkey())

        down[0] = True
        time.sleep(0.08)
        self.assertTrue(self.s.cancel_requested(), "按下必须置位取消")
        self.s.clear_cancel()
        time.sleep(0.08)
        self.assertFalse(self.s.cancel_requested(), "按住不放不应重复触发")
        down[0] = False
        time.sleep(0.05)
        down[0] = True
        time.sleep(0.08)
        self.assertTrue(self.s.cancel_requested(), "松开再按应再次触发")


if __name__ == "__main__":
    unittest.main(verbosity=2)
