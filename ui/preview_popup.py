# -*- coding: utf-8 -*-
"""候选格 7×7 状态转移预览浮层（Qt 桌面端）。

设计来源：review/ui-migration-explore.md 第 4 节、review/ui-design-spec.md 4.2/5.2、
review/ui-prototype.html 的 transition-overlay（:618-620 / 979-1055）。

本模块只负责"展示"：不扫描棋盘、不计算概率、不调用 solver。
真实数据由调用方（main.py）从现有 payload 取出后传入；**任何情况下都不合成
状态转移概率**——transitions 缺失时显式渲染"暂无真实状态转移分布"。

浮层形态（勘察报告 4.2 的推荐）：
- 独立顶层 QWidget，Qt.Tool | FramelessWindowHint | WindowStaysOnTopHint；
- WA_ShowWithoutActivating，hover 弹出不抢焦点（避免 enter→show→抢焦点→leave
  死循环，风险 R2）；
- Esc 通过 QApplication 级 eventFilter 捕获（浮层本身不激活，拿不到 keyPress）；
- 边缘定位：优先锚点右侧，放不下翻左侧，四边 QScreen.availableGeometry 夹取
  （对齐原型 positionTransitionOverlay:1024-1055 的语义）；
- 固定（pin）后移开鼠标不关闭；关闭按钮/Esc 无条件关闭。
"""

import weakref

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

# ---------------------------------------------------------------------------
# 纯函数层（无 Qt 依赖，可直接单测）
# ---------------------------------------------------------------------------

BOARD_SPAN = 7  # 局部棋盘边长（以目标格为中心 ±3）

# value_kind 三分类的**展示文案**（review/ui-design-spec.md:241 术语表）。
# 关键语义：heuristic_win_rate 是"策略胜率"，**不是**安全概率，文案必须自己
# 说清楚，不能靠读者从色带反推（风险 R1）。
VALUE_KIND_LABELS = {
    "exact_safety": "安全概率（精确解）",
    "uniform_prior": "均匀先验（非精确解）",
    "heuristic_win_rate": "策略胜率（启发式，非安全概率）",
}

# cell_value 编码契约（utils/vision.py:37、main.py:724-725）：
# 0-8=已开数字，9=未开，10=推理确定雷，11=推理安全格。
CELL_OPEN_MIN, CELL_OPEN_MAX = 0, 8
CELL_COVERED = 9
CELL_KNOWN_MINE = 10
CELL_KNOWN_SAFE = 11

NO_TRANSITION_TEXT = "暂无真实状态转移分布"
NO_TRANSITION_HINT = "当前 solver 不产出点击后的分支概率，此处不显示合成概率"

# 前瞻不可用时按真实原因解释，避免「暂未产出」这种模糊表述掩盖事实。
# 每条都保留「不显示合成概率」语义（既有测试依赖该文案）。
TRANSITION_REASON_TEXT = {
    "world_count_exceeded":
        "当前局面一致性世界过多，超出精确穷举上限，无法给出真实转移；此处不显示合成概率",
    "no_frontier":
        "当前盘面没有可推理的边界格，无法枚举世界；此处不显示合成概率",
    "empty_group":
        "边界约束无可行解，无法枚举世界；此处不显示合成概率",
    "no_feasible_worlds":
        "当前扫描盘面没有与数字一致的世界，无法给出真实转移；此处不显示合成概率",
    "no_best_cell":
        "本轮决策未给出候选格，无法计算该格的状态转移；此处不显示合成概率",
    "best_cell_not_closed":
        "建议格已不可点击，无法计算状态转移；此处不显示合成概率",
    "no_safe_worlds":
        "所有一致世界中点击该格均踩雷，无安全分支；此处不显示合成概率",
    "part_solve_error":
        "边界约束求解失败，无法枚举世界；此处不显示合成概率",
    "enumeration_error":
        "世界枚举失败，无法给出真实转移；此处不显示合成概率",
    "engine_error":
        "前瞻计算失败，无法给出真实转移；此处不显示合成概率",
    "lookahead_exception":
        "前瞻计算异常，无法给出真实转移；此处不显示合成概率",
}


def transition_hint_text(meta):
    """无真实转移时的说明文案：有明确原因就说原因，否则用通用占位。"""
    reason = (meta or {}).get("reason")
    if reason:
        return TRANSITION_REASON_TEXT.get(
            str(reason),
            f"真实状态转移当前不可用（{reason}）；此处不显示合成概率")
    return NO_TRANSITION_HINT


def describe_value(value, value_kind):
    """把 (值, 类型) 渲染成带类型标签的文案。

    ``value`` 为 None 时显示 "—"，绝不把缺失值显示成 0%。
    """
    label = VALUE_KIND_LABELS.get(value_kind, "未标注类型的估值")
    if value is None:
        return f"{label}：—"
    try:
        pct = f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return f"{label}：—"
    return f"{label} {pct}"


def sample_board(cell_value, cell, span=BOARD_SPAN):
    """取以 ``cell``（1-based (i, j)）为中心的 span×span 局部棋盘。

    返回 rows[j][i] 的编码列表；越界（棋盘外）位置为 None。
    ``cell_value`` 为 None 或形状不可用时返回 None（浮层显示占位）。
    """
    if cell_value is None:
        return None
    try:
        nrows = len(cell_value)
        ncols = len(cell_value[0]) if nrows else 0
    except (TypeError, IndexError):
        return None
    if nrows == 0 or ncols == 0:
        return None
    try:
        ci, cj = int(cell[0]), int(cell[1])
    except (TypeError, ValueError, IndexError):
        return None
    half = span // 2
    rows = []
    for dj in range(-half, half + 1):
        row = []
        for di in range(-half, half + 1):
            i, j = ci + di, cj + dj
            if 1 <= i < ncols and 1 <= j < nrows:
                try:
                    row.append(int(cell_value[j][i]))
                except (TypeError, ValueError, IndexError):
                    row.append(None)
            else:
                row.append(None)
        rows.append(row)
    return rows


def transitions_text(transitions):
    """把真实状态转移分布渲染成 [(分支名, 概率文案)] 列表。

    ``transitions`` 为空/None 时返回空列表 —— 由调用方渲染 NO_TRANSITION_TEXT，
    **不在此处编造任何概率**。
    """
    rows = []
    for item in transitions or []:
        if not isinstance(item, dict):
            continue
        name = item.get("label") or item.get("type") or item.get("kind") or "分支"
        prob = item.get("probability")
        if prob is None:
            continue
        try:
            rows.append((str(name), f"{float(prob) * 100:.2f}%"))
        except (TypeError, ValueError):
            continue
    return rows


def transition_boards(transitions, max_boards=6):
    """提取各可达分支的 7×7 局面（供浮层画多个小棋盘）。

    每项 = {"probability": float, "pct": "43.00%", "board": 7×7 编码列表}。
    只有 C++ 真实产出的 ``board`` 才会被采用；缺失的分支跳过（绝不合成局面）。
    按概率降序，最多 ``max_boards`` 个，避免浮层无限膨胀。
    """
    out = []
    for item in transitions or []:
        if not isinstance(item, dict):
            continue
        board = item.get("board")
        prob = item.get("probability")
        if not board or prob is None:
            continue
        try:
            p = float(prob)
            b = [[int(v) for v in row] for row in board]
        except (TypeError, ValueError):
            continue
        if not b or len(b) != BOARD_SPAN or any(
                len(row) != BOARD_SPAN for row in b):
            continue
        out.append({"probability": p, "pct": f"{p * 100:.2f}%", "board": b})
    out.sort(key=lambda x: -x["probability"])
    return out[:max_boards]


def summarize_payload(payload):
    """把调用方 payload 归一化成浮层要展示的各段文本/数据。

    这是浮层与 main.py 之间**唯一的数据契约**，便于不依赖 Qt 直接断言。
    返回 dict：
      cell / title / value_text / source_text / transition_text /
      transition_rows / transition_boards / has_transitions / board / board_ok
    """
    payload = payload or {}
    cell = payload.get("cell")
    try:
        cell = (int(cell[0]), int(cell[1])) if cell else None
    except (TypeError, ValueError, IndexError):
        cell = None
    title = (
        f"点击 ({cell[0]}, {cell[1]}) 之后" if cell else "点击候选格之后"
    )
    value_text = describe_value(payload.get("value"), payload.get("value_kind"))
    source = payload.get("source")
    source_text = f"来源：{source}" if source else "来源：未标注"
    board = sample_board(payload.get("cell_value"), cell) if cell else None
    rows = transitions_text(payload.get("transitions"))
    meta = payload.get("transition_meta") or {}
    # 概率口径必须显式标注，避免用户把「条件概率」读成「无条件概率」。
    basis_text = ""
    if rows:
        try:
            p_safe = float(meta.get("p_click_safe"))
            worlds = int(meta.get("world_count"))
            basis_text = (
                f"口径：以「点击该格不踩雷」为前提的条件概率"
                f"（该格安全概率 {p_safe * 100:.2f}%，已枚举 {worlds} 个一致世界）"
                if meta.get("basis") == "exact_worlds"
                else f"口径：条件概率（来源 {meta.get('basis')}）"
            )
        except (TypeError, ValueError):
            basis_text = "口径：条件概率（以「点击该格不踩雷」为前提）"
    return {
        "cell": cell,
        "title": title,
        "value_text": value_text,
        "source_text": source_text,
        "transition_rows": rows,
        "transition_boards": transition_boards(payload.get("transitions")),
        "has_transitions": bool(rows),
        "transition_text": "" if rows else NO_TRANSITION_TEXT,
        "transition_basis_text": basis_text,
        "transition_hint_text": transition_hint_text(meta),
        "board": board,
        "board_ok": bool(board),
    }


def compute_popup_position(anchor_rect, popup_size, screen_rect, margin=12):
    """边缘定位：优先右侧、放不下翻左侧、上下/左右夹取到屏幕内。

    语义对齐 review/ui-prototype.html:1024-1055。所有入参/出参都是
    (x, y, w, h) 元组，便于无 Qt 断言（不需要构造 QRect）。
    """
    ax, ay, aw, ah = anchor_rect
    pw, ph = popup_size
    sx, sy, sw, sh = screen_rect

    space_right = (sx + sw) - (ax + aw) - margin
    if space_right >= pw:
        left = ax + aw + margin
    else:
        left = ax - pw - margin
        if left < sx + margin:
            left = ax + aw + margin  # 两侧都放不下：退回右侧再夹取
    max_left = sx + sw - pw - margin
    left = max(sx + margin, min(left, max_left))

    top = ay
    space_below = (sy + sh) - (ay + ah) - margin
    if space_below < ph and ay - sy > ph + margin:
        top = ay - ph
    max_top = sy + sh - ph - margin
    top = max(sy + margin, min(top, max_top))
    return left, top


# ---------------------------------------------------------------------------
# Qt 控件层
# ---------------------------------------------------------------------------

_POPUP_QSS = (
    "QWidget#previewPopup{background-color:rgba(44, 44, 44, 242);"
    "border: 1px solid rgba(255, 255, 255, 90);border-radius: 10px;}"
    "QLabel{color: rgb(238, 241, 244);font: 10pt \"Microsoft YaHei\";}"
    "QLabel#popupTitle{font: 600 11pt \"Microsoft YaHei\";color: rgb(255, 255, 255);}"
    "QLabel#popupValue{font: 600 13pt \"Cascadia Mono\";color: rgb(151, 232, 201);}"
    "QLabel#popupSource,QLabel#popupNote{color: rgb(176, 186, 195);"
    "font: 9pt \"Microsoft YaHei\";}"
    "QPushButton{border: 1px solid rgba(255, 255, 255, 80);border-radius: 6px;"
    "background-color:rgba(255, 255, 255, 28);color: rgb(238, 241, 244);"
    "font: 9pt \"Microsoft YaHei\";padding: 3px 10px;}"
    "QPushButton:hover{background-color:rgba(255, 255, 255, 60);}"
    "QPushButton:checked{background-color:rgba(0, 109, 119, 200);}"
)

# 局部棋盘小格样式（只做形态编码，不复用主热力图色带：局部棋盘不是概率）
# 数字按经典扫雷色阶着色，用户反馈「界面不容易看清楚」，纯灰字辨识度不足。
# 局部棋盘格子尺寸：主棋盘与分支棋盘共用。此前 16px 挤在一起看不清，
# 用户裁决「一行一个」后放大到 22px 并给每行独立边框。
MINI_CELL_PX = 22

_MINI_BASE = (
    "background-color:rgba(208, 208, 214, 70);border:none;"
    "color: rgb(238, 241, 244);font: bold 10pt \"Cascadia Mono\";"
)
_MINI_OPEN_BASE = (
    "background-color:rgba(228, 233, 237, 210);border:none;"
    "font: bold 11pt \"Cascadia Mono\";"
)
_MINI_OPEN_COLORS = {
    0: "rgb(38, 52, 61)",     # 0=空白格（不印数字），中性色
    1: "rgb(25, 95, 225)",    # 蓝
    2: "rgb(20, 130, 55)",    # 绿
    3: "rgb(205, 45, 40)",    # 红
    4: "rgb(90, 30, 165)",    # 深紫
    5: "rgb(150, 40, 30)",    # 暗红
    6: "rgb(10, 135, 140)",   # 青
    7: "rgb(35, 40, 45)",     # 近黑
    8: "rgb(110, 115, 120)",  # 灰
}
_MINI_MINE = (
    "background-color:rgba(180, 35, 24, 220);border:none;"
    "color: rgb(255, 255, 255);font: bold 10pt \"Cascadia Mono\";"
)
_MINI_SAFE = (
    "background-color:rgba(213, 240, 228, 220);border:none;"
    "color: rgb(12, 81, 56);font: bold 10pt \"Cascadia Mono\";"
)
# 棋盘外（C++ 分支 board 用 -1 编码）：给一个比棋盘底色更暗、带细边的
# 可见样式，而不是全透明。此前全透明让分支棋盘靠边的几行"消失"，
# 用户真机截图里 7×7 只显示下半截、上半截空白即由此而来。
_MINI_OUTSIDE = (
    "background-color:rgba(20, 22, 24, 160);"
    "border:1px solid rgba(255,255,255,18);"
)
_MINI_CENTER = "border: 2px solid rgb(0, 109, 119);"


def mini_open_style(code):
    """返回已开数字格的样式：经典扫雷色阶 + 浅底，保证数字清晰可辨。"""
    color = _MINI_OPEN_COLORS.get(code, "rgb(38, 52, 61)")
    return _MINI_OPEN_BASE + "color: " + color + ";"


def mini_cell_style(code):
    """把 cell_value 编码映射成 (文字, 样式)，供主/分支棋盘共用。"""
    if code is None or code == -1:
        return "", _MINI_OUTSIDE
    if code == CELL_COVERED:
        return "", _MINI_BASE
    if code == CELL_KNOWN_MINE:
        return "雷", _MINI_MINE
    if code == CELL_KNOWN_SAFE:
        return "✓", _MINI_SAFE
    if CELL_OPEN_MIN <= code <= CELL_OPEN_MAX:
        # 经典扫雷：数字 0 是空白格，不印 "0"
        return ("" if code == 0 else str(code)), mini_open_style(code)
    return "", _MINI_BASE


# 浮层「延迟关闭」窗口（毫秒）：鼠标从源按钮移到浮层需要跨过 12px 间隙，
# 期间会先收到源按钮的 Leave。若 Leave 立即 hide，用户永远点不到浮层的
# 「固定」按钮——这正是此前"hover 实际不生效"的观感来源之一。
HIDE_DELAY_MS = 220


class PreviewPopup(QtWidgets.QWidget):
    """候选格 7×7 预览浮层（复用实例，禁止每次 hover 重建）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPopup")
        # Qt.Tool：不抢任务栏、不进 Alt-Tab；Frameless：无标题栏；置顶
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_POPUP_QSS)

        self._pinned = False
        self._current = None
        self._app_filter_installed = False
        self._mini_labels = []
        # 延迟关闭：源按钮 Leave 只起定时器，鼠标进入浮层即取消。
        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._delayed_hide)
        self._build_ui()
        self.resize(self.sizeHint())

    # ---- 构建 ---------------------------------------------------------
    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        self.title_label = QtWidgets.QLabel("点击候选格之后", self)
        self.title_label.setObjectName("popupTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.pin_button = QtWidgets.QPushButton("固定", self)
        self.pin_button.setObjectName("popupPin")
        self.pin_button.setCheckable(True)
        self.pin_button.setFocusPolicy(Qt.NoFocus)
        self.pin_button.clicked.connect(self.toggle_pin)
        header.addWidget(self.pin_button)
        self.close_button = QtWidgets.QPushButton("关闭", self)
        self.close_button.setObjectName("popupClose")
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.close_popup)
        header.addWidget(self.close_button)
        outer.addLayout(header)

        self.value_label = QtWidgets.QLabel("", self)
        self.value_label.setObjectName("popupValue")
        self.value_label.setWordWrap(True)
        outer.addWidget(self.value_label)

        self.source_label = QtWidgets.QLabel("", self)
        self.source_label.setObjectName("popupSource")
        outer.addWidget(self.source_label)

        board_wrap = QtWidgets.QHBoxLayout()
        board_wrap.addStretch(1)
        self.board_frame = QtWidgets.QFrame(self)
        self.board_grid = QtWidgets.QGridLayout(self.board_frame)
        self.board_grid.setContentsMargins(0, 0, 0, 0)
        self.board_grid.setSpacing(1)
        for r in range(BOARD_SPAN):
            for c in range(BOARD_SPAN):
                lab = QtWidgets.QLabel("", self.board_frame)
                lab.setFixedSize(MINI_CELL_PX, MINI_CELL_PX)
                lab.setAlignment(Qt.AlignCenter)
                lab.setStyleSheet(_MINI_BASE)
                self.board_grid.addWidget(lab, r, c)
                self._mini_labels.append(lab)
        board_wrap.addWidget(self.board_frame)
        board_wrap.addStretch(1)
        outer.addLayout(board_wrap)

        # 状态转移：多个 7×7 可达局面棋盘，每个上方标真实条件概率。
        # 分支数不定（实测最多 9 个），用横向滚动容器承载，避免浮层撑爆屏幕。
        self.transition_label = QtWidgets.QLabel("", self)
        self.transition_label.setObjectName("popupTransitions")
        self.transition_label.setWordWrap(True)
        outer.addWidget(self.transition_label)

        self.branches_scroll = QtWidgets.QScrollArea(self)
        self.branches_scroll.setObjectName("popupBranches")
        self.branches_scroll.setWidgetResizable(True)
        # 一行一个分支：不需要横向滚动，只需纵向滚动（分支多时）。
        self.branches_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff)
        self.branches_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded)
        self.branches_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # 显式给 viewport 也设透明：QScrollArea 默认 viewport 是白底，
        # 在深色浮层里会露出一大块白色矩形（用户实测视觉割裂）。
        self.branches_scroll.setStyleSheet(
            "QScrollArea#popupBranches{background:transparent;border:none;}"
            "QScrollArea#popupBranches > QWidget > QWidget{"
            "background:transparent;}")
        self.branches_scroll.viewport().setStyleSheet(
            "background:transparent;")
        self.branches_host = QtWidgets.QWidget(self)
        self.branches_host.setObjectName("popupBranchesHost")
        self.branches_host.setStyleSheet(
            "QWidget#popupBranchesHost{background:transparent;}")
        self.branches_layout = QtWidgets.QVBoxLayout(self.branches_host)
        self.branches_layout.setContentsMargins(0, 0, 0, 0)
        self.branches_layout.setSpacing(6)
        self.branches_scroll.setWidget(self.branches_host)
        # 单行高度 = 概率标签 + 7×22px 棋盘 + 边框 ≈ 172px；
        # 可视区给 2 行，其余纵向滚动。
        # 宽度 = 概率列 104（防裁切）+ 棋盘 7×22 + 行内边距/间距。
        row_h = MINI_CELL_PX * BOARD_SPAN + 18
        self.branches_scroll.setMinimumWidth(
            104 + MINI_CELL_PX * BOARD_SPAN + 36)
        self.branches_scroll.setMinimumHeight(min(2 * (row_h + 6), 360))
        outer.addWidget(self.branches_scroll)
        self.branches_scroll.hide()

        self.note_label = QtWidgets.QLabel(NO_TRANSITION_HINT, self)
        self.note_label.setObjectName("popupNote")
        self.note_label.setWordWrap(True)
        outer.addWidget(self.note_label)

    # ---- 数据 ---------------------------------------------------------
    def set_payload(self, payload):
        """渲染一份 payload（见 summarize_payload 的契约）。返回摘要 dict。"""
        info = summarize_payload(payload)
        self._current = dict(payload or {})
        self.title_label.setText(info["title"])
        self.value_label.setText(info["value_text"])
        self.source_label.setText(info["source_text"])
        self._render_board(info["board"], info["cell"])
        branches = info["transition_boards"]
        if branches:
            # 每个可达局面画一个 7×7 小棋盘，概率标在棋盘上方。
            self.transition_label.setText(
                f"点击后可能出现的情况 · {len(branches)} 个可达局面（条件概率）")
            self._render_transition_boards(branches)
            # 必须显式标注概率口径（条件概率 vs 无条件），否则用户会误读。
            self.note_label.setText(
                info.get("transition_basis_text") or "分支概率由 solver 真实产出")
        elif info["has_transitions"]:
            # 有真实转移但 C++ 未带局面（旧 pyd）：退化成文字列表，不伪造棋盘。
            lines = [f"{name}：{pct}" for name, pct in info["transition_rows"]]
            self.transition_label.setText("状态转移分支\n" + "\n".join(lines))
            self._render_transition_boards([])
            self.note_label.setText(
                info.get("transition_basis_text") or "分支概率由 solver 真实产出")
        else:
            self.transition_label.setText(NO_TRANSITION_TEXT)
            self._render_transition_boards([])
            self.note_label.setText(info.get("transition_hint_text")
                                    or NO_TRANSITION_HINT)
        # 分支行最小宽 = 概率列 104 + 7×22px 棋盘 + 行内边距/间距 +
        # 浮层左右边距；sizeHint() 在分支区隐藏时算不出真实宽度，
        # 不显式给下限会把最右一列棋盘裁掉（真机截图可见）。
        if branches:
            # 104 概率 + 10 间距 + 7×22 棋盘 + 14 行内边距/边框 +
            # 24 浮层边距 + 16 纵向滚动条 ≈ 322，取 344 留余量。
            want_w = 104 + 10 + MINI_CELL_PX * BOARD_SPAN + 14 + 24 + 16 + 8
            self.setMinimumWidth(want_w)
            self.adjustSize()
            # adjustSize() 依 sizeHint() 收缩，scroll 刚 show 时布局尚未刷新，
            # 会算回窄宽度并裁掉最右列。显式把宽度撑到下限。
            if self.width() < want_w:
                self.resize(want_w, self.height())
        else:
            self.adjustSize()
        return info

    def _render_board(self, board, cell):
        for idx, lab in enumerate(self._mini_labels):
            r, c = divmod(idx, BOARD_SPAN)
            if not board or r >= len(board) or c >= len(board[r]):
                lab.setText("")
                lab.setStyleSheet(_MINI_OUTSIDE)
                continue
            text, style = mini_cell_style(board[r][c])
            center = bool(cell) and (r == BOARD_SPAN // 2 and c == BOARD_SPAN // 2)
            if center:
                style = style + _MINI_CENTER
            lab.setText(text)
            lab.setStyleSheet(style)

    @staticmethod
    def _make_mini_board(board, highlight_center=True):
        """构造一个 7×7 QGridLayout 棋盘控件（分支局面用）。

        格子尺寸用 MINI_CELL_PX：与主棋盘保持一致，避免两套尺寸观感割裂。
        """
        frame = QtWidgets.QFrame()
        frame.setObjectName("miniBoard")
        frame.setStyleSheet(
            "QFrame#miniBoard{background-color:rgba(255,255,255,12);"
            "border:1px solid rgba(255,255,255,45);border-radius:4px;}")
        grid = QtWidgets.QGridLayout(frame)
        grid.setContentsMargins(3, 3, 3, 3)
        grid.setSpacing(1)
        for r in range(BOARD_SPAN):
            for c in range(BOARD_SPAN):
                lab = QtWidgets.QLabel("", frame)
                lab.setFixedSize(MINI_CELL_PX, MINI_CELL_PX)
                lab.setAlignment(Qt.AlignCenter)
                text, style = mini_cell_style(board[r][c])
                if highlight_center and r == BOARD_SPAN // 2 and c == BOARD_SPAN // 2:
                    style = style + _MINI_CENTER
                lab.setText(text)
                lab.setStyleSheet(style)
                grid.addWidget(lab, r, c)
        return frame

    def _render_transition_boards(self, branches):
        """清空并重建分支棋盘区：**一行一个局面**，概率在左、7×7 棋盘在右。

        用户实测「7×7 风格全都挤在一起了看不清楚」，裁决「一行一个（纵向
        列表）」。此前横向把多个棋盘压进固定宽度的滚动区，格子只有 16px；
        现在每个分支独占一行、格子 22px、每行独立边框，分支多时纵向滚动。
        """
        while self.branches_layout.count():
            item = self.branches_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        if not branches:
            self.branches_scroll.hide()
            return
        for idx, br in enumerate(branches, 1):
            row = QtWidgets.QWidget(self.branches_host)
            row.setObjectName("popupBranchRow")
            row.setAttribute(Qt.WA_StyledBackground, True)
            row.setStyleSheet(
                "QWidget#popupBranchRow{background-color:rgba(255,255,255,18);"
                "border:1px solid rgba(255,255,255,55);border-radius:6px;}")
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(6, 4, 6, 4)
            hl.setSpacing(10)
            # 概率标签必须够宽：真机截图里 `100.00%` 被裁成 `.00.00%`
            # （76px + 行左边距放不下 11pt 等宽字体的 7 字符 + #序号）。
            # 加宽到 108px、序号与概率分行、并允许横向扩展避免再次裁切。
            pct = QtWidgets.QLabel(f"#{idx}\n{br['pct']}", row)
            pct.setObjectName("popupBranchPct")
            pct.setAlignment(Qt.AlignCenter)
            pct.setMinimumWidth(104)
            pct.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                              QtWidgets.QSizePolicy.Preferred)
            pct.setStyleSheet(
                "QLabel#popupBranchPct{color:rgb(151,232,201);"
                "font:600 11pt \"Cascadia Mono\";padding:0 2px;}")
            hl.addWidget(pct)
            board_widget = self._make_mini_board(br["board"])
            # 棋盘按自身 7×22px 尺寸固定，不给 stretch：真机截图里最右
            # 一列被裁，是行内弹性区把宽度抢走、棋盘被 QHBoxLayout 压缩。
            board_widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                                       QtWidgets.QSizePolicy.Fixed)
            hl.addWidget(board_widget)
            hl.addStretch(1)
            self.branches_layout.addWidget(row)
        # 不加 addStretch：setWidgetResizable(True) 下多余，且会让
        # branches_layout.count() 比真实分支数多 1（既有断言依赖精确数量）。
        self.branches_scroll.show()

    # ---- 交互 ---------------------------------------------------------
    @property
    def is_pinned(self):
        return self._pinned

    def toggle_pin(self, checked=None):
        self._pinned = bool(self.pin_button.isChecked() if checked is None else checked)
        self.pin_button.setChecked(self._pinned)
        self.pin_button.setText("已固定" if self._pinned else "固定")

    def show_for_cell(self, cell, payload=None, anchor_widget=None,
                      anchor_point=None, screen=None):
        """在锚点旁显示浮层。``payload`` 为 None 时沿用上一次 payload，仅换格。"""
        data = dict(payload or self._current or {})
        data["cell"] = tuple(cell)
        info = self.set_payload(data)

        anchor = self._anchor_rect(anchor_widget, anchor_point)
        screen_rect = self._screen_rect(screen, anchor)
        popup_size = (self.width(), self.height())
        left, top = compute_popup_position(anchor, popup_size, screen_rect)
        self.move(int(left), int(top))
        self.show()
        self._install_app_filter()
        return info

    def hide_if_unpinned(self):
        """移开鼠标：未固定时关闭。"""
        if not self._pinned:
            self.close_popup()

    # ---- 延迟关闭（源按钮 Leave → 浮层 Enter 之间的手速窗口）----------
    def schedule_hide(self):
        """源按钮 Leave 时调用：起 220ms 定时器，不立即隐藏。

        鼠标从按钮移到浮层必然先经过按钮 Leave；若当场隐藏，浮层上的
        「固定/关闭」按钮永远点不到。定时器在鼠标进入浮层时被取消。
        """
        if self._pinned:
            return
        self._hide_timer.start()

    def cancel_scheduled_hide(self):
        """鼠标进入浮层/重新悬停源按钮时调用：取消待执行的隐藏。"""
        if self._hide_timer.isActive():
            self._hide_timer.stop()

    def _delayed_hide(self):
        if not self._pinned:
            self.close_popup()

    def close_popup(self):
        self._hide_timer.stop()
        self._remove_app_filter()
        self.hide()

    # ---- 浮层自身的 Enter/Leave（鼠标走进浮层不得被判成"离开源按钮"）----
    def enterEvent(self, event):  # noqa: N802 —— Qt 覆写
        self.cancel_scheduled_hide()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 —— Qt 覆写
        self.schedule_hide()
        super().leaveEvent(event)

    # ---- 定位辅助 -----------------------------------------------------
    def _anchor_rect(self, anchor_widget, anchor_point):
        if anchor_widget is not None:
            try:
                pos = anchor_widget.mapToGlobal(QtCore.QPoint(0, 0))
                return (pos.x(), pos.y(), anchor_widget.width(),
                        anchor_widget.height())
            except (AttributeError, RuntimeError):
                pass
        if anchor_point is not None:
            try:
                return (int(anchor_point[0]), int(anchor_point[1]), 0, 0)
            except (TypeError, ValueError, IndexError):
                pass
        cursor = QtGui.QCursor.pos()
        return (cursor.x(), cursor.y(), 0, 0)

    def _screen_rect(self, screen, anchor):
        try:
            scr = screen or QtWidgets.QApplication.screenAt(
                QtCore.QPoint(anchor[0], anchor[1])
            ) or QtWidgets.QApplication.primaryScreen()
            geo = scr.availableGeometry()
            return (geo.x(), geo.y(), geo.width(), geo.height())
        except (AttributeError, RuntimeError):
            # offscreen / 无屏幕信息：给一个足够大的虚拟视口，避免夹取成 0
            return (0, 0, 1280, 800)

    # ---- Esc（浮层不激活，必须走应用级 eventFilter）--------------------
    def _install_app_filter(self):
        if self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _remove_app_filter(self):
        if not self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._app_filter_installed = False

    def eventFilter(self, obj, event):
        if (self._app_filter_installed and self.isVisible()
                and event.type() == QtCore.QEvent.KeyPress
                and event.key() == Qt.Key_Escape):
            self.close_popup()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._remove_app_filter()
        super().closeEvent(event)


class PreviewHoverFilter(QtCore.QObject):
    """把按钮/控件的 hover 事件接到 PreviewPopup（eventFilter 天然幂等）。

    ``payload_provider(obj)`` 返回该控件对应的 payload；返回 falsy 时不起浮层
    （例如该格没有真实估值，宁可不显示也不显示占位概率）。

    ⚠️ 与 ``PreviewHoverButton`` 的分工：本过滤器挂在**任意 QWidget** 上，
    走 eventFilter 收 Enter/Leave；``PreviewHoverButton`` 则直接覆写按钮自身的
    enterEvent/leaveEvent/focusInEvent/focusOutEvent。后者在真实窗口系统里
    更可靠（eventFilter 依赖 WA_Hover 且受父级遮挡影响），前者保留给
    非按钮控件与既有测试。
    """

    def __init__(self, popup, payload_provider, parent=None):
        super().__init__(parent)
        self.popup = popup
        self.payload_provider = payload_provider

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype in (QtCore.QEvent.Enter, QtCore.QEvent.HoverEnter):
            self.popup.cancel_scheduled_hide()
            payload = None
            if callable(self.payload_provider):
                try:
                    payload = self.payload_provider(obj)
                except Exception:  # noqa: BLE001 —— hover 取数失败不得打断主界面
                    payload = None
            if payload:
                cell = payload.get("cell")
                if cell:
                    self.popup.show_for_cell(cell, payload=payload,
                                             anchor_widget=obj)
        elif etype in (QtCore.QEvent.Leave, QtCore.QEvent.HoverLeave):
            # 过滤器通道保持「离开即关」的既有契约（非按钮控件/单测依赖）；
            # 真实按钮走 PreviewHoverButton 的延迟关闭。
            self.popup.hide_if_unpinned()
        return False


def attach_hover_preview(button, popup, payload_provider):
    """给 ``button`` 安装 hover 预览过滤器（同时开 WA_Hover 以收 HoverEnter）。

    返回过滤器对象；调用方需持有引用（挂在 ``button._preview_filter``），
    否则 Python GC 会回收过滤器导致 hover 静默失效。
    """
    button.setAttribute(Qt.WA_Hover, True)
    filt = PreviewHoverFilter(popup, payload_provider, parent=button)
    button.installEventFilter(filt)
    button._preview_filter = filt
    return filt


_GLOBAL_PREVIEW_HOVER_FILTER = None


class _GlobalPreviewHoverFilter(QtCore.QObject):
    """应用级鼠标移动兜底，补齐密集棋盘格缺失 Enter/HoverEnter 的平台差异。"""

    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.MouseMove:
            return False
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        widget = app.widgetAt(QtGui.QCursor.pos())
        while widget is not None:
            if isinstance(widget, PreviewHoverButton):
                widget._show_preview()
                break
            widget = widget.parentWidget()
        return False


class PreviewHoverButton(QtWidgets.QPushButton):
    """把 Enter/Leave/Focus 事件**真实**接到 PreviewPopup 的按钮。

    为什么不用 eventFilter：``attach_hover_preview`` 依赖 ``WA_Hover`` 的
    HoverEnter，而 Qt 只在**鼠标追踪**开启且控件未被遮挡时才派发 Hover 事件；
    棋盘按钮是 24×24 密集网格、且有 ``label_p3`` 等兄弟控件叠加，实测 hover
    经常收不到。这里直接覆写 QWidget 的事件虚函数——Enter/Leave 是窗口系统
    直接投递的，不依赖属性开关。

    ``payload_provider`` 语义与 ``attach_hover_preview`` 一致：
    返回 falsy 则不起浮层（该格没有真实估值）。
    """

    def __init__(self, popup=None, payload_provider=None, text="", parent=None):
        super().__init__(text, parent)
        self._popup = popup
        self._payload_provider = payload_provider

    # ---- 接线（运行期可换 popup/provider，不必重建按钮）----------------
    def bind_preview(self, popup, payload_provider):
        self._popup = popup
        self._payload_provider = payload_provider
        # 密集棋盘格必须主动接收鼠标移动；仅依赖 WA_Hover 在真实窗口
        # （尤其截图 QLabel/相邻 24px 子控件覆盖时）可能收不到 HoverEnter。
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            global _GLOBAL_PREVIEW_HOVER_FILTER
            if _GLOBAL_PREVIEW_HOVER_FILTER is None:
                _GLOBAL_PREVIEW_HOVER_FILTER = _GlobalPreviewHoverFilter()
                app.installEventFilter(_GLOBAL_PREVIEW_HOVER_FILTER)

    def _payload(self):
        if not callable(self._payload_provider):
            return None
        try:
            return self._payload_provider(self)
        except Exception:  # noqa: BLE001 —— 取数失败绝不打断主界面
            return None

    def _show_preview(self):
        popup = self._popup
        if popup is None:
            return
        popup.cancel_scheduled_hide()
        payload = self._payload()
        if not payload:
            return
        cell = payload.get("cell")
        if not cell:
            return
        popup.show_for_cell(cell, payload=payload, anchor_widget=self)

    # ---- Qt 事件覆写 ---------------------------------------------------
    def enterEvent(self, event):  # noqa: N802 —— Qt 覆写
        self._show_preview()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 —— Qt 覆写
        # 真实物理鼠标在密集网格中可能只有 Move 没有 Enter/HoverEnter；
        # 只要鼠标仍在按钮矩形内，就重复走同一幂等展示路径。
        self._show_preview()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 —— Qt 覆写
        popup = self._popup
        if popup is not None:
            popup.schedule_hide()
        super().leaveEvent(event)

    def focusInEvent(self, event):  # noqa: N802 —— Qt 覆写（纯键盘通道）
        self._show_preview()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 —— Qt 覆写
        popup = self._popup
        if popup is not None:
            popup.schedule_hide()
        super().focusOutEvent(event)
