# Minesweeper Arbiter
# -*- coding: utf-8 -*-
"""求解器主类：Qt 线程（AutoPlayThread）、Solver 组合类与游戏主循环（play/help）。

自 utils/util.py 拆分而来。Solver 由三个 Mixin 组合而成：
- ``utils.vision.BoardVisionMixin``：截图与模板识别
- ``utils.deduction.DeductionMixin``：基础规则推理
- ``utils.probability.ProbabilityMixin``：概率枚举与胜率
"""
import json
import os
import time

import numpy as np
import pyautogui
import win32gui
import win32ui
from PyQt5 import QtCore
from PyQt5.QtCore import QThread

import setting
from logger import get_logger
from .mm0 import ClientToScreen
from .vision import BoardVisionMixin
from .deduction import DeductionMixin
from .probability import ProbabilityMixin

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 对局统计落盘：累计总局数/赢局数跨进程持久（state/game_stats.json）
# ---------------------------------------------------------------------------
_GS_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "game_stats.json",
)


def _gs_load():
    """启动时恢复累计对局统计（played=总局数 / win=赢局数）；缺失/损坏返回空表。"""
    try:
        with open(_GS_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _gs_save(played, win):
    """累计对局统计原子落盘；失败仅记日志，绝不影响自动扫雷流程。"""
    try:
        os.makedirs(os.path.dirname(_GS_STATE_PATH), exist_ok=True)
        _tmp = _GS_STATE_PATH + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump({"played": int(played), "win": int(win)}, f, ensure_ascii=False)
        os.replace(_tmp, _GS_STATE_PATH)
    except (OSError, ValueError) as e:
        logger.debug("对局统计落盘失败（忽略）: %s", e)


class AutoPlayThread(QThread):
    pv_signal = QtCore.pyqtSignal(int)
    Visible_signal = QtCore.pyqtSignal(bool)
    warning_signal = QtCore.pyqtSignal(str)
    update_btn_list_signal = QtCore.pyqtSignal(list)
    # 热力图数据：唯一发射口 _emit_heatmap，payload 结构统一
    # {"prob": 候选格概率dict(新局清空时为空表), "best", "total",
    #  "cell_value", "played", "win"}——概率与数字同帧到达、同步刷新。
    heatmap_signal = QtCore.pyqtSignal(dict)
    start_signal = QtCore.pyqtSignal(tuple)
    end_signal = QtCore.pyqtSignal(str)
    warning_signal_2 = QtCore.pyqtSignal(str)

    def __init__(self):
        super(AutoPlayThread, self).__init__()
        self.value = None
        self.is_play = True

    def set_args(self, value):
        self.value = value
        self.is_play = self.value != 0


class Solver(BoardVisionMixin, DeductionMixin, ProbabilityMixin, AutoPlayThread):
    def __init__(self):
        super().__init__()
        self.memory = {}
        self.images = None
        self._images_cell_width = None
        self._images_mtime = None
        self._locate_templates = {}
        self.by = None
        self.bx = None
        self.num = 0
        self.cell_value = None
        self.img = None

        self.count = 0
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]  #
        self._by = self.cfg["by"]  #
        self.cell_width = self.cfg["cell_width"]
        self.a = self.cfg["a"]  # 总雷数
        self.limit = self.cfg["limit"]
        self.p = self.a / (self.w * self.h)
        self.speed = self.cfg["speed"]

        self.screenshot_h = int(self.cell_width * 7 / 9)
        self.screenshot_w = int(self.cell_width * 5 / 9)
        self.load_img()

        self.pos_dict_list = []
        self.appended_pos = set()

        self.checked = {}

        self.till_now_winrate = 1.0
        # 信号节流相关变量
        self._last_pv_signal_time = 0
        self._last_pv_signal_value = -1
        self._pv_signal_throttle_interval = 0.1  # 100ms
        # data.json 统计缓存
        self.stats_data = None
        self._stats_updates = 0

    def _load_stats(self):
        """懒加载 data.json 统计信息到内存，避免每次点击都读写磁盘。"""
        if self.stats_data is None:
            try:
                with open("data.json", encoding="utf-8") as f:
                    self.stats_data = json.load(f)
            except (OSError, ValueError):
                self.stats_data = {}
        return self.stats_data

    def _flush_stats(self):
        """将内存中的统计信息写回 data.json。"""
        if self.stats_data is not None:
            try:
                with open("data.json", "w", encoding="utf-8") as f:
                    json.dump(self.stats_data, f)
            except OSError as e:
                logger.warning("写入 data.json 失败: %s", e)

    def _throttled_pv_signal_emit(self, value):
        """节流发射pv_signal，每100ms最多发射一次，除非是特殊值(0或100)"""
        current_time = time.time()
        # 特殊值(0, 100)总是立即发射
        if value == 0 or value == 100 or value == -1:
            self.pv_signal.emit(value)
            self._last_pv_signal_time = current_time
            self._last_pv_signal_value = value
            return

        # 检查时间间隔
        if current_time - self._last_pv_signal_time >= self._pv_signal_throttle_interval:
            self.pv_signal.emit(value)
            self._last_pv_signal_time = current_time
            self._last_pv_signal_value = value
        # 否则跳过此次发射

    def run(self):
        self.reload()
        if self.is_play:
            self.play(self.value)
        else:
            self.help()

    def reload(self):
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]  #
        self._by = self.cfg["by"]  #
        self.cell_width = self.cfg["cell_width"]
        self.a = self.cfg["a"]  # 总雷数
        self.limit = self.cfg["limit"]
        self.speed = self.cfg["speed"]

        self.screenshot_h = int(self.cell_width * 7 / 9)
        self.screenshot_w = int(self.cell_width * 5 / 9)
        self.load_img()

    def play(self, limit):
        try:
            self.till_now_winrate = 1.0
            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 开始时点击的坐标
            start_i = int(w / 2)
            start_j = int(h / 2)
            # start_i = 1
            # start_j = 1

            pyautogui.click(
                self.bx + start_i * self.cell_width, self.by + start_j * self.cell_width
            )
            time.sleep(setting.sleep)

            # 初始化cell_value
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9

            # 对局统计跨进程累计：从 state/game_stats.json 恢复历史值；
            # limit（本次要玩的总局数）按"本次新增局数"计数，与历史累计解耦
            _gs = _gs_load()
            win = int(_gs.get("win", 0))
            total = int(_gs.get("played", 0))
            _start_total = total

            while True:
                if self.count >= 3:
                    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                    self.count = 0
                    return

                if win32gui.FindWindow(None, "游戏胜利") > 0:
                    self._last_heatmap = {}
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    self._gs_save(total, win)  # 对局统计累计落盘
                    self._emit_heatmap(cell_value, total, win, clear=True)  # 新局：概率清空、保留数字
                    win += 1
                    total += 1

                    time.sleep(1.2)
                    exit_i, exit_j = self.locate_exit()
                    pyautogui.click(exit_i, exit_j)
                    if total - _start_total == limit:
                        break
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked
                    self.till_now_winrate = 1.0

                    time.sleep(0.1)

                elif win32gui.FindWindow(None, "游戏失败") > 0:
                    self._last_heatmap = {}
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    self._gs_save(total, win)  # 对局统计累计落盘
                    self._emit_heatmap(cell_value, total, win, clear=True)  # 新局：概率清空、保留数字
                    total += 1

                    time.sleep(1.2)
                    exit_i, exit_j = self.locate_exit()
                    pyautogui.click(exit_i, exit_j)
                    if total - _start_total == limit:
                        break
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked
                    self.till_now_winrate = 1.0

                screen = self._grab_screen_bgr()
                _ok, x, y = self._locate("./image/ok.png", screen)
                if _ok:
                    logger.debug("检测到 ok 弹窗: (%s, %s)", x, y)
                    pyautogui.click(x, y)
                    time.sleep(0.1)
                    screen = self._grab_screen_bgr()
                    _, x, y = self._locate("./image/exit.png", screen)
                    pyautogui.click(x, y)
                    time.sleep(0.1)
                    screen = self._grab_screen_bgr()

                _win, x, y = self._locate("./image/win.bmp", screen)
                if _win:
                    self._last_heatmap = {}
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    self._gs_save(total, win)  # 对局统计累计落盘
                    self._emit_heatmap(cell_value, total, win, clear=True)  # 新局：概率清空、保留数字
                    win += 1
                    total += 1

                    time.sleep(1.2)
                    pyautogui.click(x, y)
                    if total - _start_total == limit:
                        break
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked
                    self.till_now_winrate = 1.0

                    time.sleep(0.1)
                    screen = self._grab_screen_bgr()

                _lose, x, y = self._locate("./image/lose.bmp", screen)
                if _lose:
                    self._last_heatmap = {}
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    self._gs_save(total, win)  # 对局统计累计落盘
                    self._emit_heatmap(cell_value, total, win, clear=True)  # 新局：概率清空、保留数字
                    total += 1

                    time.sleep(1.2)
                    pyautogui.click(x, y)
                    if total - _start_total == limit:
                        break
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked
                    self.till_now_winrate = 1.0

                    time.sleep(0.1)

                cell_value = self.complete_scan(cell_value, True)
                sum2 = np.sum(cell_value)
                try:
                    cell_value = self.mine_clear1(cell_value, total, win)
                    self._emit_heatmap(cell_value, total, win)
                except ValueError:
                    continue
                cell_value = self.mine_clear3_1(cell_value, total, win)
                self._emit_heatmap(cell_value, total, win)
                sum3 = np.sum(cell_value)
                if sum3 == sum2:
                    try:
                        _pre_flags = cell_value.copy()  # 入参含推理标记（10=雷/11=安全）
                        cell_value = self.number5_1(cell_value)
                        # number5_1 内部会重扫棋盘，重扫只认屏幕、内存推理标记会丢失——
                        # 把仍处于未开状态(9)的旧标记恢复回来，否则下一帧概率渲染会把
                        # 推理红雷覆盖成浅灰/概率色
                        for _flag in (10, 11):
                            _m = (_pre_flags == _flag) & (cell_value == 9)
                            cell_value[_m] = _flag
                    except ImportError:
                        pass
                # 每一轮实时刷新热力图：决策轮=刚算出的新概率；纯推理轮=沿用最近
                # 一次决策的概率（推理只确定格子，旧概率对仍未开格依旧有效），
                # 新开的格由界面按数字渲染、无概率格落回浅灰半透明底。
                self._emit_heatmap(cell_value, total, win)

        except pyautogui.FailSafeException:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        finally:
            self._flush_stats()

    def _emit_heatmap(self, cell_value, played, win, clear=False):
        """热力图唯一发射口：决策/推理/新局胜负共用，payload 结构统一。

        概率/最佳点击/总局面数沿用最近一次决策（number5_1 写入 _last_heatmap）：
        推理只确定格子，旧概率对仍未开的格依旧有效；新开的格由界面按
        cell_value 渲染成数字、无概率格落回浅灰半透明底。
        clear=True（每局胜负后）：概率清空——新局从全浅灰开始，等首次决策上色。
        概率与数字在同一 payload 原子到达，界面一帧内同步刷新。
        发射失败（如测试用 __new__ 轻量实例未初始化 Qt、或界面异常）只记日志
        并静默跳过——UI 故障绝不打断推理/决策主流程。
        """
        _hm = {} if clear else (getattr(self, "_last_heatmap", None) or {})
        try:
            self.heatmap_signal.emit({
                "prob": _hm.get("prob", {}),
                "best": _hm.get("best"),
                "total": _hm.get("total", 0.0),
                "cell_value": cell_value,
                "played": played,
                "win": win,
            })
        except Exception as e:  # noqa: BLE001 —— UI 隔离：任何发射故障都不外泄
            logger.debug("热力图发射失败（忽略）: %s", e)

    def help(self):
        try:
            self.num = 1
            self.checked = {}
            self.pos_dict_list = []
            self.appended_pos = set()

            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 初始化cell_value
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9

            self.cell_value = self.complete_scan(cell_value.copy(), False)
            try:
                for _ in range(2):
                    cell_value = self.complete_scan(cell_value)
                    cell_value = self.mine_clear1(cell_value)
                    cell_value = self.mine_clear3_1(cell_value)
                cell_value = self.mine_clear1(cell_value)
            except ValueError:
                self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                self.Visible_signal.emit(False)
                return

            if len(self.pos_dict_list) == 0:
                flag = 4
                for flag in range(5):
                    self.num += 1
                    cell_value = self.number5_1(cell_value)
                    if len(self.pos_dict_list) != 0:
                        flag = 0
                        break
                if flag == 4:
                    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                    self.Visible_signal.emit(False)
                    return

            self.update_btn_list_signal.emit(self.pos_dict_list)

        except pyautogui.FailSafeException:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return


if __name__ == "__main__":
    solver = Solver()
    solver.a = 10
    solver.w = 6
    solver.h = 5
    cell_value = [[0, 0, 0, 0, 0, 0, 9, 0],
                  [0, 9, 9, 9, 9, 9, 9, 0],
                  [0, 9, 1, 9, 9, 3, 9, 0],
                  [0, 9, 9, 2, 2, 9, 9, 0],
                  [0, 9, 9, 9, 9, 1, 9, 0],
                  [0, 9, 9, 9, 9, 9, 9, 0],
                  [0, 0, 0, 0, 0, 0, 0, 0]
                  ]
    cell_value = np.array(cell_value)
    clicks = [(1, 1), (2, 1), (1, 2), (3, 1), (1, 3), (4, 1), (3, 2), (2, 3), (5, 1), (4, 2), (2, 4), (6, 1), (3, 4),
              (6, 2),
              (5, 3), (4, 4), (6, 3), (4, 5), (5, 5), (6, 4), (6, 5)]

    start = time.time()
    res_list, t, _ = solver.part_solve(clicks, cell_value, 0, 26, [(2, 2), (3, 3), (4, 3), (5, 2), (5, 4)], _try=False)
    res_list = np.array(res_list)
    res_list = res_list.sum(axis=0)
    res_list = res_list / t
    logger.info("res_list=%s, t=%s", res_list, t)
    logger.info("_=%s", _)
    logger.info("time=%s", time.time() - start)

    start = time.time()
    res_list, t, _ = solver.part_solve_single(clicks, cell_value, 0, 26, [(2, 2), (3, 3), (4, 3), (5, 2), (5, 4)],
                                              _try=False)
    res_list = np.array(res_list)
    res_list = res_list.sum(axis=0)
    res_list = res_list / t
    logger.info("res_list=%s, t=%s", res_list, t)
    logger.info("_=%s", _)
    logger.info("time=%s", time.time() - start)
