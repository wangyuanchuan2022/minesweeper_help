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
import threading
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
from .vision import BoardVisionMixin, LowConfidenceScanError
from .deduction import DeductionMixin
from .probability import ProbabilityMixin
from . import e2e_probe as probe

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 资源路径：一律经 setting 解析成绝对路径（与进程 cwd 无关）——
# 从任意目录启动都能找到 state/ 与 data.json。
# ---------------------------------------------------------------------------
_GS_STATE_PATH = os.path.join(setting.STATE_DIR, "game_stats.json")
_DATA_PATH = setting.DATA_PATH


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
    # {"prob": 候选格概率dict(新局清空时为空表), "best", "best_conf"(当前点击格
    #  不是雷的概率/胜率), "total", "cell_value", "played", "win"}
    # ——概率与数字同帧到达、同步刷新。
    heatmap_signal = QtCore.pyqtSignal(dict)
    start_signal = QtCore.pyqtSignal(tuple)
    end_signal = QtCore.pyqtSignal(str)
    warning_signal_2 = QtCore.pyqtSignal(str)

    def __init__(self):
        super(AutoPlayThread, self).__init__()
        self.value = None
        self.is_play = True
        # 协作式取消通道：置位后 play/help 在下一个安全点自行返回并走 finally
        # 清理（统计落盘等）。GUI 线程用 request_cancel() 请求停止，只有超时
        # 未退出时才用 terminate() 兜底（强杀会绕过 finally）。
        self._cancel_event = threading.Event()

    def set_args(self, value):
        self.value = value
        self.is_play = self.value != 0

    # --- 协作式取消 -----------------------------------------------------
    # ⚠️ 取属性一律走 ``self.__dict__.get`` 而不是 ``getattr(self, ..., None)``：
    # 测试用 ``Solver.__new__`` 造的轻量实例没有执行 sip 基类 ``__init__``，
    # 在这种实例上做 **缺失属性** 的 getattr/hasattr 会抛
    # ``RuntimeError: super-class __init__() of type Solver was never called``；
    # 而 ``__dict__`` 读取是安全的（空字典），缺失时返回 None 由调用方补建。
    def _cancel_event_or_none(self):
        try:
            return self.__dict__.get("_cancel_event")
        except Exception:  # noqa: BLE001 —— 实例形态异常时按「无取消通道」处理
            return None

    # ------------------------------------------------------------------
    # 全局热键紧急终止（用户指令 2026-11-09：「鼠标一动起来之后，用户就
    # 无法终止了」）
    #
    # 症状：自动模式持续用 pyautogui 真实点击，鼠标被程序驱动，用户点不到
    # GUI 上的「停止」按钮；pyautogui 的 FAILSAFE 又要求把鼠标移到屏幕角落
    # ——而鼠标正被程序占用，同样做不到 ⇒ 用户失去终止手段。
    #
    # 方案：后台守护线程轮询 Win32 ``GetAsyncKeyState``（读取**物理键状态**，
    # 不依赖窗口焦点、不需要鼠标、不需要消息循环），命中即调
    # ``request_cancel()``，与 GUI 停止按钮共用同一协作式取消通道。
    # 默认热键 ESC（VK 0x1B），可用 MSW_STOP_HOTKEY 覆盖为其它虚拟键码
    # （十进制或 0x 十六进制，如 0x7B=F12）；MSW_STOP_HOTKEY=0 关闭。
    #
    # 边界：
    #   · 只在 is_play=True（自动模式）时监听，帮助模式不抢键
    #   · 边沿触发（按住不重复请求），松开后才能再次触发
    #   · 非 Windows / ctypes 失败时静默降级（不影响主流程）
    #   · 线程是 daemon，start 时创建、cancel 或线程结束时退出
    # ------------------------------------------------------------------
    _HOTKEY_POLL_S = 0.02

    @staticmethod
    def _stop_hotkey_vk():
        raw = os.environ.get("MSW_STOP_HOTKEY", "").strip()
        if not raw:
            return 0x1B  # ESC
        try:
            v = int(raw, 0)
        except ValueError:
            return 0x1B
        return v if v > 0 else 0

    @classmethod
    def stop_hotkey_label(cls):
        """返回给用户看的热键名（如 'ESC' / 'F12' / 'VK 0x7A'）。"""
        vk = cls._stop_hotkey_vk()
        if not vk:
            return "（已关闭）"
        named = {
            0x1B: "ESC", 0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4",
            0x74: "F5", 0x75: "F6", 0x76: "F7", 0x77: "F8", 0x78: "F9",
            0x79: "F10", 0x7A: "F11", 0x7B: "F12",
            0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x20: "Space",
            0x2D: "Insert", 0x2E: "Delete", 0x24: "Home",
            0x1E: "Pause/Break", 0x7E: "F13", 0x7F: "F14",
        }
        return named.get(vk, f"VK 0x{vk:X}")

    def _hotkey_pressed(self, vk):
        """读物理键状态；不可用返回 False。"""
        try:
            import ctypes
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            return bool(state & 0x8000)
        except Exception:  # noqa: BLE001
            return False

    def _start_stop_hotkey(self):
        """启动紧急终止监听线程（幂等）。返回是否真的启动了新线程。"""
        vk = self._stop_hotkey_vk()
        if not vk or not self.__dict__.get("is_play", True):
            return False
        th = self.__dict__.get("_hotkey_thread")
        if th is not None and th.is_alive():
            return False
        ev = self._cancel_event_or_none()
        if ev is None:
            ev = threading.Event()
            self._cancel_event = ev

        def _loop(stop_event, key_vk, cancel):
            pressed = False
            while not stop_event.is_set():
                try:
                    down = self._hotkey_pressed(key_vk)
                except Exception:  # noqa: BLE001
                    down = False
                if down and not pressed:
                    pressed = True
                    cancel()          # 与 GUI 停止按钮同一通道
                    logger.warning(
                        "检测到紧急终止热键（VK=0x%X），已请求停止自动模式；"
                        "当前决策/点击结束后即退出", key_vk)
                    try:
                        self.warning_signal.emit(
                            f"已收到紧急终止（热键 0x{key_vk:X}），"
                            f"当前操作结束后自动停止")
                    except Exception:  # noqa: BLE001
                        pass
                elif not down:
                    pressed = False
                stop_event.wait(self._HOTKEY_POLL_S)

        stop = threading.Event()
        th = threading.Thread(target=_loop, args=(stop, vk, self.request_cancel),
                              daemon=True, name="hotkey-stop")
        self._hotkey_stop = stop
        self._hotkey_thread = th
        th.start()
        return True

    def _stop_stop_hotkey(self):
        """停止监听线程（不阻塞主线程）。"""
        ev = self.__dict__.get("_hotkey_stop")
        if ev is not None:
            ev.set()
        self._hotkey_thread = None

    def request_cancel(self):
        """请求停止（线程安全，可在 GUI 线程调用）；不阻塞。

        命中紧急热键时由守护线程调用；与 GUI 停止按钮共用同一路径。
        """
        ev = self._cancel_event_or_none()
        if ev is None:
            ev = threading.Event()
            self._cancel_event = ev
        ev.set()

    def clear_cancel(self):
        """清除取消标志（开始新一轮前调用）。"""
        ev = self._cancel_event_or_none()
        if ev is not None:
            ev.clear()

    def cancel_requested(self):
        """取消标志是否置位（无取消通道时按未取消处理）。"""
        ev = self._cancel_event_or_none()
        return bool(ev is not None and ev.is_set())

    # 主循环内的语义化别名
    _stop_requested = cancel_requested

    def start(self, *args, **kwargs):
        """启动线程前清掉上一轮残留的取消标志。

        只在这里清（不在 ``run()`` 里清）：``start()`` 由 GUI 线程在派发前调用，
        此后到 ``run()`` 真正开跑之间若有 ``request_cancel()``，标志必须保留，
        否则「刚点启动就点停止」的取消会被静默吞掉。
        """
        self.clear_cancel()
        self._start_stop_hotkey()
        super(AutoPlayThread, self).start(*args, **kwargs)


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
        # 配置一律经 setting.load_cfg()（绝对路径，不依赖 cwd）；
        # screenshot_* 等派生字段由 _apply_cfg 统一计算。
        self.cfg = setting.load_cfg()
        self._apply_cfg(self.cfg)
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
                with open(_DATA_PATH, encoding="utf-8") as f:
                    self.stats_data = json.load(f)
            except (OSError, ValueError):
                self.stats_data = {}
        return self.stats_data

    def _flush_stats(self):
        """将内存中的统计信息写回 data.json。"""
        if self.stats_data is not None:
            try:
                os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
                with open(_DATA_PATH, "w", encoding="utf-8") as f:
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
        # 注意：这里**不清**取消标志。清标志只在 start() 里做——若在 run() 里清，
        # 「刚点启动、线程还没跑到 run()，用户已点停止」的取消会被静默吞掉。
        try:
            self.reload()
            if self.is_play:
                self.play(self.value)
            else:
                self.help()
        finally:
            # 自动线程真正退出后停掉热键监听，避免 daemon 线程残留到下一轮
            # （start() 会重新拉起，这里只负责收尾）。
            self._stop_stop_hotkey()

    def _apply_cfg(self, cfg):
        """把配置字典写入实例字段，并统一重算**派生字段**。

        ``__init__`` 与 ``reload()`` 共用本方法：任何依赖配置的派生量（至少
        雷密度 ``p``，另有按 cell_width 换算的截图补丁尺寸）都必须在这里重算，
        否则改过配置的实例会带着构造期的旧值继续算概率。
        """
        self.cfg = cfg
        self.w = cfg["w"]
        self.h = cfg["h"]
        self._bx = cfg["bx"]  #
        self._by = cfg["by"]  #
        self.cell_width = cfg["cell_width"]
        self.a = cfg["a"]  # 总雷数
        self.limit = cfg["limit"]
        self.speed = cfg["speed"]
        # 派生字段：雷密度先验（part_solve/win_rate 的 (1-p)^n 项直接用 self.p）
        self.p = self.a / (self.w * self.h) if (self.w * self.h) else 0.0
        # 派生字段：模板补丁尺寸随 cell_width 标定
        self.screenshot_h = int(self.cell_width * 7 / 9)
        self.screenshot_w = int(self.cell_width * 5 / 9)

    def reload(self):
        self._apply_cfg(setting.load_cfg())
        self.load_img()

    # ------------------------------------------------------------------
    # 局末事件驱动重开（2026-11-09，用户指令「局末 sleep 链改事件驱动」）
    #
    # 背景（253 局真实端到端测量）：每局末有 265 次、每次中位 2.86s 的
    # **纯空白等待**，合计 734s = 31.7% 墙钟。来源是 4 个胜负分支里写死的
    #   sleep(1.2) + sleep(1.0) + sleep(0.5) + sleep(0.1) = 2.8s
    # 这些 sleep 是为了「等 Arbiter 渲染完成」，但实际渲染通常在几十毫秒内
    # 就结束 ⇒ 绝大部分是白等。
    #
    # 改为轮询检测（屏幕 DC BitBlt 抓窗口/棋盘区域，单次 6.3ms）：
    #   ① 表情稳定   —— 连续两轮命中同一位置，确认胜负动画已结束
    #   ② 旧表情消失 —— 点「新局」按钮后，等 win/lose 模板不再命中
    #   ③ 首点生效   —— 记录点击前棋盘指纹，等盘面变化且连续两帧一致
    # 每步都有超时上限（合计最坏 3.6s，正常 ~0.1-0.3s），超时按「已尽力」
    # 继续，不静默死等；等待期间响应协作式取消。
    #
    # 可回退：MSW_LEGACY_SLEEP=1 时走原固定 sleep 链（用于真机 A/B 对照），
    # 缺省走事件驱动。这是窄豁免，不动其它路径。
    # ------------------------------------------------------------------
    _POLL_INTERVAL_S = 0.025
    _SETTLE_TIMEOUT_S = 0.6
    _RESET_TIMEOUT_S = 2.0
    _FIRST_CLICK_TIMEOUT_S = 1.0

    @staticmethod
    def _legacy_sleep_mode():
        return os.environ.get("MSW_LEGACY_SLEEP", "0") not in ("0", "", "false", "False")

    def _wait_for(self, predicate, timeout_s, label=""):
        """事件驱动等待：轮询 predicate() 直到为真或超时。

        返回 (是否达成, 耗时秒, 轮询次数)。
        · 等待期间若用户请求停止，立即返回 (False, ...)。
        · predicate 抛异常只记 debug 并继续轮询（抓屏偶发失败不应终止整局）。
        """
        t0 = time.perf_counter()
        deadline = t0 + max(0.0, float(timeout_s))
        polls = 0
        last_exc = None
        while True:
            if self._stop_requested():
                return False, time.perf_counter() - t0, polls
            polls += 1
            try:
                if predicate():
                    return True, time.perf_counter() - t0, polls
            except Exception as e:  # noqa: BLE001
                last_exc = e
            now = time.perf_counter()
            if now >= deadline:
                if last_exc is not None:
                    logger.debug("%s 等待超时（期间最后一次异常：%s）",
                                 label, last_exc)
                return False, now - t0, polls
            time.sleep(min(self._POLL_INTERVAL_S, max(0.0, deadline - now)))

    def _board_region_rect(self):
        """棋盘区域的屏幕矩形（不含顶部计时器/表情栏）。

        与 ``BoardVisionMixin._grab_board`` 的取窗口径逐字一致：
        原点 = ClientToScreen(hwnd, _bx, _by) + 0.5*cell_width。
        """
        cw = self.cell_width
        x0 = self.bx + 0.5 * cw
        y0 = self.by + 0.5 * cw
        return (int(x0), int(y0),
                int(x0 + self.w * cw), int(y0 + self.h * cw))

    def _board_signature(self):
        """抓棋盘区域小图（BGR）；失败返回 None。"""
        try:
            return self._grab_region_bgr(self._board_region_rect())
        except Exception as e:  # noqa: BLE001 —— 抓屏失败只影响等待精度
            logger.debug("棋盘区域抓屏失败（忽略）: %s", e)
            return None

    def _board_ready_predicate(self, base):
        """构造一个 predicate：棋盘相对 ``base`` 已变化、且连续两帧一致。

        **为什么用棋盘像素而不是表情模板**：
        · 点表情/退出按钮后 Arbiter 立即生成全 9 的新棋盘，与上一局末尾
          （满是数字）逐像素差异巨大 ⇒ 判据极其可靠，且无需假设「表情变成
          什么样子」（点表情后变回普通笑脸，win/lose 模板是否仍部分命中
          我没有真机验证过，不该作为唯一依据）。
        · 单次 BitBlt 抓 720x384 棋盘区域只要 6.3ms，而每轮做 2-3 次
          模板匹配要 62ms ⇒ 轮询快 10x，等待更精细。
        · 「连续两帧一致」保证渲染/flood-fill 动画已结束，不会拿到半帧。
        """
        prev = [None]

        def _ready():
            cur = self._board_signature()
            if cur is None or base is None or cur.shape != base.shape:
                return False
            changed = (float(np.abs(cur.astype(np.int16)
                                   - base.astype(np.int16)).mean()) > 1.0)
            settled = (prev[0] is not None
                       and cur.shape == prev[0].shape
                       and bool(np.array_equal(cur, prev[0])))
            prev[0] = cur
            return bool(changed and settled)

        return _ready

    def _restart_after_game(self, *, dismiss_fn, limit_reached,
                            total, win, start_i, start_j, source):
        """局末统一处理：清空热力图 → 关闭弹窗 →（事件驱动）重开新局。

        ``dismiss_fn``：返回要点击的屏幕坐标 (x, y) 的可调用对象。
          · 模板胜负分支 → ``lambda: face_xy``（点表情＝开新局）
          · 标题窗口分支 → ``self.locate_exit``（点退出按钮）
        ``limit_reached``：本轮已达目标局数 ⇒ 只 dismiss、不重开新局
        （语义与原 ``if total - _start_total == limit: break`` 完全一致）。

        返回 (should_continue, cell_value)：
          · should_continue=False → 调用方应 ``return``（用户停止或局数已满）。
          · 否则返回重建的全 9 棋盘供下一轮 complete_scan 使用。
        """
        WIN_P = setting.project_path("image/win.bmp")
        LOSE_P = setting.project_path("image/lose.bmp")

        # 新局：概率清空、保留数字（与原 4 个分支一致）
        h, w = self.h, self.w
        fresh = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                fresh[j, i] = 9
        self._last_heatmap = {}
        self._emit_heatmap(fresh, total, win, clear=True)

        # ---- 回退路径：原固定 sleep 链（MSW_LEGACY_SLEEP=1）----
        if self._legacy_sleep_mode():
            time.sleep(1.2)
            xy = dismiss_fn()
            pyautogui.click(xy[0], xy[1])
            if limit_reached:
                return False, fresh
            time.sleep(1.0)
            hwnd = win32gui.FindWindow(None, setting.win_name)
            if hwnd:
                win32gui.ShowWindow(hwnd, 1)
            time.sleep(0.5)
            pyautogui.click(
                self.bx + start_i * self.cell_width,
                self.by + start_j * self.cell_width,
            )
            self.checked = {}
            self.till_now_winrate = 1.0
            time.sleep(0.1)
            return not self._stop_requested(), fresh

        # ---- ① 记录「上一局末尾」棋盘指纹，并等其稳定 ⇒ 替代 sleep(1.2) ----
        # 刚判出胜负时的盘面应已静止；连续两帧一致即认为动画结束。
        base_before = self._board_signature()
        stable_prev = [None]

        def _pre_stable():
            cur = self._board_signature()
            if cur is None:
                return False
            ok = (stable_prev[0] is not None
                  and cur.shape == stable_prev[0].shape
                  and bool(np.array_equal(cur, stable_prev[0])))
            stable_prev[0] = cur
            return ok

        ok, dt, polls = self._wait_for(
            _pre_stable, self._SETTLE_TIMEOUT_S, "局末盘面稳定")
        probe.mark("game_end.settle", ok=ok, ms=round(dt * 1000, 1),
                   polls=polls, source=source)
        if self._stop_requested():
            return False, fresh

        # ---- ② 点击关闭/新局按钮 ----
        xy = dismiss_fn()
        with probe.span("game_end.dismiss_click"):
            pyautogui.click(xy[0], xy[1])
        if limit_reached or self._stop_requested():
            return False, fresh

        # ---- ③ 等「新棋盘」出现且静止 ⇒ 替代 sleep(1.0)+sleep(0.5) ----
        # 主判据 = 棋盘像素已变化且连续两帧一致（见 _board_ready_predicate）。
        # 兜底 = 旧胜负表情不再命中（两者任一成立即可，防止极端情况下
        #        棋盘区域被弹窗遮住而一直检测不到变化）。
        ready = self._board_ready_predicate(base_before)

        def _reset_done():
            if ready():
                return True
            try:
                d = self._dialog_templates((WIN_P, LOSE_P))
                if (not d[WIN_P][0]) and (not d[LOSE_P][0]):
                    return True
            except Exception:  # noqa: BLE001 —— 兜底失败不影响主判据
                pass
            return False

        ok, dt, polls = self._wait_for(
            _reset_done, self._RESET_TIMEOUT_S, "新局棋盘就绪")
        probe.mark("game_end.reset", ok=ok, ms=round(dt * 1000, 1),
                   polls=polls, source=source)
        if self._stop_requested():
            return False, fresh

        # ---- ④ 确保窗口可见（保留原有 ShowWindow，廉价且不依赖等待）----
        hwnd = win32gui.FindWindow(None, setting.win_name)
        if hwnd:
            win32gui.ShowWindow(hwnd, 1)

        # ---- ⑤ 首点 → 等盘面变化且连续两帧一致 ⇒ 替代 sleep(0.1) ----
        base = self._board_signature()
        with probe.span("game_end.first_click"):
            pyautogui.click(
                self.bx + start_i * self.cell_width,
                self.by + start_j * self.cell_width,
            )
        ok, dt, polls = self._wait_for(
            self._board_ready_predicate(base),
            self._FIRST_CLICK_TIMEOUT_S, "首点生效")
        probe.mark("game_end.first_click", ok=ok, ms=round(dt * 1000, 1),
                   polls=polls, source=source)

        self.checked = {}            # 重置 checked
        self.till_now_winrate = 1.0
        return not self._stop_requested(), fresh

    def play(self, limit):
        try:
            self.till_now_winrate = 1.0
            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 开始时点击的坐标
            # P2 角落开局（双轮 2000 局验证：角落 39.60% vs 中心 36.35%，p=0.015；
            # 论文 AAAI-17 WS-17-15 Lemma 1 同结论）。角落首点在 Arbiter 延迟
            # 布雷下开出线性边缘 front，中盘约束传播远比中心 blob 简单。
            # 2000 局角落基线（C++ 三层 + 随机残余）：38.05%，vs 中心 +4.0pp。
            start_i = 1  # 1-based cell index (1,1) = top-left corner
            start_j = 1

            with probe.span("play.first_click"):
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
                # 【端到端探针】局开始（幂等：已在局中时无操作）。
                # 放在循环顶部而不是「首点之后」：一处覆盖全部开局路径
                # （正常续局 / 弹窗后重开），不需要在四个胜负分支各写一遍。
                probe.game_begin()
                # 协作式取消安全点①：每轮循环开头。退出后走 finally（统计落盘）
                if self._stop_requested():
                    logger.info("收到停止请求，自动模式在循环起点安全退出")
                    return
                if self.count >= 3:
                    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                    probe.error("count_abort", "count>=3 判定为设置错误，自动循环停止",
                                count=int(self.count))
                    self.count = 0
                    return

                # ---- 局末胜负判定（2026-11-09：整屏匹配改为窗口区域匹配）----
                # 一次 BitBlt 抓 Arbiter 窗口矩形（6.3ms），在这张图上批量判定
                # ok / win / lose 三个模板；返回的 xy 已换算成屏幕坐标。
                # 相比「整屏抓屏 44.6ms + 4 次整屏 matchTemplate 135-616ms」，
                # 本机离线实测 6.7-8.8x。窗口不可用时 _grab_dialog_screen
                # 自动回退整屏，绝不因优化让胜负判定失效。
                dialogs = self._dialog_templates((
                    setting.project_path("image/ok.png"),
                    setting.project_path("image/win.bmp"),
                    setting.project_path("image/lose.bmp"),
                ))
                OK_P = setting.project_path("image/ok.png")
                WIN_P = setting.project_path("image/win.bmp")
                LOSE_P = setting.project_path("image/lose.bmp")
                EXIT_P = setting.project_path("image/exit.png")

                # 标题窗口检测（原逻辑保留；253 局实测 0 次命中，属防御性路径）
                _title_win = win32gui.FindWindow(None, "游戏胜利") > 0
                _title_lose = win32gui.FindWindow(None, "游戏失败") > 0

                # ok 弹窗（如「成绩记录」）：**每轮无条件**检测并点掉。
                # 原代码在 4 个胜负分支之后独立执行 `if _ok: click`，不依赖
                # 胜负判定；合并分支时不能把它塞进胜负条件里。
                # 点掉 ok 后重新抓一次窗口区域，再判 exit / win / lose。
                if dialogs[OK_P][0]:
                    logger.debug("检测到 ok 弹窗: (%s, %s)",
                                 dialogs[OK_P][1], dialogs[OK_P][2])
                    pyautogui.click(dialogs[OK_P][1], dialogs[OK_P][2])
                    time.sleep(0.1)
                    dialogs = self._dialog_templates(
                        (OK_P, EXIT_P, WIN_P, LOSE_P))
                    if dialogs[EXIT_P][0]:
                        pyautogui.click(dialogs[EXIT_P][1], dialogs[EXIT_P][2])
                        time.sleep(0.1)
                        dialogs = self._dialog_templates(
                            (OK_P, EXIT_P, WIN_P, LOSE_P))

                if _title_win or _title_lose or dialogs[WIN_P][0] or dialogs[LOSE_P][0]:
                    # 判定胜负来源（与探针 source 口径一致）
                    if _title_win:
                        won, source = True, "popup_title_win"
                    elif _title_lose:
                        won, source = False, "popup_title_lose"
                    elif dialogs[WIN_P][0]:
                        won, source = True, "template_win"
                    else:
                        won, source = False, "template_lose"

                    # 关闭/新局按钮坐标按来源区分（原 4 个分支本就不同）：
                    #   · 标题窗口分支 → locate_exit()（关掉独立弹窗的 X）
                    #   · 模板分支     → 点表情本身（Arbiter 点表情＝重置棋盘）
                    # ⚠️ 两者点完后**都需要再点首格开新局**：原代码 4 个分支
                    #    在点 dismiss 之后都执行了 `pyautogui.click(首格)`，
                    #    Arbiter 重置后棋盘是「全新未开局」，必须点首格才布雷。
                    #    合并时不可抹平这一点（曾误判为「模板分支不需点首格」）。
                    if source.startswith("popup_title"):
                        dismiss = self.locate_exit
                    else:
                        _fx, _fy = (dialogs[WIN_P][1], dialogs[WIN_P][2])
                        if won is False:
                            _fx, _fy = (dialogs[LOSE_P][1], dialogs[LOSE_P][2])
                        dismiss = lambda: (_fx, _fy)  # noqa: E731

                    # 统计口径统一（2026-11-09）：原 4 个分支顺序不一致
                    # （2 个先 _gs_save 后累加、2 个先累加后 _gs_save），
                    # 导致 game_stats.json 偶发少记/多记一局。现统一为
                    # **先累加、再落盘、再发帧**，与 _emit_heatmap 使用的
                    # total/win 完全一致。
                    if won:
                        win += 1
                    total += 1
                    _gs_save(total, win)  # 对局统计累计落盘
                    probe.game_end(won=won, total=int(total), win=int(win),
                                   source=source)

                    cont, cell_value = self._restart_after_game(
                        dismiss_fn=dismiss,
                        limit_reached=(total - _start_total >= limit),
                        total=total, win=win,
                        start_i=start_i, start_j=start_j, source=source)
                    if not cont:
                        return

                with probe.span("play.complete_scan"):
                    cell_value = self.complete_scan(cell_value, True)
                # 协作式取消安全点②：慢操作（整盘扫描）之后、决策之前。
                # 不打断正在进行的概率枚举（那属于自适应预算内部，本身有预算门），
                # 只在这轮决策链开始前退出。
                if self._stop_requested():
                    logger.info("收到停止请求，自动模式在扫描后安全退出")
                    return
                # number5_1 内部 decide 循环每轮都需要把最新的 payload 推给 UI；
                # 这些字段只作为 UI 遥测，不参与算法计算。
                self._heatmap_played = total
                self._heatmap_win = win
                # 每轮直接进入 number5_1：自动模式的决策唯一归属 C++ decide
                # 三层（用户批准方案 A）。此前这里是「Py 规则推理 mine_clear1/
                # mine_clear3_1 → 无进展才 number5_1」的编排链，与 decide 内部
                # 决策重复；`play()` 内的 Python 推理已删除，`help()` 内的保留。
                # number5_1 内部会重扫棋盘，重扫只认屏幕、内存推理标记会丢失——
                # 这里不再有 Py 推理标记需要恢复（旧 `_pre_flags` 逻辑随之移除）。
                with probe.span("play.number5_1"):
                    cell_value = self.number5_1(cell_value)
                # 每一轮实时刷新热力图：决策轮=刚算出的新概率，新开的格由界面
                # 按数字渲染、无概率格落回浅灰半透明底。
                with probe.span("play.emit_heatmap"):
                    self._emit_heatmap(cell_value, total, win)

        except pyautogui.FailSafeException:
            probe.error("failsafe", "pyautogui FailSafeException（鼠标移到屏幕角落触发）")
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error as e:
            probe.error("win32ui_error", str(e))
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except OSError as e:
            # ⚠️ 2026-11-09 健壮性修复（真实端到端测量暴露）：
            # `ImageGrab.grab()` 会间歇性抛 `OSError: screen grab failed`
            # （GDI 抓屏在锁屏切换/驱动重置/远程会话重连/桌面被短暂占用时都会失败）。
            # 原实现只捕 FailSafe 与 win32ui.error ⇒ 这个 OSError 会把**整条自动
            # 线程打死**，用户看到「自动模式突然停了」而界面上没有任何解释
            # （实测：本轮端到端冒烟连续两次都在第一次抓屏就撞上）。
            # 现在按「可观测错误 + 停止本线程」处理：显式 ERROR 日志 + 弹窗告知
            # 用户（而不是静默死掉），语义与其它两个 fatal 分支一致。
            logger.error("自动模式因系统抓屏失败而停止：%s", e, exc_info=True)
            probe.error("screen_grab_failed", f"{type(e).__name__}: {e}")
            try:
                self.warning_signal.emit(
                    f"自动模式已停止：系统抓屏失败（{e}）。\n"
                    f"常见原因：锁屏/远程会话切换/显卡驱动重置。\n"
                    f"请确认扫雷窗口可见后重新启动自动模式。")
            except Exception:  # noqa: BLE001 —— 告警失败不得掩盖原始异常
                pass
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        finally:
            self._flush_stats()

    def _emit_help_heatmap(self, cell_value):
        """帮助模式的热力图发射口：与自动页共用 ``_emit_heatmap`` 的 payload 契约。

        帮助线程此前**从不发 heatmap_signal**（GUI 已接线但永远收不到帧），
        所以帮助页热力图一直是全灰；同时 ``prob`` 为空只表示「本帧无新估值」，
        必须带 ``clear=False``，让 UI 沿用最近一次真实概率层而不是抹掉。
        """
        played = self.__dict__.get("_heatmap_played", 0)
        win = self.__dict__.get("_heatmap_win", 0)
        self._emit_heatmap(cell_value, played, win, clear=False, phase="decision")

    def _emit_heatmap(self, cell_value, played, win, clear=False, phase="ready"):
        """热力图唯一发射口：决策/推理/新局胜负共用，payload 结构统一。

        概率/最佳点击及其置信度/总局面数沿用最近一次决策（number5_1 写入
        _last_heatmap）：推理只确定格子，旧概率对仍未开的格依旧有效；新开的
        格由界面按 cell_value 渲染成数字、无概率格落回浅灰半透明底。
        clear=True（每局胜负后）：概率清空——新局从全浅灰开始，等首次决策上色。
        概率与数字在同一 payload 原子到达，界面一帧内同步刷新。
        发射失败（如测试用 __new__ 轻量实例未初始化 Qt、或界面异常）只记日志
        并静默跳过——UI 故障绝不打断推理/决策主流程。
        """
        # ⚠️ 取缺失属性必须走 __dict__ 而不是 getattr：轻量实例（测试用
        # Solver.__new__）没有执行 sip 基类 __init__，getattr 读缺失属性会抛
        # RuntimeError: super-class __init__() of type Solver was never called。
        _hm = {} if clear else (self.__dict__.get("_last_heatmap") or {})
        try:
            self.heatmap_signal.emit({
                "prob": _hm.get("prob", {}),
                "best": _hm.get("best"),
                "best_conf": _hm.get("best_conf"),
                "total": _hm.get("total", 0.0),
                "cell_value": cell_value,
                "played": played,
                "win": win,
                "phase": phase,
                # 显式清空标记：只有「新局/停止」这类真清空帧才允许 UI 抹掉
                # 已有概率层。中间帧（如 decide 循环里的重扫帧）prob 为空只是
                # 「本帧没带新估值」，不代表旧估值失效。
                "clear": bool(clear),
                # 真实状态转移（仅 OPTIMAL 精确层产出；其它层为 None，
                # UI 据此显示"暂无真实状态转移分布"，绝不合成概率）。
                "transitions": _hm.get("transitions"),
                "transition_meta": _hm.get("transition_meta"),
            })
        except Exception as e:  # noqa: BLE001 —— UI 隔离：任何发射故障都不外泄
            logger.debug("热力图发射失败（忽略）: %s", e)

    def help(self):
        try:
            # 帮助模式预算放宽（用户指令 2026-11-06 与 2026-11-07「可以稍微把
            # 预算放高一点」）：决策预算 ×3（30s→90s，经 HELP_BUDGET_MULT）。
            # **仅**影响帮助模式自己的决策（自动模式仍是 30s 基线）；
            # finally 恢复防泄漏。
            import utils.probability as _prob_mod
            _prob_mod._DECISION_BUDGET_MULT = _prob_mod.HELP_BUDGET_MULT
            self.num = 1
            self.checked = {}
            self.pos_dict_list = []
            self.appended_pos = set()

            # 协作式取消安全点①：入口。help 的单轮由若干次扫描/推理组成，
            # 已请求停止时直接跳过，不白跑一遍重计算。
            if self._stop_requested():
                logger.info("收到停止请求，帮助模式在入口安全退出")
                return

            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 初始化cell_value
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9

            # 🔴 顺序修复（用户实测「卡在等待帮助计算开始」）：
            # 必须**先发一帧**再扫描。旧顺序是「先 complete_scan → 再 emit」，
            # 一旦首轮扫描不可信（LowConfidenceScanError，真机常见：窗口未置顶/
            # DPI/模板未标定），异常会在 emit 之前抛出 ⇒ 一帧都发不出去 ⇒
            # 界面永久停在 update_image 写入的「等待帮助计算开始…」。
            # 先发帧保证 UI 立刻从「等待」切到真实状态（哪怕是空棋盘）。
            self._emit_help_heatmap(cell_value)

            # 首轮扫描失败**不致命**：保留全未开棋盘继续走 C++ decide。
            # 真机首帧常因窗口未稳定/DPI 未就绪而不可信，重试一次即可；
            # 仍失败则用全 9 棋盘继续（decide 会对 no_closed_cell 给出明确
            # 结果，用户可据此重试），绝不静默结束线程。
            try:
                self.cell_value = self.complete_scan(cell_value.copy(), False)
                cell_value = self.cell_value
            except LowConfidenceScanError as e:
                # ⚠️ 扫描失败时必须**显式**把 self.cell_value 归位到全未开棋盘：
                # number5_1 内部会读 self.cell_value（不是局部变量），若这里
                # 不赋值就会带着上一轮的旧盘面（首轮时是 None）继续决策，
                # 直接抛异常 ⇒ 线程被兜底终止 ⇒ 界面停在「等待帮助计算开始」。
                self.cell_value = cell_value
                logger.warning("帮助模式首轮扫描不可信，保留全未开棋盘继续：%s", e)
            except Exception as e:  # noqa: BLE001 —— 扫描故障不得杀死线程
                self.cell_value = cell_value
                logger.warning("帮助模式首轮扫描失败，保留全未开棋盘继续：%s", e)
            else:
                # 扫描成功：刷新为真实棋盘
                self._emit_help_heatmap(self.cell_value)
            # 协作式取消安全点②：首轮慢扫描之后。
            if self._stop_requested():
                logger.info("收到停止请求，帮助模式在扫描后安全退出")
                return
            # ⚠️ 帮助模式**不再执行任何 Python 规则推理**（用户指令 2026-11-07：
            # 「帮助模式仍调用了 PY 的逻辑推理」——要求与自动模式统一为唯一
            # C++ decide 决策链）。
            # 此前这里跑的是：
            #   for _ in range(2): complete_scan + mine_clear1 + mine_clear3_1
            #   mine_clear1
            #   → 然后在「候选为空」时再跑最多 5 轮 number5_1
            # 这些 mine_clear1/mine_clear3_1 是**纯 Python 邻域计数推理**，
            # 与自动模式（唯一归属 C++ decide）不一致，且会先于 C++ 决策改动
            # cell_value，造成两条决策源互相覆盖。
            # 现在只保留：扫描事实棋盘 → 直接进 C++ decide（number5_1 内部
            # 就是 decide 循环）。扫描仍需要，它属于**视觉输入**而非推理。
            # 每轮 decide 后刷新帮助页；直到候选集非空即停（与自动模式一致：
            # 一次决定产出一批候选交给人类点击）。
            if self._stop_requested():
                logger.info("收到停止请求，帮助模式在决策前安全退出")
                return
            self.num += 1
            try:
                with probe.span("help.number5_1"):
                    cell_value = self.number5_1(cell_value)
            except LowConfidenceScanError as e:
                # decide 内部重扫不可信（真机局末/窗口未置顶常见）：本轮作废，
                # **不得**让异常穿出去杀掉线程（否则界面又停在「等待」）。
                logger.warning("帮助模式决策内扫描不可信，本轮作废：%s", e)
                self.warning_signal.emit(f"本轮扫描不可信：{e}")
                self._emit_help_heatmap(cell_value)
                self.update_btn_list_signal.emit(self.pos_dict_list)
                return
            # 每轮决策后立即刷新帮助页（概率/最佳格/棋盘同帧到达）
            self._emit_help_heatmap(cell_value)

            if not self.pos_dict_list:
                # C++ decide 未产出任何候选：按「决策不可用」统一语义提示，
                # 不再回退到任何 Python 推理（避免掩盖真实失败）。
                logger.warning("帮助模式：C++ decide 未产出候选（盘面无可行建议）")
                self.warning_signal.emit(
                    "C++ decide 未给出候选：请检查设置中总雷数、宽度、长度是否正确")

            self.update_btn_list_signal.emit(self.pos_dict_list)

        except pyautogui.FailSafeException:
            probe.error("failsafe", "帮助模式 pyautogui FailSafeException")
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error as e:
            probe.error("win32ui_error", str(e), mode="help")
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except Exception as e:  # noqa: BLE001 —— 兜底：绝不让帮助线程静默死掉
            # ⚠️ 这一层是**真实必要**的：``run()`` 不捕获 help() 的异常，任何
            # 未预期错误都会让 QThread 直接死掉，UI 停在初始态，用户看到的就是
            # 「帮助模式彻底不工作」而没有任何提示。
            # 旧实现靠「complete_scan/mine_clear 的 ValueError 分支」顺带兜住
            # 一部分异常；去掉那段 Python 推理后必须显式兜底，并把原因**可见地**
            # 告诉用户，而不是让线程静默消失。
            logger.error("帮助模式异常退出：%s", e, exc_info=True)
            probe.error("help_thread_exception", f"{type(e).__name__}: {e}")
            self.Visible_signal.emit(False)
            try:
                self.warning_signal.emit(
                    f"帮助模式运行出错：{e}\n请检查设置与 C++ 决策模块是否可用")
            except Exception:  # noqa: BLE001 —— 告警失败不得掩盖原始异常
                pass
            return
        finally:
            import utils.probability as _prob_mod
            _prob_mod._DECISION_BUDGET_MULT = 1.0  # 预算乘数恢复（防泄漏到自动模式）


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
