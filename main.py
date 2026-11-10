#!/usr/bin/python3
# -*- coding: utf-8 -*-
# https://github.com/wangyuanchuan2022/minesweeper_help
import functools
import json
import multiprocessing
import os
import re
import sys
import time
import warnings
import weakref

import cv2
import numpy as np
import pyautogui
import win32gui
import win32ui
import win32con
from PIL import ImageGrab
from PyQt5 import QtGui, QtWidgets, sip, QtCore
from PyQt5.Qt import QInputDialog, QDialog, QFileDialog, QPixmap
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QMainWindow,
    QApplication,
    QMessageBox,
    QCheckBox,
    QPushButton,
)

from color import OKLCHGradient
from ui import Ui_Dialog, Ui_form, Ui_MainWindow
from ui.preview_popup import PreviewPopup, attach_hover_preview, PreviewHoverButton
from utils import (
    minesweeper_run,
    ClientToScreen,
    ScreenToClient,
    GetMousePosition,
    MouseWindowTread,
    set_top_window,
    Solver
)
import setting
from utils import native as _native_mod
from utils.probability import ProbabilityMixin as _ProbabilityMixin
from utils import e2e_probe as _probe
from logger import get_logger

logger = get_logger(__name__)

# PyQt5 5.15.x 搭配 PyQt5-sip>=12.13 时，定义 Qt 子类会触发 sipPyTypeDict 弃用告警。
# 这是第三方库版本组合导致的、无法在本项目内根治的提示，此处针对性忽略。
warnings.filterwarnings(
    "ignore",
    message="sipPyTypeDict",
    category=DeprecationWarning,
)

gradient_gen = OKLCHGradient()
oklch_grad = gradient_gen.create_gradient([(1, 0, 0), (0, 1, 0)])

# 协作式停止等待上限：Solver 主循环在安全点自查取消标志并自行返回
# （走 finally 落盘）；只有超过该时长仍未退出时才 terminate() 强杀兜底。
_SOLVER_STOP_TIMEOUT_MS = 3000


# ===================== UI 批 C：value_kind 语义分类（纯函数）=====================
# 背景（review/ui-migration-explore.md:127 风险 R1）：现有 solver 只发概率数值，
# 不同来源的数值语义完全不同，混排会让用户把"策略胜率"读成"安全概率"：
#   exact_safety       安全概率（精确解）——枚举/边界精确求解得出
#   uniform_prior      均匀先验（非精确解）——随机分支按剩余雷密度估的近似值
#   heuristic_win_rate 策略胜率（启发式）——win_rate 前瞻估计，**不是**安全概率
# 本批只做**发射侧打标 + UI 侧承接**，不改任何算法实现。
VALUE_KIND_EXACT = "exact_safety"
VALUE_KIND_PRIOR = "uniform_prior"
VALUE_KIND_WIN_RATE = "heuristic_win_rate"
VALUE_KIND_UNKNOWN = "unknown"

# 帮助页 exp 文案 → value_kind（exp 由 utils/probability.py 与 utils/deduction.py
# 各发射分支写死，此处只做映射不改上游；未列出的文案一律 unknown，宁可标未知
# 也不错标类型）
#
# ⚠️ 行号为 2026-11-07 方案 A 删除自动模式重复 Python 决策链后的**当前值**
# （`review/remove-py-decision-implementation.md`）。删除后帮助页只剩两条 exp
# 生产者：C++ decide 单次决策（`probability.py:707`）与 pbs/过程枚举
# （`probability.py:1061,1126,1138,1158,1173,1187`）+ deduction 的纯逻辑推导。
_EXP_TO_KIND = (
    ("C++ 三层决策", VALUE_KIND_EXACT),   # probability.py:707
    ("枚举得出", VALUE_KIND_EXACT),        # probability.py:1061,1126,1138,1158,1173,1187
    # 纯逻辑推导（数格子）得到 0/1 的确定结论：不是概率估计，是证明
    ("由(", VALUE_KIND_EXACT),             # deduction.py:68,95,145,173,199,226
    # ⚠️ 以下两条的**发射点已随自动模式 Python 决策链一并删除**（原
    # probability.py 的 win_rate 分支与随机分支）。映射保留是为了兼容历史
    # payload / 旧日志 / 单测（tests/test_ui_migration.py 仍有断言），不是死代码
    # 误留——但当前生产不再产生这两个 exp 值。
    ("胜率计算得出", VALUE_KIND_WIN_RATE),  # 生产者已删除（历史 exp 值）
    ("随机选择", VALUE_KIND_PRIOR),        # 生产者已删除（历史 exp 值）
)


def classify_value_kind(exp):
    """把帮助页候选的 ``exp`` 文案映射成 value_kind（未知则 unknown）。

    纯函数、无副作用，供 GUI 侧与测试直接调用。
    """
    text = "" if exp is None else str(exp)
    for needle, kind in _EXP_TO_KIND:
        if needle in text:
            return kind
    return VALUE_KIND_UNKNOWN


def classify_heatmap_kind(payload):
    """按 solver 发射路径判断热力图 ``prob`` 的语义类型。

    判定依据（只用现有 payload 字段，不猜算法内部）：
    - ``prob`` 为空 **且** ``best`` 也为空 → 新局清空/无估值 → unknown
      （``utils/solver.py:444`` 的 ``_emit_heatmap(clear=True)`` 分支）；
    - ``prob`` 为空 **但** ``best`` 存在 → 均匀先验兜底：自动模式已无随机分支
      （方案 A 删除），当前只可能来自 pbs 分支改点未开内部格时
      （``utils/probability.py:1098-1142`` 的 ``confidence = 1 - 剩余雷数/内部格数``）
      或历史 payload → ``uniform_prior``；
    - ``prob`` 非空 → ``exact_safety``：pbs 路径（``utils/probability.py:1194`` 的
      ``_prob = {tuple(c): float(r) for c, r in zip(clicks, res)}``）发的
      是「该格不是雷」的世界枚举占比，属于安全概率，**不是**策略胜率。

    ⚠️ 已知边界（未在本批修，需发射侧打标）：decide 分支的 ``best_conf`` 是
    C++ 返回的 ``confidence``（``utils/probability.py:697,741`` 的
    ``round(confidence, 5)``），与同一 payload 里 ``prob`` 的安全概率语义在大
    局面 pbs 路径下不同，而 payload 无来源字段可区分。本函数只判定 ``prob``
    的类型，决策卡对 ``best_conf`` 的展示沿用该类型并标注来源，
    不额外宣称它是安全概率（详见报告「未做与范围边界」）。
    """
    payload = payload or {}
    prob = payload.get("prob") or {}
    if not prob:
        if payload.get("best"):
            return VALUE_KIND_PRIOR
        return VALUE_KIND_UNKNOWN
    return VALUE_KIND_EXACT


def classify_payload_kind(payload):
    """payload 若自带 value_kind 则尊重之，否则按热力图路径推断。"""
    payload = payload or {}
    explicit = payload.get("value_kind")
    if explicit in (VALUE_KIND_EXACT, VALUE_KIND_PRIOR, VALUE_KIND_WIN_RATE):
        return explicit
    return classify_heatmap_kind(payload)


VALUE_KIND_TEXT = {
    VALUE_KIND_EXACT: "安全概率（精确解）",
    VALUE_KIND_PRIOR: "均匀先验（非精确解）",
    VALUE_KIND_WIN_RATE: "策略胜率（启发式，非安全概率）",
    VALUE_KIND_UNKNOWN: "无估值数据",
}

# best_conf 与 prob[best] 视为「同源同值」的容差：两处都是 5 位小数四舍五入
# （probability.py:697,741 的 round(..., 5)），单侧误差 ≤5e-6，双侧 ≤1e-5。
_BEST_CONF_EPS = 1e-5


def classify_decision_value_kind(payload):
    """判定 payload 里 ``best_conf`` 展示数值的语义类型（只用真实字段比对，不猜）。

    热力图 ``prob`` 是逐格「不是雷」的安全概率；决策用的 ``best_conf`` 在不同
    决策路径下语义**不同**（utils/probability.py，方案 A 删除自动模式 Python
    决策链后的当前发射点）：
      - decide 分支（:697 帮助模式 / :741 自动模式 ``best_conf = round(confidence,5)``）：
        值与 ``prob[best]`` 同源同值（C++ 返回的同一 confidence）→ ``exact_safety``；
        大局面 pbs 降级时 ``best_conf`` 仍来自 C++ confidence，与 ``prob`` 的
        逐格安全概率异值 → ``heuristic_win_rate``（保守判定，宁可标启发式）；
      - pbs 路径（:1194 ``_prob`` 由 ``zip(clicks, res)`` 组装）：``prob`` 覆盖候选格，
        ``best`` 不在其中时属均匀先验 → ``uniform_prior``。
    判定全部基于 payload 与真实发射点，不引入任何自造数值。
    """
    payload = payload or {}
    best = payload.get("best")
    best_conf = payload.get("best_conf")
    if best is None or best_conf is None:
        return VALUE_KIND_UNKNOWN
    prob = payload.get("prob") or {}
    if not prob:
        return VALUE_KIND_PRIOR
    try:
        key = (int(best[0]), int(best[1]))
        value = float(best_conf)
    except (TypeError, ValueError, IndexError):
        return VALUE_KIND_UNKNOWN
    if key not in prob:
        # prob 覆盖的是可推理候选格；best 不在其中 ⇒ 走的是剩余雷密度兜底
        return VALUE_KIND_PRIOR
    try:
        cell_value = float(prob[key])
    except (TypeError, ValueError):
        return VALUE_KIND_UNKNOWN
    if abs(cell_value - value) <= _BEST_CONF_EPS:
        return VALUE_KIND_EXACT
    return VALUE_KIND_WIN_RATE


class _PreviewFocusFilter(QtCore.QObject):
    """预览浮层的**键盘焦点**通道（eventFilter，与 hover 通道并存）。

    hover 通道（``ui.preview_popup.attach_hover_preview`` 的 Enter/Leave）只覆盖
    鼠标；纯键盘用户 Tab/方向键聚焦候选按钮时**不产生 Enter 事件**，浮层不会出现，
    设计规范 P0-UI-06「所有任务均可键盘完成、弹层 Esc 关闭」就不成立。
    本过滤器补 FocusIn（显示）/ FocusOut（未固定时关闭），与 hover 语义一致；
    取数失败一律静默（预览绝不打断主界面）。
    """

    def __init__(self, popup, payload_provider, parent=None):
        super().__init__(parent)
        self.popup = popup
        self.payload_provider = payload_provider

    def _safe_payload(self, obj):
        if not callable(self.payload_provider):
            return None
        try:
            return self.payload_provider(obj)
        except Exception:  # noqa: BLE001 —— focus 取数失败不得打断主界面
            return None

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QtCore.QEvent.FocusIn:
            payload = self._safe_payload(obj)
            if payload:
                cell = payload.get("cell")
                if cell:
                    self.popup.show_for_cell(cell, payload=payload,
                                             anchor_widget=obj)
        elif etype == QtCore.QEvent.FocusOut:
            self.popup.hide_if_unpinned()
        return False


class MyMainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()

        self.value = None  # 现在进度条数值 float
        self.value_per_interval = None  # 每隔0.1s增加的数值 float
        self.list_p = None  # 帮助页面上提示框中提到的横纵坐标的列表
        self.is_show = True  # 是否展示提示limit不足
        self.warning_window = None
        self.info_window = None
        # 提示框起始点坐标
        self._x = 391
        self._y = 119
        self.bx = None
        self.by = None
        self.with_help = True
        self.clicks = []  # 已知不是雷的方格
        self.mines = []  # 已知是雷的方格
        # 加载config（经 setting 解析绝对路径，不依赖 cwd）
        self.cfg = setting.load_cfg()
        # assign values to the variables
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]
        self._by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]

        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))

        # 左侧功能按钮不接受键盘焦点：启动时 Qt 会把焦点给 tab 序第一个按钮
        # （setting_2），若此时收到外部注入的按键（如 VK_SELECT，Qt 把它当作
        # 空格键），焦点按钮会被"按下"导致设置窗口自动弹出。这些按钮只供鼠标
        # 点击，禁用其键盘焦点即可（auto_play/help_human 的快捷键不受影响）。
        for _btn in (
                self.setting_2,
                self.auto_play,
                self.help_human,
                self.screenshot_help,
        ):
            _btn.setFocusPolicy(Qt.NoFocus)

        self.show()

        # 设置plainTextEdit滑动条样式，
        self.plainTextEdit.verticalScrollBar().setStyleSheet(
            """QScrollBar:vertical
        {
            margin:30px 0px 30px 0px;
            background-color:rgba(255, 255, 255, 0);
            border: none;
            width:20px;    
        }
        QScrollBar::handle:vertical
        {
            background-color:rgb(192, 192, 192);
            width:30px;
            border-radius:7px;
            min-height:40px;
            max-width:10px
        }
        QScrollBar::handle:vertical:hover
        {
            background-color:rgb(209, 209, 209);
            width:30px;
            border-radius:7px;
        }
        QScrollBar::add-line:vertical
        {
            subcontrol-origin: margin;
            border:none;
            height:0px;
        }
        
        QScrollBar::sub-line:vertical
        {
           subcontrol-origin: margin;
            border:none;
            height:0px;
        }
        QScrollBar::add-page:vertical
        {
          background-color:rgba(0, 0, 0, 0);
        }
        
        QScrollBar::sub-page:vertical
        {
            background-color:rgba(0, 0, 0, 0); 
        }
        QScrollBar::up-arrow:vertical
        {
          border:0px;
          width:0px;
          height:0px;
        }
        
        QScrollBar::up-arrow:vertical:pressed
        {
            border:0px;
            width:0px;
            height:0px;
        }
        QScrollBar::down-arrow:vertical
        {
            border:0px;
            width:0px;
            height:0px;
        }"""
        )

        # 更新屏幕
        self.timer = QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.update_screen)
        self.timer.start()

        # 绑定左侧
        self.auto_play.clicked.connect(self.auto_play_func)
        self.setting_2.clicked.connect(self.get_setting)
        # ⚠️ 必须用 lambda 丢弃 clicked 携带的 bool（否则会顶掉 start_help 默认值）
        # QPushButton.clicked 信号签名是 clicked(bool checked=False)。直接
        # connect 到 update_image(start_help=True) 时，Qt 会把该 bool 当作
        # start_help 传入 —— 对非 checkable 按钮恒为 False ⇒ 帮助线程永不启动
        # ⇒ 界面停在「等待帮助计算开始…」（用户实测 2026-11-08 的直接根因）。
        self.help_human.clicked.connect(lambda _checked=False: self.update_image())

        self.label.setPixmap(QtGui.QPixmap(setting.project_path("image/example.png")))

        self.stackedWidget.setCurrentIndex(0)

        self.auto_play_thread = Solver()

        # 设置一个值表示进度条的当前进度
        self.pv = 0
        self.pgb.setValue(self.pv)
        self.pgb.setVisible(False)
        self.pv_p3 = 0
        self.pgb_p3.setValue(self.pv_p3)
        self.pgb_p3.setVisible(False)
        self.plainTextEdit.setReadOnly(True)
        self.plainTextEdit.setPlaceholderText("这是一个程序输出窗口")
        self.return_to_main_page.setVisible(False)
        self.return_to_main_page.clicked.connect(
            lambda: self.stackedWidget.setCurrentIndex(0)
        )
        # 批 A close 通道：页面切换时关掉悬浮预览（浮层是独立顶层窗口，
        # 不随页面隐藏，留着会盖在新页面上）。
        self.stackedWidget.currentChanged.connect(self._hide_preview_popup)

        self.end_help_thread.setVisible(False)
        self.end_auto_play_thread.setVisible(False)

        self.end_auto_play_thread.clicked.connect(self.end_a_func)
        self.end_help_thread.clicked.connect(self.end_h_func)

        self.auto_play_thread.pv_signal.connect(lambda v: self.pgb.setValue(v))
        # 命令行输出窗口已取消：其承载的信息（局数/胜率/决策结果）由热力图统计栏呈现；
        # text_signal 信号链已整体移除（定义/连接/emit）。
        # 控件从布局移除并销毁；先前的滚动条样式调用在本行之前已执行完，不受影响。
        self.verticalLayout_2.removeWidget(self.plainTextEdit)
        self.plainTextEdit.setVisible(False)
        self.plainTextEdit.deleteLater()
        self.auto_play_thread.Visible_signal.connect(lambda v: self.pgb.setVisible(v))
        self.auto_play_thread.Visible_signal.connect(
            lambda v: self.end_auto_play_thread.setVisible(v)
        )
        self.auto_play_thread.warning_signal.connect(self._on_auto_warning)
        self.auto_play_thread.start_signal.connect(
            functools.partial(self.update_pgb, name="pgb")
        )
        self.auto_play_thread.end_signal.connect(self.end_timer)
        self.auto_play_thread.heatmap_signal.connect(self.update_heatmap)
        # ⚠️ 不能用 ``Visible_signal(False)`` 触发清理：该信号在 pbs 大局面近似
        # 分支（utils/probability.py:996）和每轮枚举结束（:1029）都会发 False
        # ——那是"隐藏进度条"，不是"线程停止"。用它清热力图会在计算中途把刚
        # 上色的格子清空（用户反馈"显示与真实进度对不上"的又一个来源）。
        # 正确锚点是 QThread 自带的 ``finished``：线程真正退出后才清旧 payload。
        self.auto_play_thread.finished.connect(self._on_auto_thread_finished)

        self.help_thread = Solver()
        self.help_thread.set_args(0)  # 设置为帮助模式。
        self.help_thread.pv_signal.connect(lambda v: self.pgb_p3.setValue(v))
        self.help_thread.Visible_signal.connect(lambda v: self.pgb_p3.setVisible(v))
        self.help_thread.Visible_signal.connect(
            lambda v: self.end_help_thread.setVisible(v)
        )
        # 帮助线程真正退出后清掉统一棋盘上的旧概率色/最佳框（防残留）
        self.help_thread.finished.connect(self._on_help_thread_finished)
        self.help_thread.update_btn_list_signal.connect(self.update_btn_list)
        # 帮助页实时热力图（2026-11-07 新增）：帮助线程与自动线程共用同一条
        # heatmap_signal 发射口（utils/solver.py:459 _emit_heatmap）；此前这里
        # 只接了自动线程的那条，所以帮助页永远没有热力图。
        self.help_thread.heatmap_signal.connect(self.update_help_heatmap)
        self.help_thread.warning_signal.connect(self._on_help_warning)
        self.help_thread.warning_signal_2.connect(self.help_thread_warning)
        self.help_thread.start_signal.connect(
            functools.partial(self.update_pgb, name="pgb_p3")
        )
        self.help_thread.end_signal.connect(self.end_timer)
        self.return_to_main_p3.clicked.connect(
            lambda: self.stackedWidget.setCurrentIndex(0)
        )
        self.click_all.clicked.connect(self.click_all_func)

        self.up_pgb_timer = QTimer()
        self.up_pgb_timer.setInterval(100)
        self.up_pgb_timer.timeout.connect(self.end_func)

        # 初始化帮助页面的提示块
        self.btn_list = []
        self.row_list = []
        self.col_list = []

        self.btn_width = 24
        for j in range(self.h):
            btn = PreviewHoverButton(text=str(j + 1), parent=self.frame_2)
            btn.setGeometry(0, (j + 1) * self.btn_width, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.col_list.append(btn)
            temp = []
            for i in range(self.w):
                btn = PreviewHoverButton(parent=self.frame_2)
                btn.setGeometry(
                    i * self.btn_width + self.btn_width,
                    j * self.btn_width + self.btn_width,
                    self.btn_width,
                    self.btn_width,
                )
                btn.setStyleSheet(
                    "QPushButton{border-radius:0px;border: none;"
                    'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(255, 255, 255, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
                temp.append(btn)
            self.btn_list.append(temp)
        self.set_btn_list_enable(False)
        for i in range(self.w):
            btn = PreviewHoverButton(text=str(i + 1), parent=self.frame_2)
            btn.setGeometry((i + 1) * self.btn_width, 0, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.row_list.append(btn)

        # 棋盘容器尺寸必须容得下 (w+1)×(h+1) 个 24px 格：ui/window.ui 里写死的
        # 最小高 400 只够 16 行（16*24=384）＋表头，最后一排格子会溢出容器
        # 8px（实测按钮底边 408 > 容器 400）——溢出的部分会被相邻控件盖住。
        # 帮助页统一采用自动页同款深色热力图容器，截图 QLabel 只作为事实层底图，
        # 不再让帮助页看起来像旧版白色棋盘。
        self.frame_2.setStyleSheet(
            "QFrame{background-color:rgba(44, 44, 44, 170);"
            "border: 1px solid rgba(255, 255, 255, 70);"
            "border-radius: 10px;}"
        )
        # 这里在建完网格后按真实行列数收紧，和 update_image 的重算保持一致。
        self.frame_2.setMinimumSize(
            (self.w + 1) * self.btn_width, (self.h + 1) * self.btn_width
        )
        # 棋盘宽度是**物理下限**（30 列 × 24px = 744），窗口缩到 900 时整页
        # 会被它顶出水平滚动条（实测 h=94），其余卡片也跟着左右拖动。
        # 让棋盘自己独占一个只横向滚动的容器：窗口够宽时与页面同步，
        # 不够宽时仅棋盘区域可横向拖动，卡片/统计栏保持自适应不被裁切。
        self._wrap_help_board_in_scroller()

        self.screenshot_help.clicked.connect(self.screenshot_help_func)

        # 自动页面热力图（与帮助页同款配色）+ 单行统计栏。
        # 网格独立于帮助页 btn_list 自建一套（帮助页 reset_btn_list 会整体
        # 重建自己的网格，共用会互相摧毁），尺寸变量也独立命名。
        # ⚠️ 从 2026-11-07 起热力图放进 page_2 的 QScrollArea 内容区
        # （auto_scroll_content / verticalLayout_auto_content，见 ui/window.py），
        # 不再直接插进 verticalLayout_2 —— 后者是页面级布局，塞满会被压缩重叠。
        self.heatmap_btn_list = []
        self.heatmap_frame = QtWidgets.QFrame(self.auto_scroll_content)
        # 容器高度必须容得下 (h+1) 行 24px 格：原写死 400 只够 16 行 + 8px，
        # 最后一排格子（y=384..408）溢出容器 8px 被相邻控件盖住。
        self.heatmap_frame.setMinimumSize(0, (self.h + 1) * 24)
        # 伪磨砂卡片：深色半透明透出页面底色 + 细描边 + 圆角（沿用原输出窗口的质感）
        self.heatmap_frame.setStyleSheet(
            "QFrame{background-color:rgba(44, 44, 44, 170);"
            "border: 1px solid rgba(255, 255, 255, 70);"
            "border-radius: 10px;}"
        )
        self.verticalLayout_auto_content.insertWidget(0, self.heatmap_frame)
        self.heatmap_label_stats = QtWidgets.QLabel(self.auto_scroll_content)
        # 统计栏：同款磨砂卡片 + 纯白文字；宽度自适应文字、水平居中（紧凑不撑满整行）
        self.heatmap_label_stats.setStyleSheet(
            'QLabel{background-color:rgba(44, 44, 44, 170);'
            'border: 1px solid rgba(255, 255, 255, 70);'
            'border-radius: 8px; color: rgb(255, 255, 255);'
            'font: 11pt "楷体"; padding: 4px 10px;}'
        )
        self.heatmap_label_stats.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed
        )
        self.heatmap_label_stats.setAlignment(Qt.AlignCenter)
        self.verticalLayout_auto_content.insertWidget(
            1, self.heatmap_label_stats, 0, Qt.AlignHCenter
        )
        self.heatmap_label_stats.setText("等待自动扫雷开始…")
        self.heatmap_btn_width = 24
        for j in range(self.h):
            temp = []
            for i in range(self.w):
                btn = PreviewHoverButton(parent=self.heatmap_frame)
                btn.setGeometry(
                    i * self.heatmap_btn_width + self.heatmap_btn_width,
                    j * self.heatmap_btn_width + self.heatmap_btn_width,
                    self.heatmap_btn_width,
                    self.heatmap_btn_width,
                )
                btn.setStyleSheet(self._HM_BASE)
                btn.show()
                temp.append(btn)
            self.heatmap_btn_list.append(temp)
        self._hm_last = {}  # 样式去重缓存：按钮 id -> (style, text)，防重绘闪烁

        # ===================== 帮助页统一单一棋盘（2026-11-07 重做）=====================
        # 上一版在棋盘 frame_2 之外**又建了一套** help_heatmap_frame /
        # help_heatmap_btn_list 热力图网格。两套网格各自独立渲染：
        #   · btn_list  ← update_btn_list(pos_dict_list)（候选序号/雷/安全/推荐）
        #   · help_heatmap_btn_list ← update_help_heatmap(payload)（cell_value/prob/best）
        # 用户看到的是「上方一个棋盘、下方又一个棋盘」，且两者可能不同步
        # （候选先到 / payload 后到时，两个棋盘读数互相矛盾）。
        #
        # 现在**只保留 btn_list 一套可见棋盘**：它同时承载事实棋盘
        # （cell_value）、概率色（prob）、证明安全/雷（10/11）、最佳格（best）
        # 与候选叠加（pos_dict_list），渲染唯一入口 = `_render_help_board()`
        # （见下方实现，单一循环一次成型，杜绝「后到的旧数据覆盖新数据」）。
        # 这里只保留一条非网格的统计栏（纯文本，不构成第二个棋盘）。
        self.help_board_label_stats = QtWidgets.QLabel(self.help_scroll_content)
        self.help_board_label_stats.setObjectName("helpBoardStats")
        self.help_board_label_stats.setStyleSheet(
            'QLabel{background-color:rgba(44, 44, 44, 170);'
            'border: 1px solid rgba(255, 255, 255, 70);'
            'border-radius: 8px; color: rgb(255, 255, 255);'
            'font: 11pt "楷体"; padding: 4px 10px;}'
        )
        self.help_board_label_stats.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed
        )
        self.help_board_label_stats.setAlignment(Qt.AlignCenter)
        self.help_board_label_stats.setText("等待帮助计算开始…")
        # 统计栏放在棋盘之后（append 到内容布局末尾，不覆盖棋盘）
        self.verticalLayout_help_content.addWidget(
            self.help_board_label_stats, 0, Qt.AlignHCenter
        )
        # 统一棋盘的样式去重缓存：id(btn) -> (style, text)。
        # ⚠️ reset_btn_list 会 sip.delete 重建整批按钮，新按钮可能复用刚释放
        # 对象的 id——缓存必须在重建处清空（main.py:reset_btn_list），否则新按钮
        # 被误判成「样式没变」而跳过 setStyleSheet，留下上一帧的残留外观。
        self._help_board_last = {}
        # 当前挂了候选点击 handler 的按钮集合（失去候选身份时要断开，防旧弹窗）
        self._help_click_buttons = set()
        self._help_candidate_style = (
            "QPushButton{border-radius:0px; border: 2px solid rgb(0, 0, 255);"
            'background-color:rgba(255, 0, 0, 255);font: 9pt "楷体"}'
            "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
            "color:rgba(210, 210, 210, 0);}"
        )

        # ===================== UI 批 B：紧凑现代信息区（运行期注入）=====================
        # 约束：不改 ui/window.py（pyuic 生成物），全部在运行期插入到既有布局里；
        # 保持 QStackedWidget 与两条 Solver 线程原样工作。
        # 信息契约（批 C 接线）：
        #   状态条   —— Solver 线程状态 / 最近动作 / 已完局胜率（事件+轮询混合）
        #   决策卡   —— 当前建议格 / value_kind 类型标签 / 数值 / 来源说明
        #   图例     —— 五项语义（已证明安全/已证明是雷/概率估计/推荐未证明/无数据）
        #   候选详情 —— pos_dict_list 逐项：序号、坐标、类型、数值、是否推荐
        self._compact_ready = False
        self._last_cell_value = None          # 最近一次真实棋盘（帮助页）
        self._last_heatmap_payload = None     # 最近一次热力图 payload
        self._last_candidates = []            # 最近一次 pos_dict_list
        self._preview_popup = None            # 悬浮预览（懒建，全窗口复用）
        # 按「悬停格」缓存真实状态转移：key=(棋盘指纹, a, w, h, x, y)。
        # 用户裁决 2026-11-08：转移必须是点击鼠标所在格之后的分布，
        # 不能共用 decide 针对 best_cell 算的那一份。
        self._per_cell_transitions = {}
        # 已挂预览过滤器的按钮（弱引用集，防重复挂接）。
        # ⚠️ 不能用 id(button) 集合：reset_btn_list 会 sip.delete 整批按钮重建
        # （main.py:893-...），新按钮可能复用刚释放对象的 id，按 id 去重会把新
        # 按钮误判成"已挂"而静默跳过接线。弱引用集按对象身份去重，对象销毁即
        # 自动移出，且不阻止按钮回收。
        self._preview_buttons = weakref.WeakSet()
        self._build_compact_ui()
        self._compact_ready = True

    # ------------------------------------------------------------------
    # 线程告警出口（2026-11-07：扫描噪声不弹窗）
    # ------------------------------------------------------------------
    def _on_auto_warning(self, msg):
        """自动线程的 warning 出口：过滤「局中扫描不可信」的噪声弹窗。

        用户明确要求：「自动模式不要弹『点击后重扫：9/9 格无法可信识别』」。
        该文案来自 ``utils/vision._apply_recognition``（识别比例超阈值时的告警），
        在自动模式下**属于正常节奏**——局末失败弹窗盖住棋盘、连点后 flood-fill
        未稳定等都会触发，主循环会自然进入下一轮，不需要用户介入。此前它经
        ``warning_signal`` 直接弹 QMessageBox，用户会被反复打断。

        保留的告警：设置类错误（"请检查设置中总雷数/宽度/长度"）、decide 不可用
        等需要用户介入的消息，仍然弹窗。过滤只针对「无法可信识别」这一类扫描
        噪声，且**不是静默丢弃**——降级为 INFO 日志，便于事后取证。
        """
        text = "" if msg is None else str(msg)
        if "无法可信识别" in text:
            logger.info("自动模式扫描告警（不弹窗，按设计降级为日志）：%s", text)
            return
        QMessageBox.warning(self, "错误", text)

    def _on_help_warning(self, msg):
        """帮助线程的 warning 出口：同样的扫描噪声过滤，其余照常提示。"""
        text = "" if msg is None else str(msg)
        if "无法可信识别" in text:
            logger.info("帮助模式扫描告警（不弹窗，按设计降级为日志）：%s", text)
            return
        QMessageBox.about(self, "提示", text)

    # ------------------------------------------------------------------
    # 线程生命周期 → 热力图状态清理
    # ------------------------------------------------------------------
    def _on_auto_thread_finished(self):
        """自动线程真正退出后保留最后一帧真实热力图。

        ``finished`` 表示本轮自动任务结束，不表示最后 payload 失效；用户需要在
        win/read 阶段看到最终建议和胜率。只有下一局启动、显式停止后才由清理入口
        复位，因此这里不再抹掉最后一帧概率色/best 框。
        """
        self.set_solver_status("自动运行完成 · 保留最后一帧真实决策")
        if hasattr(self, "heatmap_label_stats"):
            text = self.heatmap_label_stats.text()
            if text and "状态:" in text and "已结束" not in text:
                self.heatmap_label_stats.setText(text.replace("状态: ", "状态: 已结束 · ", 1))

    def _on_help_thread_finished(self):
        """帮助线程结束后**保留**最后一帧候选与热力图。

        旧实现在这里调 ``_clear_heatmap_state("help")``，把 ``_last_candidates``
        与 ``_last_heatmap_payload`` 一起清空——而帮助线程刚发完
        ``update_btn_list_signal`` 就结束，于是「候选刚画上棋盘就被抹掉」，
        ``click_all`` 的点击目标随之消失，用户看到的就是「点击所有确定方格
        完全没反应」。帮助页与自动页一致：线程结束不等于数据失效，只有下一次
        开始/显式停止才由清理入口复位。
        """
        self.set_solver_status("帮助分析完成 · 保留最后一帧真实候选")
        if hasattr(self, "help_board_label_stats"):
            text = self.help_board_label_stats.text()
            if text and "已结束" not in text:
                self.help_board_label_stats.setText(f"已结束 · {text}")

    # ------------------------------------------------------------------
    # UI 批 B：紧凑现代信息区
    # ------------------------------------------------------------------
    _CARD_QSS = (
        "QFrame#uiCard{background-color:rgba(44, 44, 44, 176);"
        "border: 1px solid rgba(255, 255, 255, 64);border-radius: 10px;}"
        "QLabel{color: rgb(238, 241, 244);font: 10pt \"Microsoft YaHei\";}"
        "QLabel#uiCardTitle{color: rgb(176, 186, 195);"
        "font: 9pt \"Microsoft YaHei\";}"
        "QLabel#uiDecisionValue{color: rgb(151, 232, 201);"
        "font: 600 15pt \"Cascadia Mono\";}"
        "QLabel#uiDecisionKind{color: rgb(255, 214, 122);"
        "font: 9pt \"Microsoft YaHei\";}"
    )

    def _make_card(self, parent, title):
        """建一张紧凑卡片：标题行 + 内容 QLabel。返回 (frame, body_label)。"""
        frame = QtWidgets.QFrame(parent)
        frame.setObjectName("uiCard")
        frame.setStyleSheet(self._CARD_QSS)
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        head = QtWidgets.QLabel(title, frame)
        head.setObjectName("uiCardTitle")
        lay.addWidget(head)
        body = QtWidgets.QLabel("", frame)
        body.setWordWrap(True)
        lay.addWidget(body)
        return frame, body

    def _build_compact_ui(self):
        """把状态条/决策卡/图例/候选详情注入自动页与帮助页（不动生成物）。

        ⚠️ 2026-11-07 遮挡修复后，这里注入的目标布局改了：
        - 自动页：``verticalLayout_auto_content``（QScrollArea 内容），不用页面级
          ``verticalLayout_2``——后者只剩滚动区/按钮条/进度条，塞卡片会让滚动区
          被压到 0 高；
        - 帮助页：``verticalLayout_help_content``，且**追加在棋盘 frame_2 之后**
          （append），保证图例/候选卡永远在棋盘下方、不覆盖棋盘。
        """
        # ---- 自动页（page_2）：状态条 + 决策卡 + 图例 + 候选详情 ----
        self.status_card, self.status_label = self._make_card(
            self.auto_scroll_content, "求解状态")
        self.status_label.setText("就绪 · 等待自动扫雷开始")
        self.verticalLayout_auto_content.insertWidget(2, self.status_card)

        self.decision_card, self.decision_body = self._make_card(
            self.auto_scroll_content, "当前决策")
        self._decision_value = QtWidgets.QLabel("—", self.decision_card)
        self._decision_value.setObjectName("uiDecisionValue")
        self._decision_kind = QtWidgets.QLabel("无估值数据", self.decision_card)
        self._decision_kind.setObjectName("uiDecisionKind")
        self._decision_value.setParent(self.decision_card)
        self._decision_kind.setParent(self.decision_card)
        _dlay = self.decision_card.layout()
        _dlay.addWidget(self._decision_value)
        _dlay.addWidget(self._decision_kind)
        self.decision_body.setText("等待第一轮真实决策数据…")
        self.verticalLayout_auto_content.insertWidget(3, self.decision_card)

        self.legend_card, self.legend_body = self._make_card(
            self.auto_scroll_content, "图例（五项语义）")
        self.legend_body.setText(
            "■ 已证明安全（仅 is_best 且 confidence=1）  "
            "■ 已证明是雷（is_mine）  "
            "■ 概率估计（安全概率/u 均匀先验/w 策略胜率）  "
            "■ 推荐但未证明（金框）  ■ 无数据（浅灰）"
        )
        self.verticalLayout_auto_content.insertWidget(4, self.legend_card)

        self.candidate_card, _ = self._make_card(
            self.auto_scroll_content, "候选详情（最多 5 项）")
        self.candidate_list = QtWidgets.QListWidget(self.candidate_card)
        self.candidate_list.setObjectName("uiCandidateList")
        self.candidate_list.setMaximumHeight(96)
        self.candidate_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;"
            "color: rgb(238, 241, 244);font: 9pt \"Cascadia Mono\";}"
            "QListWidget::item{padding:2px 4px;}"
        )
        self.candidate_card.layout().addWidget(self.candidate_list)
        self.verticalLayout_auto_content.insertWidget(5, self.candidate_card)

        # ---- 帮助页（page_3）：图例 + 候选详情（追加在棋盘之后）----
        self.help_legend_card, self.help_legend_body = self._make_card(
            self.help_scroll_content, "帮助图例 / 候选语义")
        self.help_legend_body.setText(
            "颜色=安全概率（精确解）；「策略胜率」是启发式胜率，不是安全概率；"
            "「均匀先验」是非精确近似值。悬停候选格查看 7×7 局部预览。"
        )
        self.verticalLayout_help_content.addWidget(self.help_legend_card)

        self.help_candidate_card, _ = self._make_card(
            self.help_scroll_content, "候选详情（真实 pos_dict_list）")
        self.help_candidate_list = QtWidgets.QListWidget(self.help_candidate_card)
        self.help_candidate_list.setObjectName("uiHelpCandidateList")
        self.help_candidate_list.setMaximumHeight(84)
        self.help_candidate_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;"
            "color: rgb(238, 241, 244);font: 9pt \"Cascadia Mono\";}"
            "QListWidget::item{padding:2px 4px;}"
        )
        self.help_candidate_card.layout().addWidget(self.help_candidate_list)
        self.verticalLayout_help_content.addWidget(self.help_candidate_card)

    # ---- 状态条 ----
    def set_solver_status(self, text):
        """更新状态条文案（供线程状态/信号槽调用，幂等去重）。"""
        if not getattr(self, "_compact_ready", False):
            return
        if self.status_label.text() != text:
            self.status_label.setText(text)

    # ---- 决策卡（批 C：value_kind 真实接入）----
    def update_decision_card(self, cell, value, value_kind, source=""):
        """渲染当前决策：坐标 / 类型标签 / 数值 / 来源。

        value_kind 三分类必须显式显示，heuristic_win_rate 绝不写成安全概率。
        """
        if not getattr(self, "_compact_ready", False):
            return
        kind_text = VALUE_KIND_TEXT.get(value_kind, VALUE_KIND_TEXT[VALUE_KIND_UNKNOWN])
        if value is None:
            value_text = "—"
        else:
            try:
                value_text = f"{float(value) * 100:.2f}%"
            except (TypeError, ValueError):
                value_text = "—"
        pos_text = f"({cell[0]}, {cell[1]})" if cell else "—"
        self.decision_body.setText(
            f"建议点击 {pos_text}" + (f" · 来源：{source}" if source else "")
        )
        if self._decision_value is not None:
            self._decision_value.setText(value_text)
        if self._decision_kind is not None:
            self._decision_kind.setText(kind_text)

    # ---- 候选详情 ----
    def _fill_candidate_list(self, widget, pos_dict_list):
        if widget is None:
            return
        widget.clear()
        for idx, pos_dict in enumerate(list(pos_dict_list or [])[:5], start=1):
            pos = pos_dict.get("pos")
            kind = classify_value_kind(pos_dict.get("exp"))
            conf = pos_dict.get("confidence")
            try:
                pct = f"{float(conf) * 100:.2f}%"
            except (TypeError, ValueError):
                pct = "—"
            flag = "推荐" if pos_dict.get("is_recommend") else (
                "已证明" if pos_dict.get("is_best") else "普通")
            widget.addItem(
                f"#{idx} {tuple(pos) if pos else '—'} · {VALUE_KIND_TEXT.get(kind, kind)}"
                f" · {pct} · {flag}"
            )

    # ---- 悬浮预览（懒建 + 幂等挂接）----
    def _ensure_preview_popup(self):
        if self._preview_popup is None:
            self._preview_popup = PreviewPopup()
        return self._preview_popup

    def _transitions_for_cell(self, i, j):
        """返回「点击悬停格 (i,j) 之后」的真实状态转移 (transitions, meta)。

        🔴 用户第三次指出「悬浮窗展示的不是点击悬浮在那个格子上之后的
        转移概率」。根因：旧实现三个 provider 全从 _last_heatmap_payload
        取同一份 transitions —— 那是 decide 针对 best_cell 算的，悬停任何格
        都显示同一份。现在改为按 (i,j) 调 C++ transitions_for（复用 decide
        缓存的世界集，7×7 局面以该格为中心），并按格缓存结果。

        旧 pyd 无该导出时回退到 payload 里那一份（保持兼容），但那是
        best_cell 的、语义不对应；新 pyd 装好后即按格生效。
        """
        payload = self._last_heatmap_payload or {}
        cv = payload.get("cell_value")
        if cv is None:
            cv = getattr(self, "_last_cell_value", None)
        if cv is None or not _native_mod.has_transitions_for():
            return (payload.get("transitions"),
                    payload.get("transition_meta"))
        try:
            fp = hash(bytes(np.asarray(cv, dtype=np.int32).tobytes()))
            key = (fp, getattr(self, "a", None),
                   getattr(self, "w", None), getattr(self, "h", None),
                   int(i), int(j))
        except (TypeError, ValueError, AttributeError):
            key = None
        cache = getattr(self, "_per_cell_transitions", None)
        if cache is None:
            cache = self._per_cell_transitions = {}
        if key is not None and key in cache:
            return cache[key]
        r = _native_mod.transitions_for(cv, self.a, self.w, self.h, i, j)
        if r is None:
            result = (payload.get("transitions"),
                      payload.get("transition_meta"))
        else:
            meta = dict(r.get("transition_meta") or {})
            reason = r.get("transition_reason")
            if reason and not r.get("transitions"):
                meta["reason"] = str(reason)
            result = (_ProbabilityMixin._parse_transitions(r),
                      meta or None)
        if key is not None:
            # 缓存条目按棋盘指纹隔离；盘面变化后旧 key 自然不再命中，
            # 但仍限制总量，防止长时间悬停无限增长。
            if len(cache) > 512:
                cache.clear()
            cache[key] = result
        return result

    def _preview_payload_for_candidate(self, pos_dict):
        """把 pos_dict 项转成预览浮层 payload（真实字段，不合成概率）。

        ``transitions`` 按该候选格坐标调 C++ 单独计算，而不是共用决策帧里
        针对最优格算的那一份。
        """
        pos = pos_dict.get("pos")
        cell = tuple(pos) if pos else None
        transitions, meta = (self._transitions_for_cell(*cell)
                             if cell else (None, None))
        return {
            "cell": cell,
            "value": pos_dict.get("confidence"),
            "value_kind": classify_value_kind(pos_dict.get("exp")),
            "source": str(pos_dict.get("exp") or ""),
            "cell_value": self._last_cell_value,
            "transitions": transitions,
            "transition_meta": meta,
        }

    def _preview_payload_for_heatmap_cell(self, i, j):
        """把热力图 (i, j)（1-based）转成预览 payload：转移按该格算。"""
        payload = self._last_heatmap_payload or {}
        prob = payload.get("prob") or {}
        cell_value = payload.get("cell_value")
        if cell_value is None:
            cell_value = self._last_cell_value
        transitions, meta = self._transitions_for_cell(i, j)
        return {
            "cell": (i, j),
            "value": prob.get((i, j)),
            "value_kind": classify_payload_kind(payload),
            "source": "自动热力图 prob",
            "cell_value": cell_value,
            "transitions": transitions,
            "transition_meta": meta,
        }

    def _attach_preview(self, button, provider):
        """给按钮挂预览（hover + 键盘焦点双通道，同一按钮只挂一次）。

        2026-11-07 起棋盘/热力图按钮是 ``PreviewHoverButton``：它自己覆写
        enterEvent/leaveEvent/focusInEvent/focusOutEvent 真实投递到浮层
        （不依赖 WA_Hover 的 HoverEnter，也不受 24×24 密集网格影响）。

        ⚠️ 两类按钮的挂接方式必须分开，否则「延迟关闭」被破坏：
        - ``PreviewHoverButton``：只 ``bind_preview``（绑 popup + provider）。
          不能再挂 ``attach_hover_preview`` 的 eventFilter —— 事件过滤器先于
          控件自身的 leaveEvent 执行，会在 Leave 时立刻 ``hide_if_unpinned()``，
          把按钮刚安排好的 220ms 延迟关闭直接抵消（用户就点不到浮层的「固定」）。
        - 其它普通 QWidget：走 ``attach_hover_preview`` + focus 过滤器（保留
          既有契约，供非按钮控件与既有单测使用）。
        """
        if button is None:
            return
        try:
            popup = self._ensure_preview_popup()
            if isinstance(button, PreviewHoverButton):
                # 按钮对象会跨 payload/候选轮次复用；必须每次更新 provider，
                # 不能因为对象已挂接就 return，否则 hover 闭包会永久指向上一轮
                # 的 pos_dict，表现为“下一步预示不成立/一直显示旧格”。
                button.bind_preview(popup, provider)
            elif button in self._preview_buttons:
                # 普通 QWidget 同样更新既有过滤器的数据源，避免旧闭包残留。
                filt = getattr(button, "_preview_filter", None)
                if filt is not None:
                    filt.payload_provider = provider
                focus_filter = getattr(button, "_preview_focus_filter", None)
                if focus_filter is not None:
                    focus_filter.payload_provider = provider
            else:
                attach_hover_preview(button, popup, provider)
                focus_filter = _PreviewFocusFilter(popup, provider, parent=button)
                button.installEventFilter(focus_filter)
                # 持有引用防 GC（同 attach_hover_preview 的 _preview_filter 约定）
                button._preview_focus_filter = focus_filter
            self._preview_buttons.add(button)
        except Exception as e:  # noqa: BLE001 —— 预览失败绝不打断主界面
            logger.debug("挂载预览失败（忽略）: %s", e)

    def _hide_preview_popup(self, *_args):
        """关闭预览浮层（页面切换/主窗口关闭等 close 通道，幂等）。"""
        popup = getattr(self, "_preview_popup", None)
        if popup is None:
            return
        try:
            popup.close_popup()
        except Exception as e:  # noqa: BLE001 —— 关闭失败不得影响主流程
            logger.debug("关闭预览浮层失败（忽略）: %s", e)

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        """主窗口关闭：先摘掉浮层的应用级 eventFilter，再走默认关闭。

        不给 ``Qt.Tool`` 浮层收尾就退出，应用级过滤器会残留在 QApplication 上
        （``ui/preview_popup.py:420-434`` 的安装/摘除成对语义）。
        """
        self._hide_preview_popup()
        super().closeEvent(a0)

    def end_timer(self, s):
        # 已经计算完成
        self.up_pgb_timer.stop()
        self.pgb.setValue(100)
        self.pgb_p3.setValue(100)

    def update_pgb(self, t, name):
        # 根据上一次计算的速度更新进度条
        pgb = getattr(self, name, None)
        total, speed, _ = t
        self.value_per_interval = 0.1 / (total / speed) * 100
        self.value = 0
        pgb.setValue(0)
        self.up_pgb_timer.start()

    def end_func(self):
        # 根据上一次计算的速度更新进度条
        self.pgb_p3.setValue(int(self.value))
        self.pgb.setValue(int(self.value))
        self.value += self.value_per_interval
        if self.value >= 100:
            self.value = 100
            self.up_pgb_timer.stop()

    def screenshot_help_func(self):
        # Reload the game
        self.reload()
        try:
            # Run the game and get the window handle
            hwnd = minesweeper_run(self.path)
        except Exception:
            # Show an error message if the path is not correct
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            return

        # Disable help：用显式参数表示「本次只截图、不启动帮助线程」，
        # 不要污染 self.with_help 这个跨调用的状态标志。
        # ⚠️ 旧实现写 `self.with_help = False` 再调 update_image()：虽然
        # update_image 末尾会把它改回 True，但**本次启动已被跳过**；更糟的是
        # 任何提前 return 都会把 False 留在实例上，导致之后点「帮助人类」时
        # `if self.with_help:` 为假 → 帮助线程根本不启动 → 用户看到的就是
        # 「帮助模式彻底不工作」。
        self.update_image(start_help=False)

        # Create a new instance of the ScreenShot class
        self.screen_shot = ScreenShot(hwnd)
        # Set the window modality to application modal
        self.screen_shot.setWindowModality(Qt.ApplicationModal)
        # Show the window
        self.screen_shot.show()
        # Wait for the window to be created
        time.sleep(0.05)
        # Find the window handle of the "截图帮助" window
        hwnd = win32gui.FindWindow(None, "截图帮助")
        # Show the window
        win32gui.ShowWindow(hwnd, 1)
        # Set the top window
        set_top_window(hwnd)

    def _stop_solver_thread(self, thread, label):
        """停止 Solver 线程：先协作式取消，超时才强杀兜底。

        顺序（见 review/fix-config-lifecycle.md）：
        1. 未运行 → 直接返回。
        2. ``request_cancel()`` 置位取消标志，主循环在安全点自行 return，
           正常走 ``finally``（统计落盘、预算乘数恢复）。
        3. 限时 ``wait()``；仍未退出才 ``terminate()`` —— 这是不可恢复故障的
           最后兜底，会绕过 Python ``finally``，因此必须记录 warning 留痕。
        """
        try:
            if thread is None or not thread.isRunning():
                return
        except RuntimeError:
            return
        thread.request_cancel()
        if thread.wait(_SOLVER_STOP_TIMEOUT_MS):
            return
        logger.warning("%s 未在 %d ms 内协作退出，改用 terminate() 强杀兜底"
                       "（本次退出不保证 finally 清理）", label, _SOLVER_STOP_TIMEOUT_MS)
        thread.terminate()
        thread.wait(1000)

    def end_h_func(self):
        # 终止帮助程序的计算（协作取消优先，terminate 仅兜底）
        self.pgb_p3.setVisible(False)
        self._stop_solver_thread(self.help_thread, "帮助线程")
        self.end_help_thread.setVisible(False)
        self.set_btn_list_enable(True)
        self.return_to_main_p3.setVisible(True)
        self.up_pgb_timer.stop()
        QMessageBox.about(self, "提示", "您可以降低设置中的limit来减少计算时间\n" "但减少limit可能会导致结果不全面")

    def end_a_func(self):
        # 终止自动程序的计算（协作取消优先，terminate 仅兜底）
        self.pgb.setVisible(False)
        self._stop_solver_thread(self.auto_play_thread, "自动线程")
        self.end_auto_play_thread.setVisible(False)
        self.set_btns_Enabled(True)
        self.pgb_p3.setVisible(False)
        self._stop_solver_thread(self.help_thread, "帮助线程")
        self.end_help_thread.setVisible(False)
        self.return_to_main_page.setVisible(True)
        self.up_pgb_timer.stop()
        QMessageBox.about(self, "提示", "您可以降低设置中的limit来减少计算时间\n" "但减少limit可能会导致结果不全面")

    def set_btns_Enabled(self, v: bool):
        # 设置左侧按钮Enable or not
        self.screenshot_help.setEnabled(v)
        self.help_human.setEnabled(v)
        self.auto_play.setEnabled(v)
        self.setting_2.setEnabled(v)

    def reload(self):
        # 重新加载（绝对路径，不依赖 cwd）
        cfg = setting.load_cfg()
        new_w, new_h = cfg["w"], cfg["h"]
        size_changed = (new_w != getattr(self, "w", None)
                        or new_h != getattr(self, "h", None))
        self.w = new_w
        self.h = new_h
        self._bx = cfg["bx"]  #
        self._by = cfg["by"]  #
        self.cell_width = cfg["cell_width"]
        self.a = cfg["a"]  # 总雷数
        self.path = cfg["path"]
        # 🔴 进程内改 W/H 后自动页热力图长宽必须跟着变（用户实测 2026-11-08）：
        # 旧实现只在 __init__ 里按初始 w/h 建一次 heatmap_btn_list，
        # reload() 只更新 self.w/self.h 数值，网格本身永远不重建 ⇒ 界面
        # 仍按旧尺寸渲染（_render_heatmap_grid 用 min(self.h, len(btn_list))
        # 保护，多出来的格子根本不存在）。帮助页有 reset_btn_list 会重建，
        # 自动页此前没有对应路径。
        if size_changed and getattr(self, "heatmap_frame", None) is not None:
            self._rebuild_heatmap_grid()

    def _rebuild_heatmap_grid(self):
        """按当前 self.w/self.h 重建自动页热力图按钮网格。

        与帮助页 reset_btn_list 对称：删除旧按钮（含样式去重缓存与 hover
        绑定），按新行列数重建，并同步容器最小高度。已有 payload 不在这里
        重绘——下一帧 update_heatmap 会自然用新网格渲染。
        """
        old = getattr(self, "heatmap_btn_list", None) or []
        frame = getattr(self, "heatmap_frame", None)
        for row in old:
            for btn in row:
                try:
                    btn.deleteLater()
                    sip.delete(btn)
                except (RuntimeError, AttributeError):
                    pass
        # 按钮对象被 sip.delete 后 id 可能被复用，样式缓存必须整批清空，
        # 否则新格会被误判成「样式没变」而跳过 setStyleSheet（残留旧色）。
        self._hm_last = {}
        self.heatmap_btn_list = []
        if frame is None:
            return
        frame.setMinimumSize(0, (self.h + 1) * self.heatmap_btn_width)
        for j in range(self.h):
            temp = []
            for i in range(self.w):
                btn = PreviewHoverButton(parent=frame)
                btn.setGeometry(
                    i * self.heatmap_btn_width + self.heatmap_btn_width,
                    j * self.heatmap_btn_width + self.heatmap_btn_width,
                    self.heatmap_btn_width,
                    self.heatmap_btn_width,
                )
                btn.setStyleSheet(self._HM_BASE)
                btn.show()
                temp.append(btn)
            self.heatmap_btn_list.append(temp)

    def set_btn_list_enable(self, b: bool):
        # 设置帮助页面按钮Enable or not
        for j in range(len(self.btn_list)):
            for i in range(len(self.btn_list[0])):
                self.btn_list[j][i].setEnabled(b)

    def get_setting(self):
        self.edit_window = EditWindow()
        self.edit_window.setWindowModality(Qt.ApplicationModal)
        self.edit_window.show()

    def reset_btn_list(self):
        # 重置按钮列表
        # ⚠️ 统一棋盘的样式去重缓存按 id(btn) 索引，而这里会 sip.delete 整批
        # 按钮并重建：新按钮可能复用刚释放对象的 id，缓存若不清就会被误判成
        # 「样式没变」而跳过 setStyleSheet，留下上一局的残留外观。故必须同步清空。
        self._help_board_last = {}
        self._help_click_buttons = set()
        for j in range(len(self.btn_list)):
            for i in range(len(self.btn_list[0])):
                self.btn_list[j][i].deleteLater()
                sip.delete(self.btn_list[j][i])
        for i in range(len(self.row_list)):
            self.row_list[i].deleteLater()
            sip.delete(self.row_list[i])
        for j in range(len(self.col_list)):
            self.col_list[j].deleteLater()
            sip.delete(self.col_list[j])

        self.btn_list = []
        self.row_list = []
        self.col_list = []
        for j in range(self.h):
            temp = []
            for i in range(self.w):
                btn = PreviewHoverButton(parent=self.frame_2)
                btn.setGeometry(
                    i * self.btn_width + self.btn_width,
                    j * self.btn_width + self.btn_width,
                    self.btn_width,
                    self.btn_width,
                )
                btn.setStyleSheet(
                    "QPushButton{border-radius:0px;border: none;"
                    'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(255, 255, 255, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
                btn.show()
                temp.append(btn)
            self.btn_list.append(temp)

        for j in range(self.h):
            btn = PreviewHoverButton(text=str(j + 1), parent=self.frame_2)
            btn.setGeometry(0, (j + 1) * self.btn_width, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.col_list.append(btn)

        for i in range(self.w):
            btn = PreviewHoverButton(text=str(i + 1), parent=self.frame_2)
            btn.setGeometry((i + 1) * self.btn_width, 0, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体";}'
            )
            btn.show()
            self.row_list.append(btn)

    def update_image(self, start_help=True):
        # 启动帮助程序
        self.click_all.setEnabled(False)
        self.return_to_main_p3.setVisible(False)
        self.reload()
        self.clicks = []
        self.mines = []

        # 🔴 模式互斥（用户实测「点帮助后卡住」的确定根因之一）：
        # 进入帮助模式前必须**先停掉自动线程**。两者共用同一个扫雷窗口，
        # 自动线程会持续「点击格子 → 重扫」，帮助线程同时扫描/决策时，
        # 盘面每轮都在变（日志实测 num9 469→478→435 反复跳、每轮
        # 「点击后重扫 99/99 无法识别」），帮助线程产出的候选与帧随即被
        # 自动线程的数据覆盖 ⇒ 帮助页候选为空、按钮置灰、界面卡死。
        # 自动模式的 auto_play_func 同样反向停掉帮助线程，保证任一时刻
        # 只有一个线程操作游戏窗口。
        if self.auto_play_thread.isRunning():
            self._stop_solver_thread(self.auto_play_thread, "自动线程")
            self.pgb.setVisible(False)
            self.end_auto_play_thread.setVisible(False)
            self.set_btns_Enabled(True)

        self.label_p3.setText("")
        try:
            hwnd = minesweeper_run(self.path)
        except Exception:
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            self.set_btns_Enabled(True)
            return

        self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)
        w = self.w * self.cell_width
        h = self.h * self.cell_width

        # 等待扫雷窗口真正渲染出棋盘再启动帮助线程：自动模式 play() 是
        # 「点击开局 → sleep → 扫描」有时间等渲染；帮助模式若立刻抓屏，可能
        # 截到启动动画/空白帧（识别分数 0.27~1.00 远超阈值 0.05）⇒ 全盘
        # UNKNOWN ⇒ 每轮判定棋盘不可信。这与自动模式的等待是同一目的。
        if start_help:
            win32gui.ShowWindow(hwnd, 1)
            set_top_window(hwnd)
            time.sleep(0.6)

        # 🔴 顺序修复（用户实测「点帮助后仍卡在等待帮助计算开始」）：
        # 状态清理必须放在 **help_thread.start() 之前**。
        # 旧顺序是「先 start() → 末尾 _clear_heatmap_state("both")」，而后台线程
        # 几百毫秒内就会发出首帧真实 payload；清理晚于该帧执行，会把线程刚发的
        # 数据（以及 _last_candidates）一并抹掉，界面永久退回
        # 「等待帮助计算开始…」，且后续「点击所有确定方格」没有目标。
        # 清理的唯一目的是「新局不要残留上一局的概率色」，它属于**启动前**的
        # 复位动作，绝不能在线程已经产生数据之后再跑。
        self._clear_heatmap_state("both", reason="new_game")

        self.reset_btn_list()
        self.label_p3.lower()
        self.set_btn_list_enable(False)

        self.stackedWidget.setCurrentIndex(2)

        if start_help:
            self.set_btns_Enabled(False)
            self.help_thread.start()

        self.with_help = True

        pil_img = ImageGrab.grab(
            (
                self.bx + 0.5 * self.cell_width,
                self.by + 0.5 * self.cell_width,
                w + self.bx + 0.5 * self.cell_width,
                h + self.by + 0.5 * self.cell_width,
            )
        )
        _save_path = setting.project_path("image/save.png")
        pil_img.save(_save_path)
        self.label_p3.setPixmap(
            QtGui.QPixmap(_save_path).scaled(
                self.w * self.btn_width, self.h * self.btn_width
            )
        )
        self.label_p3.move(self.btn_width, self.btn_width)
        w = (self.w + 1) * self.btn_width
        h = (self.h + 1) * self.btn_width
        self.label_p3.resize(self.w * self.btn_width, self.h * self.btn_width)
        self.label_p3.setMinimumSize(self.w * self.btn_width, self.h * self.btn_width)
        self.frame_2.setMinimumSize(w, h)

        w, h = self.w, self.h
        cell_value = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                cell_value[j, i] = 9
        # ⚠️ 不能用 self.help_thread.complete_scan：
        # help_thread.start() 已在上面启动（可见时），此时后台线程正在跑 play/help，
        # 而 complete_scan 会写共享的 self.img（utils/vision.py:133-147）。GUI 线程
        # 再调同一实例的扫描 = 两个线程交叉写 self.img / self.bx / self.by，
        # 后台拿到的可能是"上一次点击后"的截图，导致棋盘错帧。
        # 改用独立的一次性视觉对象：只做截图+识别，不碰工作线程的任何状态。
        cell_value = self._gui_scan_once(cell_value)
        # 批 C：缓存真实棋盘 —— 帮助页候选格的悬浮预览要用真实 cell_value 渲染
        # 7×7 局部棋盘（候选详情到达时若还没有热力图 payload，就用这里这份）。
        self._last_cell_value = cell_value
        # ⚠️ 这里**不再**调用 _clear_heatmap_state：
        # 状态清理已前移到 help_thread.start() 之前。若在此处（线程启动之后）
        # 再清一次，会把后台线程刚发出的首帧 payload 与 _last_candidates 抹掉，
        # 界面永久退回「等待帮助计算开始…」并且「点击所有确定方格」失去目标
        # （用户实测「点帮助后仍卡在等待」的直接原因）。
        # 首屏只需要把 GUI 侧扫到的真实棋盘推给帮助页作为事实层。
        self.update_help_heatmap({
            "prob": {}, "best": None, "best_conf": None, "total": 0.0,
            "cell_value": cell_value, "played": 0, "win": 0,
            "phase": "ready", "clear": True,
        })

    def _gui_scan_once(self, cell_value):
        """GUI 线程的一次性棋盘扫描（不与工作线程共享任何可变状态）。

        背景：帮助流程里 GUI 需要在后台线程运行期间单独取一次棋盘用于首屏预览。
        历史实现直接调用 self.help_thread.complete_scan()，而该方法会写
        `self.img`（utils/vision.py）——与后台线程交叉写同一字段，可能让后台
        拿到错误的截图（跨线程共享可变状态缺陷）。

        这里用一个临时的视觉对象：只读配置（w/h/cell_width/坐标），独占自己的
        `img`，扫描完即弃。失败时返回全未开（9）而不是抛异常打断界面。
        """
        from utils.vision import BoardVisionMixin

        class _OneShotScanner(BoardVisionMixin):
            pass

        try:
            scanner = _OneShotScanner()
            scanner.w = self.w
            scanner.h = self.h
            scanner.cell_width = self.cell_width
            scanner.screenshot_w = int(self.cell_width * 5 / 9)
            scanner.screenshot_h = int(self.cell_width * 7 / 9)
            scanner._bx = self._bx
            scanner._by = self._by
            scanner.img = None
            scanner.images = None
            scanner._images_cell_width = None
            scanner._images_mtime = None
            scanner._locate_templates = {}
            scanner.load_img()
            return scanner.complete_scan(cell_value, False)
        except Exception as e:  # noqa: BLE001 —— 首屏预览失败不打断帮助主流程
            logger.warning("首屏预览扫描失败，使用全未开棋盘：%s", e)
            return cell_value

    def update_btn_list(self, pos_dict_list):
        """帮助页候选到达：更新候选详情 + 用**唯一渲染入口**重绘统一棋盘。

        与 ``update_help_heatmap`` 共享同一个最新 payload（``_last_heatmap_payload``）
        与同一个渲染入口（``_render_help_board``）——不存在「旧 pos_dict_list
        覆盖新热力图」的路径，因为两边都只是更新自己的那一份输入，然后整盘重算。
        """
        # 批 C：缓存真实候选列表，填充帮助页候选详情（不合成任何数值）
        self._last_candidates = list(pos_dict_list or [])
        self._fill_candidate_list(self.help_candidate_list, self._last_candidates)
        # 唯一渲染入口：事实(cell_value) + 概率(prob) + 最佳格(best) + 候选叠加
        self._render_help_board()

        self._refresh_help_click_all()
        self.return_to_main_p3.setVisible(True)

        self.set_btns_Enabled(True)
        # 状态条：帮助模式给出本轮真实候选数（不伪造进度）
        kind_counts = {}
        for pos_dict in pos_dict_list or []:
            k = classify_value_kind(pos_dict.get("exp"))
            kind_counts[k] = kind_counts.get(k, 0) + 1
        if kind_counts:
            parts = "、".join(
                f"{VALUE_KIND_TEXT.get(k, k)}×{v}" for k, v in kind_counts.items()
            )
            self.set_solver_status(f"帮助模式 · 候选 {len(pos_dict_list)} 个（{parts}）")

    def update_help_heatmap(self, payload):
        """帮助页热力图刷新（帮助线程 heatmap_signal 的槽）。

        写入「最近一次真实 payload」后交给**唯一渲染入口** ``_render_help_board``：
        与 ``update_btn_list`` 共享同一份输入与同一条渲染路径，因此
        「候选先到 / payload 后到」都收敛到同一块棋盘，不会出现两个棋盘读数打架。

        ``payload`` 为空表（新局 clear）时同样走全量重算：所有格回到
        「无数据」样式，上一帧的概率色与 best 蓝框被结构性清除（不残留）。
        """
        payload = payload or {}
        prev_cv = (self._last_heatmap_payload or {}).get("cell_value")
        self._last_heatmap_payload = payload
        cv = payload.get("cell_value")
        if cv is not None and prev_cv is not cv:
            # 盘面变了：按格缓存的世界集与转移全部作废。
            self._per_cell_transitions = {}
            self._last_cell_value = cv
        self._render_help_board()
        self._update_help_board_stats(payload)

    def _update_help_board_stats(self, payload):
        """帮助页统计栏：与自动页同一套字段语义（只用真实 payload 字段）。"""
        played = payload.get("played") or 0
        win = payload.get("win") or 0
        rate = win / played * 100 if played else 0.0
        best_conf = payload.get("best_conf")
        conf_text = (
            f"{best_conf * 100:0.2f}%" if isinstance(best_conf, (int, float))
            else "—"
        )
        new_stats = (
            f"当前格非雷的概率: {conf_text} ｜ "
            f"已完局: {played} ｜ 赢局: {win} ｜ 胜率: {rate:0.2f}%"
        )
        if self.help_board_label_stats.text() != new_stats:
            self.help_board_label_stats.setText(new_stats)

    def _preview_payload_for_board_cell(self, i, j):
        """统一帮助棋盘按坐标取当前 hover payload。

        provider 不捕获某一轮的 ``pos_dict``/``payload``，而是每次悬停时从
        ``_last_candidates`` 与 ``_last_heatmap_payload`` 读取最新数据，避免按钮
        复用后悬停仍显示上一轮“下一步预示”。没有真实候选/概率时返回 None，
        不显示伪造的估值。
        """
        for item in reversed(getattr(self, "_last_candidates", None) or []):
            pos = item.get("pos")
            try:
                if (int(pos[0]), int(pos[1])) == (int(i), int(j)):
                    return self._preview_payload_for_candidate(item)
            except (TypeError, ValueError, IndexError):
                continue
        payload = self._last_heatmap_payload or {}
        if (int(i), int(j)) not in (payload.get("prob") or {}):
            return None
        return self._preview_payload_for_heatmap_cell(int(i), int(j))

    def _wrap_help_board_in_scroller(self):
        """把帮助棋盘包进独立的横向滚动容器（幂等）。

        背景（用户实测「窗口有大小限制，UI 不能完整显示」）：棋盘宽度是
        ``(w+1)×24`` 的物理下限（30 列 = 744px），窗口缩到最小宽度时它会
        撑出**整页**水平滚动条，导致统计栏/卡片一起被拖动、右侧内容看不全。
        包一层 ``QScrollArea`` 后，只有棋盘区域横向滚动，页面其它元素保持
        自适应宽度；窗口够宽时滚动条不出现，外观与原来一致。
        """
        if getattr(self, "_help_board_scroller", None) is not None:
            return
        layout = getattr(self, "verticalLayout_help_content", None)
        if layout is None or self.frame_2 is None:
            return
        try:
            parent = self.frame_2.parentWidget()
            # 🔴 顺序至关重要（2026-11-08 原生崩溃 0xC0000005 修复）：
            # 必须先记住棋盘在原布局中的位置、**先把旧 item 从布局里摘掉**，
            # 再 setWidget 改父级。反序（先 setWidget 再 indexOf）会让
            # indexOf 返回 -1（此时 frame_2 已被重新 parented），于是走到
            # addWidget 分支 —— 原布局里那条指向 frame_2 的旧 item **永远
            # 留在布局中**，形成悬空引用；Qt 之后激活/销毁布局时访问已释放
            # 内存 ⇒ 进程级「该内存不能为 read」。
            index = layout.indexOf(self.frame_2)
            if index >= 0:
                layout.removeWidget(self.frame_2)
            scroller = QtWidgets.QScrollArea(parent)
            scroller.setObjectName("helpBoardScroller")
            scroller.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroller.setWidgetResizable(True)
            scroller.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            scroller.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroller.setStyleSheet("QScrollArea{background:transparent;border:none;}")
            scroller.setWidget(self.frame_2)
            # 滚动容器必须至少有棋盘那么高，否则会被布局压缩并把棋盘裁切
            # （实测：未设高度时容器仅 180px，而棋盘需 408px）。
            scroller.setMinimumHeight(self.frame_2.minimumSize().height())
            if index >= 0:
                layout.insertWidget(index, scroller)
            else:
                layout.addWidget(scroller)
            self._help_board_scroller = scroller
        except Exception as e:  # noqa: BLE001 —— 包裹失败不得打断界面构建
            logger.debug("帮助棋盘滚动容器创建失败（忽略）: %s", e)

    def _refresh_help_click_all(self):
        """让「点击所有确定方格」按钮与当前候选状态保持一致。

        旧实现只在 ``update_btn_list`` 里 ``setEnabled(True)``，而帮助线程走
        C++ decide 失败 / 无候选时不会再发该信号，按钮会一直停留在
        ``update_image()`` 设置的禁用态——用户看到的就是「点了完全没反应」。
        这里按「是否有可点击的确定格/雷」显式同步可用性，任何候选变化都调用。
        """
        if not hasattr(self, "click_all"):
            return
        try:
            # 与 _help_action_targets 用同一真源（逐格 prob），而不是
            # self.clicks/self.mines 缓存：win_rate 阶段 56 个确定安全格里
            # 只有 1 个进过候选表，按旧缓存判断会把按钮误禁用。
            clicks, mines = self._help_action_targets()
            self.click_all.setEnabled(bool(clicks or mines))
        except Exception as e:  # noqa: BLE001 —— UI 状态同步失败不得打断主流程
            logger.debug("同步 click_all 可用性失败（忽略）: %s", e)

    def _render_help_board(self):
        """帮助页**唯一可见棋盘**的单一渲染入口：事实/概率/best/候选一次成型。

        唯一真源（本函数是它们**唯一**的消费点）：
          - ``self._last_heatmap_payload``：最近一次真实 heatmap payload
            （prob / best / cell_value / played / win / best_conf）；
          - ``self._last_cell_value``：最近一次真实扫描棋盘（payload 无棋盘时兜底）；
          - ``self._last_candidates``：最近一次 pos_dict_list（候选叠加层）。

        ⚠️ 为什么必须「每帧全量重算」而不是增量叠加（用户实测「热力图有残留，
        不是真实值」的结构性根因）：旧实现有两套网格 + 多处 setStyleSheet，
        后到的旧数据会盖掉新数据；且上一帧的红/绿底与 best 蓝框没有清除路径
        （payload 变空、best 换格都不会触发复位）。现在每帧从**最新**输入重算
        全部 (style, text) 并整批 set：任何上一帧的样式只要不在最新 payload 里，
        必然被覆盖回默认底 —— 残留从结构上不可能存在。

        语义优先级（不可互相覆盖）：
          1. 已开数字 0-8：文字=数字（**事实**，候选叠加不得覆盖它）；
          2. 已证明是雷 10：红底（事实，优先于概率色）；
          3. 已证明安全 11：绿底（事实，优先于概率色）；
          4. 未开 9 + prob：概率色（越绿越安全）；best 另加 3px 蓝框；
          5. 未开 9 无 prob：浅灰/白半透明底（无数据）；
          6. 候选叠加（仅未开格）：num 文字 + 证明雷/证明安全/推荐框语义。

        返回本帧实际发生文字/样式变化的格子数（供回归断言「确有更新」）。
        """
        payload = self._last_heatmap_payload or {}
        cv = payload.get("cell_value")
        if cv is None:
            cv = getattr(self, "_last_cell_value", None)
        prob = payload.get("prob") or {}
        best = payload.get("best")
        best_key = None
        if best is not None:
            try:
                best_key = (int(best[0]), int(best[1]))
            except (TypeError, ValueError, IndexError):
                best_key = None
        # 候选索引：1-based (i, j) → pos_dict（后写覆盖先写，保持「最新一批」语义）
        candidates = {}
        for pos_dict in getattr(self, "_last_candidates", None) or []:
            pos = pos_dict.get("pos")
            try:
                candidates[(int(pos[0]), int(pos[1]))] = pos_dict
            except (TypeError, ValueError, IndexError):
                continue
        # clicks/mines 每帧从最新候选重建（旧实现是 append，跨轮会累积旧格子）
        self.clicks = []
        self.mines = []
        changed = 0
        self.frame_2.setUpdatesEnabled(False)
        try:
            for j in range(min(self.h, len(self.btn_list))):
                row = self.btn_list[j]
                for i in range(min(self.w, len(row))):
                    ci, cj = i + 1, j + 1
                    btn = row[i]
                    # 统一棋盘按钮始终绑定按坐标读取最新数据的 provider；
                    # 不依赖候选/概率本轮是否存在，避免 provider 生命周期断裂。
                    self._attach_preview(
                        btn,
                        lambda _button, x=ci, y=cj: (
                            self._preview_payload_for_board_cell(x, y)
                        ),
                    )
                    v = 9
                    if cv is not None:
                        try:
                            v = int(cv[cj, ci])
                        except (IndexError, TypeError, ValueError):
                            v = 9
                    style, text = self._hm_style_for_cell(
                        v, prob.get((ci, cj)), best_key == (ci, cj))
                    # 棋盘格可交互（与旧 update_image 一致：事实棋盘全格可见）
                    btn.setEnabled(True)
                    pos_dict = candidates.get((ci, cj))
                    if pos_dict is not None and v == 9:
                        # 候选格与自动页共用同一套概率色带：用 confidence 作为
                        # 「不是雷的概率」交给 _hm_style_for_cell 上色，is_best
                        # 时得到与自动页一致的 3px 蓝框。文字仍保留候选序号，
                        # 但底色/边框语义不再另起一套旧绿色样式。
                        style, text = self._hm_style_for_cell(
                            9, self._conf(pos_dict),
                            bool(pos_dict.get("is_best")),
                        )
                        text = str(pos_dict.get("num"))
                        self._bind_help_candidate_click(btn, pos_dict)
                        if pos_dict.get("is_mine") or (
                                self._conf(pos_dict) <= 0.01):
                            self.mines.append((ci, cj))
                        elif pos_dict.get("is_best") or (
                                self._conf(pos_dict) >= 0.99):
                            self.clicks.append((ci, cj))
                    elif id(btn) in self._help_click_buttons:
                        # 本轮失去候选身份：断开旧 handler，防点出上一轮的弹窗
                        self._unbind_help_candidate_click(btn)
                    if self._help_board_last.get(id(btn)) != (style, text):
                        btn.setText(text)
                        btn.setStyleSheet(style)
                        self._help_board_last[id(btn)] = (style, text)
                        changed += 1
        finally:
            self.frame_2.setUpdatesEnabled(True)
        self._refresh_help_click_all()
        return changed

    @staticmethod
    def _conf(pos_dict):
        try:
            return float(pos_dict.get("confidence"))
        except (TypeError, ValueError):
            return 0.0

    def _bind_help_candidate_click(self, btn, pos_dict):
        """把候选格的点击 → 详情弹窗 + hover 预览挂到**统一棋盘**按钮上。

        预览 payload 取 ``pos_dict`` 的候选语义 + ``_last_cell_value`` 的真实棋盘，
        与热力图 payload 同源（同一份 ``_last_cell_value``），不存在第二套数据。
        """
        try:
            i, j = pos_dict["pos"]
            string = (
                str(pos_dict.get("exp", ""))
                + f"\n({i}, {j})不是雷的概率："
                  f"{self._conf(pos_dict) * 100:0.3f}%"
            )
        except (KeyError, TypeError, ValueError):
            return
        # ⚠️ 防重复弹窗：每轮都会重跑，同一按钮若重复 connect 会点一次弹多个框
        try:
            btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass  # 无已连接 handler 时 Qt 抛 TypeError，属正常路径
        btn.clicked.connect(functools.partial(self.info, string))
        self._help_click_buttons.add(id(btn))
        # 批 A：统一棋盘候选格 hover → 7×7 预览浮层（真实 pos_dict + 真实棋盘）
        self._attach_preview(
            btn,
            lambda _button, item=pos_dict: self._preview_payload_for_candidate(item),
        )

    def _unbind_help_candidate_click(self, btn):
        """候选身份消失时断开点击 handler（幂等）。"""
        try:
            btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._help_click_buttons.discard(id(btn))

    def _clear_heatmap_state(self, which="auto", reason=""):
        """清空指定页的热力图状态并立即重绘（状态生命周期收口点）。

        调用时机（用户硬约束「清除热力图残留」）：
          - 新局 / 新一次扫描（``update_image``）：帮助页统一棋盘回到纯事实；
          - 线程停止或被终止（``Visible_signal(False)``）：两页都要清；
          - 扫描失败/盘面不可信：由上层在拿到新 payload 前主动调用。

        「清」= 把该页的最近 payload 置空 + 样式去重缓存清空，然后由**唯一渲染
        入口**整盘重绘：上一帧的概率色、best 蓝框、以及候选叠加全部结构性消失，
        只留下 ``_last_cell_value`` 的事实棋盘（数字/证明雷/安全），不残留任何
        旧 payload 的颜色。绝不在这里合成任何数值。
        """
        if which in ("auto", "both"):
            self._last_heatmap_payload = None
            self._hm_last = {}
            frame = getattr(self, "heatmap_frame", None)
            cv = self._last_cell_value
            if frame is not None:
                frame.setUpdatesEnabled(False)
            try:
                if cv is None:
                    # 无真实棋盘时逐格复位到「无数据」底（不留旧颜色）
                    for row in getattr(self, "heatmap_btn_list", []):
                        for btn in row:
                            btn.setText("")
                            btn.setStyleSheet(self._HM_BASE)
                else:
                    self._render_heatmap_grid(
                        self.heatmap_btn_list, self._hm_last, None,
                        cv, {}, None,
                    )
            finally:
                if frame is not None:
                    frame.setUpdatesEnabled(True)
            if hasattr(self, "heatmap_label_stats"):
                self.heatmap_label_stats.setText(
                    "已停止 · 无实时数据" if reason != "new_game" else "等待自动扫雷开始…"
                )
            if hasattr(self, "decision_card"):
                self.update_decision_card(
                    None, None, VALUE_KIND_UNKNOWN, source="无实时数据（已清空）")
        if which in ("help", "both"):
            self._last_heatmap_payload = None
            self._last_candidates = []
            self._help_board_last = {}
            self._help_click_buttons = set()
            self._render_help_board()
            if hasattr(self, "help_candidate_list"):
                self._fill_candidate_list(self.help_candidate_list, [])
            if hasattr(self, "help_board_label_stats"):
                self.help_board_label_stats.setText(
                    "已停止 · 无实时数据" if reason != "new_game"
                    else "等待帮助计算开始…"
                )

    # 默认样式：浅灰半透明（透出磨砂卡片，用于无需显示概率颜色的格子）+ 白字
    # （供已开格数字）。只有需要呈现概率颜色/雷标记的按钮才设为不透明。
    _HM_BASE = (
        "QPushButton{border-radius:0px;border: none;"
        'background-color:rgba(208, 208, 214, 100);font: 9pt "楷体";'
        "color: rgb(255, 255, 255)}"
    )

    def _hm_style_for_cell(self, v, p, is_best):
        """单个格子 → (style, text)。**自动页与帮助页共用**，保证配色语义一致。

        v 是 cell_value 编码（9=未开、0-8=已开数字、10=推理雷、11=推理安全）；
        p 是 payload["prob"].get((i, j))（1-based 坐标键），None 表示该格无概率。
        """
        style = self._HM_BASE
        text = ""
        if v == 9:  # 未开格：按不是雷的概率上色（自动页/帮助页同款色带）
            if p is not None:
                if p <= 0.01:
                    style = (
                        "QPushButton{border-radius:0px;border: 2px solid rgb(0, 0, 255);"
                        'background-color:rgba(255, 0, 0, 255);font: 9pt "楷体"}'
                    )
                elif p >= 0.99:
                    style = (
                        "QPushButton{border-radius:0px;border: 2px solid rgb(0, 0, 255);"
                        'background-color:rgba(0, 255, 0, 255);font: 9pt "楷体"}'
                    )
                else:
                    # 色带 0=红(必雷)→99=绿(安全)，p 是"不是雷"的概率；
                    # 色值必须 int()（oklch_grad 为 float，直接拼进 rgba() 会让
                    # Qt 样式解析失败），且拼接行若混用非 f-string，行尾 }} 不会
                    # 折叠成 } —— 曾导致整条样式解析失败（warning: parse）。
                    c = oklch_grad[int((p - 1e-5) * 100)]
                    style = (
                        f"QPushButton{{border-radius:0px;border: none;"
                        f"background-color:rgba({int(c[0])}, {int(c[1])}, {int(c[2])}, 255);"
                        'font: 9pt "楷体"}'
                    )
            else:
                style = (
                    "QPushButton{border-radius:0px;border: none;"
                    'background-color:rgba(255, 255, 255, 180);font: 9pt "楷体"}'
                )
            if is_best:
                # 最佳点击加 3px 蓝框：渐变色款替换 border: none，
                # 红/绿实色款把 2px 边框加粗为 3px（replace 链两步各命中其一）
                style = style.replace(
                    "border: none;", "border: 3px solid rgb(0, 0, 255);"
                ).replace("border: 2px", "border: 3px")
        elif v == 10:  # 推理确定的雷
            style = (
                "QPushButton{border-radius:0px;border: none;"
                'background-color:rgba(255, 0, 0, 255);font: 9pt "楷体"}'
            )
        elif v == 11:  # 推理确定的安全格（证明不是雷）
            # ⚠️ 必须与「未开且无概率」（``_HM_BASE`` 浅灰）**可区分**：旧实现把
            # 11 落到默认底，用户看到"证明安全"和"无数据"长得一模一样——两个
            # 语义互相覆盖，正是本任务要求消除的形态。这里给绿底（与 best 的
            # 绿同色系，但无蓝框，靠边框区分"证明"与"推荐点击"）。
            style = (
                "QPushButton{border-radius:0px;border: none;"
                'background-color:rgba(30, 225, 0, 255);font: 9pt "楷体"'
                ';color: rgb(20, 20, 20)}'
            )
        elif 0 <= v <= 8:  # 已开方格：浅灰半透明底 + 白字数字（不遮挡磨砂卡片）
            text = str(v)
        return style, text

    def _render_heatmap_grid(self, btn_list, cache, frame, cv, prob, best):
        """把 payload 渲染进一套网格（自动页/帮助页共用同一实现）。

        返回本帧实际发生文字或样式变化的格子数（供回归测试断言"确有更新"）。
        """
        changed = 0
        if cv is None or not btn_list:
            return changed
        if frame is not None:
            frame.setUpdatesEnabled(False)
        try:
            with _probe.span("ui.render_grid", cells=int(min(self.h, len(btn_list))
                                                       * min(self.w, len(btn_list[0])))):
                for j in range(min(self.h, len(btn_list))):
                    row = btn_list[j]
                    for i in range(min(self.w, len(row))):
                        v = int(cv[j + 1, i + 1])
                        btn = row[i]
                        style, text = self._hm_style_for_cell(
                            v, prob.get((i + 1, j + 1)), best == (i + 1, j + 1)
                        )
                        # 样式去重：只把真正变化的按钮重新 set
                        key = id(btn)
                        if cache.get(key) != (style, text):
                            btn.setText(text)
                            btn.setStyleSheet(style)
                            cache[key] = (style, text)
                            changed += 1
        finally:
            if frame is not None:
                frame.setUpdatesEnabled(True)
        if changed:
            _probe.mark("ui.style_changed", where="heatmap", n=int(changed))
        return changed

    def update_heatmap(self, payload):
        # 自动模式：概率热力图（未开格上色，已开格白字数字不遮挡）+ 单行统计栏。
        #
        # ⚠️ 坐标系（易混！）：按钮网格 heatmap_btn_list[j][i] 是 0-based；
        # payload["prob"] 的键与 best 是 1-based 全局坐标（与 solver/决策一致）。
        # 因此按钮 (j, i) ↔ 概率键 (i+1, j+1) ↔ 棋盘格 cell_value[j+1, i+1]。
        # cell_value 编码：9=未开、0-8=已开数字、10=推理确定的雷、11=推理安全格
        # （11 与"未开但无概率数据"一样落回浅灰半透明底，无需特殊渲染）。
        #
        # 概率与数字的刷新同步保证：prob/cell_value 在同一 payload 原子到达，
        # 这里一次遍历同时更新两者的文字与背景；
        # 防闪烁双保险：① 样式去重——Qt 对相同样式重复 setStyleSheet 也会触发
        # ② 渲染期间关闭整帧更新（setUpdatesEnabled），一批样式原子呈现。
        _probe.mark("ui.heatmap_frame", phase=str(payload.get("phase") or ""),
                    n_prob=len(payload.get("prob") or {}))
        cv = payload.get("cell_value")
        prob = payload.get("prob") or {}
        best = payload.get("best")
        played = payload.get("played") or 0
        win = payload.get("win") or 0
        # 中间帧（重扫/计算中）的 prob 为空只表示「本帧没带新估值」，不代表
        # 旧估值失效：沿用上一帧真实估值。只有显式 clear（新局/停止）才抹掉。
        previous = self._last_heatmap_payload or {}
        _skip_grid = False
        _explicit_clear = bool(payload.get("clear"))
        if not _explicit_clear and not prob and best is None:
            if previous.get("prob") or previous.get("best") is not None:
                prob = previous.get("prob") or {}
                best = previous.get("best")
                if payload.get("best_conf") is None:
                    payload = dict(payload)
                    payload["best_conf"] = previous.get("best_conf")
                if not payload.get("total"):
                    payload = dict(payload)
                    payload["total"] = previous.get("total", 0.0)
            elif payload.get("phase") == "computing":
                # 上一帧也没有可沿用的真实估值：跳过整盘重绘。用 prob={}
                # 重绘会把所有格子刷回灰色 = 热力图整段消失。
                _skip_grid = True
        # 批 C：缓存**生效后**的 payload 供决策卡/悬浮预览消费。
        # 必须缓存补全过的 prob/best（而不是原始空表），否则连续两个“本帧无
        # 新估值”的中间帧会：第一帧沿用、第二帧发现缓存也空 → 热力图丢失。
        if not _skip_grid:
            self._last_heatmap_payload = dict(
                payload, prob=prob, best=best,
                cell_value=cv if cv is not None else payload.get("cell_value"))
        if cv is not None:
            self._last_cell_value = cv
        else:
            # 无棋盘字段时用最近一次真实棋盘兜底；两者都没有 ⇒ 走清理入口，
            # 绝不让上一帧的概率色/best 框停屏（用户反馈的「残留」形态之一）。
            cv = self._last_cell_value
            if cv is None:
                self._clear_heatmap_state("auto")
        if not _skip_grid:
            self._render_heatmap_grid(
                self.heatmap_btn_list, self._hm_last, self.heatmap_frame,
                cv, prob, best,
            )

        rate = win / played * 100 if played else 0.0
        best_conf = payload.get("best_conf")
        conf_text = (
            f"{best_conf * 100:0.2f}%" if isinstance(best_conf, (int, float))
            else "—"
        )
        best_text = (
            f"下一步: ({int(best[0])}, {int(best[1])})"
            if best is not None and len(best) >= 2 else "下一步: —"
        )
        phase_text = {
            "computing": "状态: 正在计算下一步",
            "decision": "状态: 已生成下一步",
            "rescanned": "状态: 点击后已重扫",
            "ready": "状态: 等待下一步",
        }.get(str(payload.get("phase") or "ready"), "状态: 运行中")
        new_stats = (
            f"{phase_text} ｜ {best_text} ｜ 当前格非雷的概率: {conf_text} ｜ "
            f"已完局: {played} ｜ 赢局: {win} ｜ 胜率: {rate:0.2f}%"
        )
        if self.heatmap_label_stats.text() != new_stats:  # 文本去重，避免无谓重绘
            self.heatmap_label_stats.setText(new_stats)

        # ---- 批 B/C：状态条 + 决策卡（使用真实 payload，不合成任何数值）----
        if best:
            best_conf = payload.get("best_conf")
            # 决策卡展示的是 best_conf，其语义按**真实字段比对**判定：
            # 与 prob[best] 同值 ⇒ 安全概率；异值 ⇒ win_rate 策略胜率；
            # prob 为空 / best 不在 prob 中 ⇒ 剩余雷密度均匀先验。
            decision_kind = classify_decision_value_kind(payload)
            self.update_decision_card(
                tuple(best), best_conf, decision_kind,
                source=f"自动热力图最优格 · {VALUE_KIND_TEXT[decision_kind]}")
        else:
            self.update_decision_card(None, None, VALUE_KIND_UNKNOWN,
                                      source="本轮无最优格")
        self.set_solver_status(
            f"自动运行中 · 已完局 {played} · 赢局 {win} · 胜率 {rate:0.2f}%"
        )

        # ---- 批 A：热力图格 hover 预览（只挂未开且有真实概率的格）----
        for j in range(self.h):
            for i in range(self.w):
                if (i + 1, j + 1) not in prob:
                    continue
                btn = self.heatmap_btn_list[j][i]
                self._attach_preview(
                    btn,
                    lambda _button, x=i + 1, y=j + 1: (
                        self._preview_payload_for_heatmap_cell(x, y)
                    ),
                )

    def info(self, s):
        # 提示info
        template = re.compile(r"\((\d+),\s(\d+)\)")
        self.list_p = re.findall(template, s.split("\n")[0])
        if self.list_p:
            for i, j in self.list_p:
                i = int(i) - 1
                j = int(j) - 1
                self.btn_list[j][i].setEnabled(True)
                self.btn_list[j][i].setStyleSheet(
                    "QPushButton{border-radius:0px; border: 3px solid rgb(211, 51, 255);"
                    'background-color:rgba(0, 0, 0, 0);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
        self.info_window = MyMessageBox(
            self, message=s, x=self._x + self.x(), y=self._y + self.y()
        )
        self.info_window.signal.connect(self.set_window_xy)

    def set_window_xy(self, t):
        self._x = t[0] - self.x()
        self._y = t[1] - self.y()
        if self.list_p:
            for i, j in self.list_p:
                i = int(i) - 1
                j = int(j) - 1
                self.btn_list[j][i].setEnabled(False)

    def help_thread_warning(self, s):
        if self.is_show:
            self.warning_window = MyMessageBox(
                self, title="警告", message=s, checked=True
            )
            self.warning_window.signal.connect(self.set_is_show)

    def set_is_show(self, t):
        self.is_show = not t[2]

    def _help_action_targets(self):
        """返回当前要执行动作的 (clicks, mines)，以**1-based (x, y)** 表示。

        🔴 真源是逐格安全概率 ``prob``，不是候选表、更不是 decide.confidence：
        - ``decide.confidence`` 在 win_rate 阶段是「策略胜率」（实测 1.0），
          不是「该格 100% 不是雷」，拿它判定会把非确定格也点下去；
        - 候选表在 win_rate / pbs / limitation 分支只写了**单点** ``(x,y)``
          （只有 certain_safe 分支带整批 tied），实测 win_rate 阶段 56 个
          prob==1.0 的确定安全格只被收进 1 个 ⇒ 用户实测「进入 win rate
          阶段后，不能一次点击所有百分百不是雷的方格」。
        - ``prob`` 是 C++ 逐格安全概率：>=1 为确定安全（真值实测 0 个是雷），
          <=0 为确定雷（真值实测全部是雷）。

        仅点未开格（cell_value==9），避免重复点已开格。prob 完全缺失时
        回退到候选表（保持旧行为兼容），绝不静默什么都不做。
        """
        payload = self._last_heatmap_payload or {}
        prob = payload.get("prob") or {}
        cv = payload.get("cell_value")
        if cv is None:
            cv = getattr(self, "_last_cell_value", None)
        clicks, mines = set(), set()
        # ① 逐格安全概率：win_rate / pbs 分支的全部确定格都在这里
        #    （实测 win_rate 阶段 56 个 prob==1.0 的格，候选表只收了 1 个）。
        for key, val in prob.items():
            try:
                x, y = int(key[0]), int(key[1])
                p = float(val)
            except (TypeError, ValueError, IndexError):
                continue
            if p >= 0.999999:
                clicks.add((x, y))
            elif p <= 0.000001:
                mines.add((x, y))
        # ② 候选表：**只按 proven_safe 收集批量点击目标**（P0-1 修复）。
        #    旧逻辑以 is_best / conf>=0.99 判点击，而 probability.py 把
        #    decide 的 best 无条件塞进候选表且 is_best=True ⇒ 「点击所有
        #    确定方格」会点「最佳但不确定」的格（review2 architect P0-1）。
        #    best 仍保留 is_best 供 UI 标推荐，但不再进点击集合；
        #    没有 proven_safe 字段的旧候选 fail-closed（不点击）。
        for item in reversed(getattr(self, "_last_candidates", None) or []):
            pos = item.get("pos")
            try:
                x, y = int(pos[0]), int(pos[1])
            except (TypeError, ValueError, IndexError):
                continue
            conf = self._conf(item)
            if item.get("is_mine") or conf <= 0.01:
                mines.add((x, y))
            elif item.get("proven_safe") is True:
                clicks.add((x, y))
        # 同一格若被判成雷，绝不能再点；只处理未开格，避免重复点已开格。
        clicks -= mines
        def _is_closed(xy):
            if cv is None:
                return True
            try:
                return int(cv[xy[1], xy[0]]) == 9
            except (IndexError, TypeError, ValueError):
                return True
        return (sorted(p for p in clicks if _is_closed(p)),
                sorted(p for p in mines if _is_closed(p)))

    def click_all_func(self):
        """点击所有确定安全格 + 右键标记所有确定雷格。

        与自动模式共用同一套像素换算（``bx + x*cell_width``，1-based），
        并对每一步做可观测日志；任何异常都不再被 Qt 槽静默吞掉。
        """
        self.reload()
        hwnd = win32gui.FindWindow(None, setting.win_name)
        if hwnd <= 0:
            message = f"未找到扫雷窗口（标题：{setting.win_name}），请先启动游戏"
            logger.warning("点击所有确定方格失败：%s", message)
            self.set_solver_status(message)
            return
        try:
            win32gui.ShowWindow(hwnd, 1)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)
            set_top_window(hwnd)
            clicks, mines = self._help_action_targets()
            logger.info("点击所有确定方格：安全格 %d 个 / 雷格 %d 个",
                        len(clicks), len(mines))
            for (x, y) in clicks:
                pyautogui.click(
                    self.cell_width * x + self.bx,
                    self.cell_width * y + self.by,
                )
            if mines:
                pyautogui.keyDown("ctrl")
                try:
                    for (x, y) in mines:
                        pyautogui.rightClick(
                            self.cell_width * x + self.bx,
                            self.cell_width * y + self.by,
                        )
                finally:
                    pyautogui.keyUp("ctrl", _pause=False)
            self.clicks = []
            self.mines = []
            self.click_all.setEnabled(False)
            self.set_solver_status(
                f"已执行：点击安全格 {len(clicks)} 个、标记雷格 {len(mines)} 个")
        except pyautogui.FailSafeException:
            pyautogui.keyUp("ctrl", _pause=False)
            return
        except Exception as e:  # noqa: BLE001 —— 点击失败必须可见，不得静默
            logger.warning("点击所有确定方格失败：%s", e)
            self.set_solver_status(f"执行失败：{e}")

    def update_screen(self):
        if self.auto_play_thread.isRunning() and self.stackedWidget.currentIndex() == 1:
            self.auto_play.setText("已启动")
            self.set_btns_Enabled(False)

        elif (
                self.auto_play_thread.isFinished()
                and self.stackedWidget.currentIndex() == 1
        ):
            self.set_btns_Enabled(True)
            self.auto_play.setText("自动")
            if self.stackedWidget.currentIndex() == 1:
                self.return_to_main_page.setVisible(True)
        elif self.help_thread.isRunning() and self.stackedWidget.currentIndex() == 2:
            self.set_btns_Enabled(False)

        elif (
                self.help_thread.isFinished()
                and self.stackedWidget.currentIndex() == 2
        ):
            self.set_btns_Enabled(True)
            if self.stackedWidget.currentIndex() == 2:
                self.return_to_main_p3.setVisible(True)

    def auto_play_func(self):
        # 启动自动程序
        self.reload()
        # 模式互斥（与 update_image 对称）：先停掉帮助线程，避免两线程同时
        # 操作同一个扫雷窗口（互相点击/重扫会让彼此盘面错帧）。
        if self.help_thread.isRunning():
            self._stop_solver_thread(self.help_thread, "帮助线程")
            self.pgb_p3.setVisible(False)
            self.end_help_thread.setVisible(False)
        _hk = self.auto_play_thread.stop_hotkey_label()
        value, ok = QInputDialog.getInt(
            self, "总局数",
            "请输入要玩的总局数\n\n"
            f"运行中按 {_hk} 可随时紧急终止，\n"
            "无需用鼠标点停止按钮。\n\n"
            "请输入整数:", 1, 1, 10000, 1
        )
        if ok:
            try:
                self.reload()
                minesweeper_run(self.path)
                self.auto_play_thread.set_args(value)
                self.auto_play_thread.start()
                self.return_to_main_page.setVisible(False)
                self.set_solver_status(
                    f"自动运行中 · 按 {_hk} 可随时紧急终止（无需鼠标）")
                self.stackedWidget.setCurrentIndex(1)
            except Exception:
                QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
                self.return_to_main_page.setVisible(True)


class MyMessageBox(QMessageBox):
    signal = QtCore.pyqtSignal(tuple)

    def __init__(
            self, parent=None, title="消息", message="", x=None, y=None, checked=False
    ):
        super(MyMessageBox, self).__init__(parent)
        if x and y:
            self.move(int(x), int(y))
        self._x = x
        self._y = y
        self.setModal(True)
        # 设置窗口标题 及样式
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        # 设置提示图标
        self.setIcon(QMessageBox.Icon.Information)
        # 设置自定义提示图片
        # self.setIconPixmap(QPixmap("./imgs/dog.jpg").scaled(50, 50))

        # 设置标题
        self.setText(message)
        # 设置勾选框
        self.cb = None
        if checked:
            self.cb = QCheckBox("下次不在显示", self)
            self.setCheckBox(self.cb)
        # 详情文本
        # self.setDetailedText("详细信息")

        # 添加标准按钮
        # self.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        # yes_button = self.button(QMessageBox.StandardButton.Yes)  # 返回按钮对象

        # 自定义按钮
        confirm_btn = self.addButton("确定", QMessageBox.ButtonRole.YesRole)
        # 移除按钮
        # self.removeButton(some_btn)

        # 设置默认按钮
        self.setDefaultButton(confirm_btn)

        # 设置 ESC键 对应的按钮
        # self.setEscapeButton(cancle_btn)
        # 信号
        self.buttonClicked.connect(self.set_x_y)

        self.show()

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        self._x = self.x()
        self._y = self.y()
        param = (
            (self._x, self._y, self.cb.isChecked()) if self.cb else (self._x, self._y)
        )
        self.signal.emit(param)

    def set_x_y(self):
        self._x = self.x()
        self._y = self.y()
        param = (
            (self._x, self._y, self.cb.isChecked()) if self.cb else (self._x, self._y)
        )
        self.signal.emit(param)


class EditWindow(QDialog, Ui_Dialog):
    def __init__(self):
        super().__init__()
        self.x0 = None
        self.y0 = None
        self.x1 = None
        self.y1 = None
        self.cfg = setting.load_cfg()
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self.bx = self.cfg["bx"]
        self.by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]
        self.limit = self.cfg["limit"]

        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        self.setWindowTitle("设置")

        self.edit_w.clicked.connect(self.get_int_w)
        self.edit_h.clicked.connect(self.get_int_h)
        self.edit_a.clicked.connect(self.get_int_a)
        self.edit_bx.clicked.connect(self.get_int_bx)
        self.pushButton.clicked.connect(self.get_ms_path)
        self.edit_limit.clicked.connect(self.get_int_limit)

        self.label_w.setText(str(self.w))
        self.label_h.setText(str(self.h))
        self.label_a.setText(str(self.a))
        self.label_bx.setText(str(self.bx))
        self.label_by.setText(str(self.by))
        self.label_cell_width.setText(str(self.cell_width))
        self.label_path.setText(
            str(self.path[-30:] if len(self.path) > 30 else self.path)
        )
        self.label_limit.setText(str(self.limit))

        self.get_pos_1 = GetMousePosition()
        self.get_pos_2 = GetMousePosition()
        self.get_pos_1.pos_signal.connect(self.get_pos_1_func)
        self.get_pos_2.pos_signal.connect(self.get_pos_2_func)

        self.mouse_window = None

    def set_btns_enabled(self, enabled: bool):
        self.edit_w.setEnabled(enabled)
        self.edit_h.setEnabled(enabled)
        self.edit_a.setEnabled(enabled)
        self.pushButton.setEnabled(enabled)
        self.edit_limit.setEnabled(enabled)
        self.edit_bx.setEnabled(enabled)

    def get_int_w(self):
        value, ok = QInputDialog.getInt(self, "w", "宽度\n\n请输入整数:", self.w, 0, 10000, 2)
        if ok:
            self.cfg["w"] = value
            self.w = value
            self.label_w.setText(str(value))

    def get_int_h(self):
        value, ok = QInputDialog.getInt(self, "h", "高度\n\n请输入整数:", self.h, 0, 10000, 2)
        if ok:
            self.cfg["h"] = value
            self.h = value
            self.label_h.setText(str(value))

    def get_int_a(self):
        value, ok = QInputDialog.getInt(self, "a", "总雷数\n\n请输入整数:", self.a, 0, 10000, 2)
        if ok:
            self.cfg["a"] = value
            self.a = value
            self.label_a.setText(str(value))

    def get_int_bx(self):
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()

        self.set_btns_enabled(False)

        try:
            minesweeper_run(self.path)
            self.mouse_window = MouseWindowTread()
            self.mouse_window.start()
        except Exception as e:
            logger.error("打开扫雷失败: %s", e)
            self.set_btns_enabled(True)
            self.label_bx.setText(str(self.bx))
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            return

        self.label_bx.setText("请将鼠标移到扫雷区域的左上角，并静止。")
        self.get_pos_1.start()

    def get_pos_1_func(self, pos):
        self.x0, self.y0 = pos
        hwnd = win32gui.FindWindow(None, setting.win_name)
        self.x0, self.y0 = ScreenToClient(hwnd, self.x0, self.y0)
        hwnd = win32gui.FindWindow(None, "设置")
        self.label_bx.setText("请将鼠标移到扫雷区域的右下角，并静止。")
        self.get_pos_2.start()
        set_top_window(hwnd)

    def get_pos_2_func(self, pos):
        self.x1, self.y1 = pos
        hwnd = win32gui.FindWindow(None, setting.win_name)
        self.x1, self.y1 = ScreenToClient(hwnd, self.x1, self.y1)

        cell_width = (self.x1 - self.x0) / self.w
        self.cell_width = round(cell_width)
        if abs(self.cell_width - cell_width) >= 0.11:
            QMessageBox.about(self, "提示", "现在每个格子的宽度不是整数，请调整扫雷窗口大小。")
            self.set_btns_enabled(True)
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
            return
        self.bx, self.by = round(self.x0 - self.cell_width * 0.5), round(
            self.y0 - self.cell_width * 0.5
        )
        self.cfg["cell_width"] = self.cell_width
        self.cfg["bx"] = self.bx
        self.cfg["by"] = self.by

        self.label_bx.setText(str(self.bx))
        self.label_by.setText(str(self.by))
        self.label_cell_width.setText(str(self.cell_width))
        self.set_btns_enabled(True)
        self.mouse_window.terminate()
        del self.mouse_window
        self.mouse_window = None

    def get_int_limit(self):
        value, ok = QInputDialog.getInt(
            self,
            "limit",
            "limit\n注：但limit每增加1计算所需的时间增加1倍\n\n请输入整数:",
            self.limit,
            0,
            10000,
            2,
        )
        if ok:
            self.cfg["limit"] = value
            self.limit = value
            self.label_limit.setText(str(value))

    def get_ms_path(self):
        a = QFileDialog.getOpenFileName(self, "选择文件", os.getcwd(), "exe(*.exe)")
        if a[0] != "":
            self.cfg["path"] = a[0]
            self.path = a[0]
            self.label_path.setText(
                str(self.path[-30:] if len(self.path) > 30 else self.path)
            )

    def accept(self):
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
        # 原子保存（安全评审 P1 #5）：先校验再写 tmp + os.replace，
        # 避免直接覆盖写中途失败留下截断 JSON（下次启动将无法加载配置）。
        # 路径显式走 setting.CFG_PATH（绝对路径、与 cwd 无关），绝不拼相对 "cfg.json"。
        setting.save_cfg(self.cfg, path=setting.CFG_PATH, encoder=MyEncoder)
        QDialog.accept(self)

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
        # 原子保存（同上，安全评审 P1 #5）；路径显式走 setting.CFG_PATH
        setting.save_cfg(self.cfg, path=setting.CFG_PATH, encoder=MyEncoder)
        self.close()

    def reject(self) -> None:
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
        QDialog.reject(self)


class ScreenShot(QtWidgets.QWidget, Ui_form):
    def __init__(self, hwnd):
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        self.setWindowTitle("截图帮助")

        self.img = None
        self.cfg = setting.load_cfg()
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self.bx = self.cfg["bx"]
        self.by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]
        self.limit = self.cfg["limit"]

        h = int(self.cell_width * 7 / 9)
        w = int(self.cell_width * 5 / 9)
        self.hwndDC = win32gui.GetWindowDC(hwnd)
        self.mfcDC = win32ui.CreateDCFromHandle(self.hwndDC)
        self.saveDC = self.mfcDC.CreateCompatibleDC()
        self.saveBM = win32ui.CreateBitmap()
        self.saveBM.CreateCompatibleBitmap(self.mfcDC, w, h)
        self.saveDC.SelectObject(self.saveBM)

        self._name_list = [
            "0.bmp",
            "0_1.bmp",
            "0_2.bmp",
            "1.bmp",
            "1_1.bmp",
            "1_2.bmp",
            "2.bmp",
            "2_1.bmp",
            "3.bmp",
            "3_1.bmp",
            "4.bmp",
            "4_1.bmp",
            "5.bmp",
            "5_1.bmp",
            "6.bmp",
            "6_1.bmp",
            "7.bmp",
            "7_1.bmp",
            "8.bmp",
            "8_1.bmp",
            "9.bmp",
            "9_1.bmp",
            "9_2.bmp",
            "10.bmp",
            "10_1.bmp",
        ]
        for n in self._name_list:
            self.comboBox_name.addItem(n)
        self.re_screenshot.clicked.connect(self.screen_shot)
        self.save_img.clicked.connect(self._save_img)

    def screen_shot(self):
        try:
            i = int(self.lineEdit_i.text())
            j = int(self.lineEdit_j.text())
            x = i * self.cell_width
            y = j * self.cell_width
            h = int(self.cell_width * 7 / 9)
            w = int(self.cell_width * 5 / 9)

            hwnd = win32gui.FindWindow(None, setting.win_name)
            bx, by = ClientToScreen(hwnd, self.bx, self.by)
            pil_img = ImageGrab.grab(
                (
                    bx + 0.5 * self.cell_width,
                    by + 0.5 * self.cell_width,
                    self.w * self.cell_width + bx + 0.5 * self.cell_width,
                    self.h * self.cell_width + by + 0.5 * self.cell_width,
                )
            )

            pil_img = np.array(pil_img)
            pil_img.reshape(self.w * self.cell_width, self.h * self.cell_width, 3)
            self.img = cv2.cvtColor(pil_img, cv2.COLOR_RGB2BGR)

            self.img = self.img[
                       y - self.cell_width // 2 - h // 2: y - self.cell_width // 2 + h // 2,
                       x - self.cell_width // 2 - w // 2: x - self.cell_width // 2 + w // 2,
                       :,
                       ]
            _temp_bmp = setting.project_path("image/temp.bmp")
            cv2.imwrite(_temp_bmp, self.img)
            self.img_label.setText("")
            self.img_label.setPixmap(QPixmap(_temp_bmp).scaled(100, 140))
            self.img_label.repaint()
        except win32ui.error:
            QMessageBox.about(self, "错误", "找不到扫雷窗口哦。\n将要重新打开扫雷窗口。")
            try:
                minesweeper_run(self.path)
            except Exception as e:
                logger.error("打开扫雷失败: %s", e)
                QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
        except ValueError as e:
            QMessageBox.about(self, "错误", "横纵坐标都要为整数哦。")

    def _save_img(self):
        if self.img is not None:
            name = self._name_list[self.comboBox_name.currentIndex()]
            _out = setting.project_path(f"image/{name}")
            cv2.imwrite(_out, self.img)
            QMessageBox.about(self, "信息", f'图片一保存至"{_out}"')
            self.img = None
        else:
            QMessageBox.about(self, "错误", "请先截图才能保存哦。")


class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return str(obj, encoding="utf-8")
        if isinstance(obj, int):
            return int(obj)
        elif isinstance(obj, float):
            return float(obj)
        else:
            return super(MyEncoder, self).default(obj)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # 【端到端探针】MSW_E2E_PROBE=1 时启动内存采样并登记本次运行；
    # 未设置时 start_mem_sampler() 立即返回，零开销。
    _probe.start_mem_sampler()
    _probe.mark("app.start", cwd=os.getcwd(),
                native_available=bool(_native_mod.available),
                has_decide=bool(_native_mod.has_decide()))
    app = QApplication(sys.argv)
    ex = MyMainWindow()
    try:
        rc = app.exec_()
    finally:
        _probe.write_summary({"exit_code": locals().get("rc")})
    sys.exit(rc)
