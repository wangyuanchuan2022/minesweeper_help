# -*- coding: utf-8 -*-
"""概率枚举与胜率计算：number5_1 主入口、部分枚举(part_solve)、
试解评估(try_solve)、大局面近似(process_bigger_situation)与
期望胜率搜索(win_rate)。

自 utils/util.py 拆分而来。``ProbabilityMixin`` 不单独使用，
由 ``utils.solver.Solver`` 混入，依赖实例属性：
``w``/``h``/``a``/``p``/``limit``/``is_play``/``num``/``count``/``memory``/
``checked``/``pos_dict_list``/``appended_pos``/``bx``/``by``/``cell_width``，
以及 ``cell_around``/``get_set_1``/``complete_scan``/``mine_clear1``/
``mine_clear3_1``/``rescan_after_click``/``_throttled_pv_signal_emit``
与各 Qt 信号。

⚠️ 本模块**不做任何逐点击的失败弹窗探测**（用户裁决 2026-11-07）：局末棋盘
出现雷/失败弹窗时扫描不准属于正常节奏，不应打断自动循环——失败判定统一
由 ``utils/solver.py`` 主循环的胜负弹窗检测接管。
"""
import hashlib
import json
import math
import os
import time
from collections import defaultdict

import numpy as np
import pyautogui
import cv2 as cv

from logger import get_logger
from .combinatorics import A, C_num, combination_ratio, get_list
from . import native
from . import decide_trace
from . import e2e_probe as probe
from .vision import LowConfidenceScanError

logger = get_logger(__name__)


def _decide_diag(r):
    """把 C++ decide 返回的 dict 摘要成可写入日志的诊断串。

    背景（真机 2026-11-08 17:54 连续 6 次「decide 返回无效坐标」）：旧日志
    只写「返回无效坐标」，不含 reason / world_upper / prob 规模，导致无法
    区分「盘面识别错」「世界枚举不可行」还是「C++ 内部放弃」——排查卡死。
    本函数只做**只读摘要**，不改变任何决策语义；r 为 None/非 dict 时安全返回。
    """
    if not isinstance(r, dict):
        return f"decide 返回 {type(r).__name__}（非 dict）"
    fields = []
    for k in ("reason", "x", "y", "world_upper", "path", "confidence"):
        if k in r:
            fields.append(f"{k}={r.get(k)}")
    prob = r.get("prob")
    if hasattr(prob, "__len__"):
        fields.append(f"prob格数={len(prob)}")
    tied = r.get("tied")
    if hasattr(tied, "__len__"):
        fields.append(f"tied格数={len(tied)}")
    return ", ".join(fields) if fields else "decide 返回空 dict"


def _help_transition_meta(r):
    """把 C++ decide 返回的转移元数据规整成 UI 契约。

    帮助模式的精确前瞻若失败，C++ 会通过 ``transition_reason`` 给出真实原因
    （如世界数超限）；这里把它并进 meta，供浮层显示准确文案而不是笼统的
    「暂无」。成功时 meta 本身已带 basis/p_click_safe/world_count。
    """
    meta = dict(r.get("transition_meta") or {})
    reason = r.get("transition_reason")
    if reason and not (r.get("transitions")):
        meta["reason"] = str(reason)
    return meta or None


# ---------------------------------------------------------------------------
# 盘面逻辑一致性校验（用户指令 2026-11-08：C++ decide 之前必须先验证盘面）
#
# 背景：视觉扫描偶发错误（DPI/主题/遮挡/半透明叠加）会产出**逻辑上不可能**的
# 盘面——例如某个「1」的邻域已标 2 个雷、或总雷数超过全部未开格数。用这种
# 盘面求解只会得到错误的概率与点击建议，甚至让 C++ 侧抛异常/静默降级。
# 因此在进入 decide 之前先做多项式时间（O(格数)）的可行性校验：
#   ① 逐格邻域可满足性：已标雷数 ≤ 数字 ≤ 已标雷数 + 未开格数
#   ② 总雷数可行性：已标雷数 ≤ a，且 a - 已标雷数 ≤ 全部未开格数
# 只检查「不可能满足」的硬矛盾，不检查跨格全局一致性（那需要求解，交给
# 枚举阶段；枚举无解时由调用方按"决策不可用"处理）。
# 返回 (ok, problems)：problems 是 [(x, y, 说明)]，最多保留 8 条供弹窗展示。
# ---------------------------------------------------------------------------
MAX_REPORTED_BOARD_PROBLEMS = 8


def validate_board_consistency(cell_value, a, w, h):
    """校验扫描出的盘面是否存在逻辑矛盾。

    参数（与 decide 同口径）：
      cell_value —— (h+2, w+2) int32；1-based 内部区域，
                    0-8=已开数字、9=未开、10=已标记雷、11=推理安全
      a / w / h —— 总雷数 / 宽 / 高

    返回 (ok, problems)：
      ok=True  —— 未发现硬矛盾，可以进入 decide
      ok=False —— problems 非空，列出具体格子与原因
    """
    problems = []
    try:
        cv = np.asarray(cell_value)
    except Exception:  # noqa: BLE001 —— 非数组输入视为不可校验
        return False, [(0, 0, f"cell_value 不是数组：{type(cell_value)}")]
    if cv.ndim != 2:
        return False, [(0, 0, f"cell_value 维度错误：{cv.ndim}")]
    if cv.shape[0] < h + 2 or cv.shape[1] < w + 2:
        return False, [(0, 0,
                        f"cell_value 形状 {cv.shape} 小于期望 {(h+2, w+2)}")]
    if a < 0:
        return False, [(0, 0, f"总雷数 a={a} 为负")]

    num10 = 0
    num9 = 0
    # ① 逐格邻域可满足性
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            v = int(cv[y, x])
            if v == 10:
                num10 += 1
                continue
            if v == 9:
                num9 += 1
                continue
            if v == 11:
                # 11 = 推理确定安全格：既不是已标雷，也不参与邻域雷数，
                # 但它是**不可布雷**的格（否则与"已证明安全"矛盾）。
                # 对邻域统计而言等价于已开格（不计入 closed），不报错。
                continue
            if not (0 <= v <= 8):
                if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
                    problems.append(
                        (x, y, f"非法编码 {v}（应为 0-8/9/10/11）"))
                continue
            # 统计 8 邻域
            flagged = 0
            closed = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (1 <= nx <= w and 1 <= ny <= h):
                        continue
                    nv = int(cv[ny, nx])
                    if nv == 10:
                        flagged += 1
                    elif nv == 9:
                        # 只有真正「未开且未判定」的格才可能布雷。
                        # 11=推理确定安全，不可布雷，不计入 closed。
                        closed += 1
            # 已标雷数已超过数字 ⇒ 不可能
            if flagged > v:
                if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
                    problems.append(
                        (x, y, f"数字 {v} 但邻域已标 {flagged} 个雷（超额）"))
                continue
            # 未开格不够凑满剩余雷数 ⇒ 不可能
            if v - flagged > closed:
                if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
                    problems.append(
                        (x, y,
                         f"数字 {v} 需 {v - flagged} 个雷，但邻域只剩 "
                         f"{closed} 个未开格"))

    # ② 总雷数可行性
    if num10 > a:
        if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
            problems.append((0, 0, f"已标雷 {num10} 个 > 总雷数 {a}"))
    else:
        remaining = a - num10
        if remaining > num9:
            if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
                problems.append(
                    (0, 0,
                     f"剩余雷 {remaining} 个 > 未开格 {num9} 个"))
        elif remaining > 0 and num9 > 0:
            # ③ 未开格「可布雷性」（2026-11-08 实测补丁，含误报复盘）
            # 必要判据：一个未开格 c **不能**布雷 ⟺ 存在某个数字邻居 v，
            # 其剩余需求 (v - 已标雷数) 已为 0（c 再布雷会让 v 超额）。
            # 反之：
            #   · c 不在任何数字的 8 邻域内 ⇒ 可自由布雷（不受约束）
            #   · c 的数字邻居都还有需求 ⇒ 可能布雷
            # 可布雷格总数 < 剩余雷数 ⇒ 全局矛盾。
            #
            # ⚠️ 首版实现写成「只统计与仍有需求的数字相邻的未开格」，实测
            # 240 个真实盘面误报 40 个（全在开局）：开局时绝大多数未开格远离
            # 已开数字区、可自由布雷，却被漏计。以下为修正后的 O(n) 判据。
            #
            # 为什么必须加这条（真实故障根因）：抓图退化（黑屏/遮挡）时识别器
            # 会把大片格子认成「数字 0」（实测黑图 → 142 个 0 + 338 个未开），
            # 每个 0 的邻域自洽成立、单格校验全过；但所有相邻未开格都被那些
            # 「已满足的 0」排除布雷资格，99 个雷无处可放。只查单格会漏掉这类
            # 整盘矛盾，让错误盘面进入 decide（真机 17:54 连续 6 次失败）。
            blocked = None  # 惰性创建的位图：True=该未开格被锁死为安全
            for y in range(1, h + 1):
                for x in range(1, w + 1):
                    v = int(cv[y, x])
                    if not (0 <= v <= 8):
                        continue
                    flagged = 0
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            nx, ny = x + dx, y + dy
                            if not (1 <= nx <= w and 1 <= ny <= h):
                                continue
                            if int(cv[ny, nx]) == 10:
                                flagged += 1
                    if v - flagged != 0:
                        continue        # 该数字仍需雷，不能锁死邻格
                    if blocked is None:
                        blocked = [[False] * (w + 2) for _ in range(h + 2)]
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            nx, ny = x + dx, y + dy
                            if 1 <= nx <= w and 1 <= ny <= h \
                                    and int(cv[ny, nx]) == 9:
                                blocked[ny][nx] = True
            # 可布雷格 = 未开格中未被锁死的
            placeable = 0
            for y in range(1, h + 1):
                for x in range(1, w + 1):
                    if int(cv[y, x]) != 9:
                        continue
                    if blocked is not None and blocked[y][x]:
                        continue
                    placeable += 1
            if placeable < remaining:
                if len(problems) < MAX_REPORTED_BOARD_PROBLEMS:
                    problems.append(
                        (0, 0,
                         f"剩余雷 {remaining} 个，但只有 {placeable} 个未开格"
                         f"可能布雷（其余被已满足的数字约束排除）"
                         f"：盘面识别很可能有误"))
    return (not problems), problems


def format_board_problems(problems):
    """把校验问题列表格式化成单行/多行文案（供弹窗与日志）。"""
    if not problems:
        return ""
    lines = []
    for (x, y, msg) in problems[:MAX_REPORTED_BOARD_PROBLEMS]:
        lines.append(f"({x}, {y}) {msg}" if x or y else msg)
    return "；".join(lines)


# ---------------------------------------------------------------------------
# 决策耗时预算控制：把 number5_1 单次决策的总计算时间控制在 10 秒以内。
#
# part_solve 按组大小呈指数增长，用指数模型
#   t(L) ≈ base_ms · k^(L - 33)（k 由历史实测样本自动拟合，见 _ps_refit_k）
# 预测各组耗时，并按实测在线校准（指数平滑）；模型状态挂在 solver 实例
# （属性 _time_budget）上跨决策记忆，并经 _tb_save 落盘 state/time_budget.json
# 跨进程持久。仅 native.tuned()（C++ 可用且未设
# MSW_NATIVE_TUNE=0）时启用，纯 Python 回退自动恢复原始静态行为。
# ---------------------------------------------------------------------------
DECISION_TIME_BUDGET = 30.0  # number5_1 单次决策总预算（秒，含视觉扫描）——用户确认的上限
# 帮助模式预算乘数（用户指令 2026-11-06 与 2026-11-07「可以稍微把预算放高一点」）。
# 由 utils/solver.py help() 设为该值并在结束时恢复；自动模式恒 1.0。
# 取值 3.0 ⇒ 帮助模式单次决策 30s × 3 = 90s（自动模式仍为 30s 不变）。
# 注意：模块级变量，帮助/自动线程并发时共享——当前 UI 两者不同页互斥使用，
# 且 C++ decide 主路径不读预算（只影响 Python 回退链），泄漏后果仅是预算放宽。
# 帮助模式 C++ decide 放宽参数（用户指令：「帮助人类的话可以适当提高推理的
# 预算」「可以稍微把预算放高一点」）。
#   wr_threshold: 自动基线 = 6 + LIMIT_BONUS_WIN_RATE(2) = 8；帮助 = 基线 + 10 = 18
#                 （比自动多 10 ⇒ 更多局面进自适应前瞻层，深度随 limitation 自适应）
#   node_cap:     自动 = 200 万；帮助 ×3 = 600 万（配合 90s 预算给前瞻更大内存余量）
# 这两个值只影响 C++ 决策质量，不改任何正确性语义。
HELP_NODE_CAP = 6_000_000
# 帮助模式预算乘数（用户指令 2026-11-06 与 2026-11-07「可以稍微把预算放高一点」）。
# 由 utils/solver.py help() 设为该值并在结束时恢复；自动模式恒 1.0。
# 取值 3.0 ⇒ 帮助模式单次决策 30s × 3 = 90s（自动模式仍为 30s 不变）。
# 注意：模块级变量，帮助/自动线程并发时共享——当前 UI 两者不同页互斥使用，
# 且 C++ decide 主路径不读预算（只影响 Python 回退链），泄漏后果仅是预算放宽。
_DECISION_BUDGET_MULT = 1.0
HELP_BUDGET_MULT = 3.0
# C++ decide 连续失败计数（成功后清零；ERROR 日志可见性用，见 _number5_1_core）
_DECIDE_FAIL_SEQ = [0]
# 「C++ decide 决策不可用」的统一文案（用户批准方案 A：失败必须可见、绝不静默
# 回退到重复 Python 决策链）。{where} 由调用点填空，指明是哪一类失败。
DECIDE_UNAVAILABLE_MSG = (
    "C++ decide 决策不可用（{where}）：按「不回退重复 Python 决策链」策略处理，"
    "本轮改为安全重扫（不点击任何格子）。请检查 settings 与 C++ 加速模块是否可用。"
)


def decision_budget():
    """当前生效的决策预算秒数（帮助模式 ×2）。"""
    return DECISION_TIME_BUDGET * _DECISION_BUDGET_MULT
_PS_REF_SIZE = 33            # 模型参考组大小（实测标定点：C++ 2.3ms / Python 976ms）
_PS_SAFETY = 2.0             # 预测安全系数（组形状差异可达 10x + 模型误差）
# win_rate 阈值调节（用户规则：步长 0.5，无上限，下限 0）——起点 6+2=8，

# ---------------------------------------------------------------------------
# win_rate 节点数硬上限的历史说明（**该机制已于 2026-11-09 随 win_rate 删除**）
#
# 保留本章注释只为追溯历史标定口径，勿据此恢复逻辑。
#
# 原机制：win_rate 的内存有两项 ——① 世界列表 nworlds × 2304B；
# ② memo 缓存 live_nodes × ~200B（memo 只写不删，随候选数×分叉×深度组合增长）。
# 实测节点开销完全主导（1e5 节点时 memo ≈19MB、世界列表 <0.15MB），
# 因此原始防爆手段是给**节点数**封顶。
#
# 原标定（真实 RSS 峰值采样，样本来自已删的 calibrate_node_cap.py）：
#     cap=2e3 → ΔRSS 0.00MB / 1e4 → 0.10MB / 5e4 → 0.73MB / 2e5 → 4.04MB
#     ⇒ 每节点中位 15.2B、最大 21.2B；取 25B/节点留余量 ⇒ 2e6 × 25B ≈ 48MB，
#       对照 1.5GB 决策层硬上限留 8x 余量 ⇒ 2e6 安全。
#
# 现状态（2026-11-09）：C++ 决策链已无 py_win_rate / wr_f，节点预算改由
# ExactEngine 的 node_cap 与时间闸在 C++ 内部自理（见 cpp/mscore.cpp）。
# 本文件下方 _wr_node_cap / _WrNodeCapExceeded / _WR_NODECAP_TOTAL 仅为
# 历史遗留符号：部分 bench 与测试仍导入它们，硬删会连锁破坏，
# 故保留定义但**不再有生产消费路径**。
# ---------------------------------------------------------------------------
def _ps_default_base_ms():
    """模型初值：33 格组在当前模式下的典型耗时（毫秒）。

    C++ 取实测（2.3ms）的约 4 倍作保守初值（10ms），首次遭遇大组时宁可少算一点
    也不突破预算；随后由实测逐次校准收敛到真实值。Python 侧仅在 native 部分回退
    场景下由观测自校正，初值影响不大。
    """
    return 10.0 if native.available else 1500.0


# 生产侧 win_rate 节点超限降级的**模块级累计计数**（供 bench 统计读取）。
# 每个 Solver 实例有自己的 _wr_nodecap_hits，跨局会随实例丢弃；
# 这里用模块级单元素列表保留跨局累计，便于 bench 扫描 node_cap 时核对
# "到底触发了几次"。bench 每局开头通过 ab_policy.reset_gate_stats() 清零。
def _ps_base_ms(state):
    """读取模型基准耗时；模式切换（C++↔纯 Python）时重置。

    ⚠️ base 是"按增长模型折算回 33 格参考点的等效耗时"，纯数学抽象：
    小组（size≪33）观测经 k^-(size-33) 折算会把 base 推到百万毫秒级，
    这是正常现象——predict 用同一因子逆向展开后预测值仍然准确。
    切勿把 base 当成"33 格组的真实耗时"去校验或手动改小；预测的正确性
    由 predict/observe 使用同一增长因子保证（历史上两者因子不一致
    ——predict 1.2 / observe 2.0——曾导致预测爆炸成几十秒，见 _ps_k）。
    """
    base = state.get("ps_base_ms")
    if base is None or state.get("ps_native") is not native.available:
        base = _ps_default_base_ms()
        state["ps_native"] = native.available
    return base


_PS_K_DEFAULT = 1.1                # 增长因子缺省（实测标定 2026-09-03：size≥30 对中位数≈1.08；运行中由自动拟合覆盖）
_PS_K_MIN, _PS_K_MAX = 1.05, 2.0   # 拟合值夹限：防小样本噪声外推
_PS_FIT_MIN_SIZE = 30              # 拟合只用大组样本（小组耗时被固定开销/形状噪声主导，不含指数信息）
# 拟合窗口：只用最近 N 条样本。k 反映当前机器/程序的实时状态，久远样本既拖慢收敛
# 又让 O(n²) 拟合随对局数恶化（实测 1777 条全量 ~23ms、3000 条 ~1.5s，而每局触发数十次）。
_PS_FIT_WINDOW = 200
# 样本文件保留上限（滑窗）：文件只增不减会让 _ps_record_ms 的整文件重写与启动读取
# 随对局数无限变慢（实测 1777 条已 155KB）。留 1200 条 ≈ 足够拟合 + 留历史余量。
_PS_SAMPLES_KEEP = 1200
_PS_SAMPLES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data_time.json",
)


def _ps_k(state):
    """当前增长因子：自动拟合值优先（state["ps_k"]），无则用缺省。"""
    k = state.get("ps_k")
    return float(k) if k else _PS_K_DEFAULT


def _ps_predict_ms(state, size):
    """预测 size 格组的 part_solve 耗时（毫秒）。

    max(size-33, -20) 的下限把极小组的折算倍率钳在 k^-20（约 1/10），
    防止指数下溢到 0；_ps_observe 的折算用同样的截断，两侧对称抵消。
    """
    return _ps_base_ms(state) * (_ps_k(state) ** max(size - _PS_REF_SIZE, -20)) * _PS_SAFETY


def _ps_record_ms(size, ms):
    """把一次实测 (size, ms) 样本追加到 data_time.json（标准 JSON 数组）。

    供自动拟合增长因子用（_ps_refit_k）；过滤明显非真实的观测（非有限值/
    亚毫秒构造值）。写失败仅记日志，绝不影响决策主流程。
    成功返回最新样本列表（供拟合复用），被过滤或失败返回 None。

    ⚠️ 保留"标准 JSON 数组 + 原子替换"的格式（测试与既有数据都依赖它），
    但**只在样本数超过保留上限时才重写整个文件**；常规追加走紧凑路径：
    读尾部追加后一次性写回。相比历史上的"无条件全量重写 155KB"，
    这里限制文件不超过 _PS_SAMPLES_KEEP 条（滑窗），保证写成本有上界。
    """
    try:
        # ms≥0.5 的下限：剔除单测构造值（0.001ms）与物理上不可能的观测
        if not (math.isfinite(ms) and ms >= 0.5 and int(size) >= 1):
            return None
        try:
            with open(_PS_SAMPLES_PATH, "r", encoding="utf-8") as f:
                _rows = json.load(f)
            if not isinstance(_rows, list):
                _rows = []
        except (OSError, ValueError):
            _rows = []
        _rows.append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "size": int(size),
            "ms": round(float(ms), 3),
            "native": bool(native.available),
        })
        # 滑窗：文件只保留最近 N 条，防止无限增长拖慢每次重写
        if len(_rows) > _PS_SAMPLES_KEEP:
            _rows = _rows[-_PS_SAMPLES_KEEP:]
        _tmp = _PS_SAMPLES_PATH + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_rows, f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _PS_SAMPLES_PATH)
        return _rows
    except (OSError, ValueError) as e:
        logger.debug("实测耗时记录失败（忽略）: %s", e)
        return None


def _ps_observe(state, size, ms):
    """按实测耗时更新模型（base 指数平滑；k 用全部历史样本自动重拟合）。"""
    _rows = _ps_record_ms(size, ms)
    k = _ps_k(state)
    implied = ms / (k ** max(size - _PS_REF_SIZE, -20))
    state["ps_base_ms"] = 0.5 * _ps_base_ms(state) + 0.5 * implied
    if _rows is not None:
        _ps_refit_k(state, _rows)
    _tb_save(state)


def _ps_read_samples():
    """读取历史实测样本（data_time.json，JSON 数组）；缺失/损坏返回空表。"""
    try:
        with open(_PS_SAMPLES_PATH, "r", encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _ps_fit_k(rows, native_flag):
    """由历史样本拟合增长因子 k（t(L)=base·k^(L-33)，base 不可观故用样本对消去）。

    log k = (log t2 − log t1) / (L2 − L1)，对所有 |ΔL|≥3 的样本对取中位数
    （稳健抗形状噪声：同 size 组形状差异可达 10x）。仅用与当前模式一致的样本
    （native/纯 Python 耗时差 3 个量级，不可混），且优先取
    size≥_PS_FIT_MIN_SIZE 的大组样本对（小组不含指数信息）；有效样本对
    不足 3 返回 None。
    """
    def _pairs(min_size):
        pts = []
        for r in rows:
            try:
                if (r.get("native") is native_flag and r.get("ms") and r.get("size")
                        and int(r["size"]) >= min_size):
                    pts.append((int(r["size"]), float(r["ms"])))
            except (AttributeError, TypeError, ValueError):
                continue
        ks = []
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d_l = pts[j][0] - pts[i][0]
                if abs(d_l) >= 3:
                    ks.append(math.log(pts[j][1] / pts[i][1]) / d_l)
        return ks

    ks = _pairs(_PS_FIT_MIN_SIZE)   # 大组样本对比值才含指数信息
    if len(ks) < 3:
        ks = _pairs(1)              # 大组不足 3 对 → 回退全量（聊胜于无）
    if len(ks) < 3:
        return None
    ks.sort()
    k = math.exp(ks[len(ks) // 2])
    return min(max(k, _PS_K_MIN), _PS_K_MAX)


def _ps_refit_k(state, rows=None):
    """用全部历史样本重新拟合 k 写入 state["ps_k"]（样本不足保持原值）。

    只取最近 200 条（rows[-200:]）：k 反映当前机器/程序的实时状态，
    久远样本反而拖慢收敛；调用时机为程序启动与每次观测落盘后。
    """
    if rows is None:
        rows = _ps_read_samples()
    # 只取最近 _PS_FIT_WINDOW 条（与文档承诺一致）。
    # 早期实现传的是 rows[:]（全量），导致拟合随样本数呈 O(n²) 增长：
    # 1777 条样本时全量拟合 ~23ms/数万条时可达 1.5s（实测），
    # 而每局会触发数十次拟合——这是"越玩越慢"的真正来源。
    window = rows[-_PS_FIT_WINDOW:]
    k = _ps_fit_k(window, native.available)
    if k is not None:
        state["ps_k"] = k
    logger.debug(f"k:{k}")


# ---------------------------------------------------------------------------
# 预算模型状态落盘：ps_base_ms / win_rate 阈值等跨进程持久（state/time_budget.json）
# ---------------------------------------------------------------------------
_TB_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "time_budget.json",
)


def _tb_load():
    """启动时恢复模型状态（base 校准值 / wr 阈值）；文件缺失或损坏返回空。"""
    try:
        with open(_TB_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tb_save(state):
    """模型状态变化后原子落盘；失败仅记日志，不影响决策主流程。"""
    try:
        os.makedirs(os.path.dirname(_TB_STATE_PATH), exist_ok=True)
        _tmp = _TB_STATE_PATH + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(_tmp, _TB_STATE_PATH)
    except (OSError, ValueError) as e:
        logger.debug("模型状态落盘失败（忽略）: %s", e)







class ProbabilityMixin:
    # ---- 决策级单调进度坐标（仅 native.tuned() 的 number5_1 期间生效）----
    # 所有进度发射统一经 _pv_mapped_emit：操作内 0-100 先按当前阶段区间 [lo,hi]
    # 线性映射到决策级坐标，再过单调门（低于已显示值的丢弃）——进度条绝不回退。
    # 阶段区间：预扫描 [0,10) → 枚举（按各组预测耗时加权）[10,80) → 决策 [80,100]。
    # _pv_state=None（回退模式 / 决策之外 / 直接调用热点方法）时为原样直通。

    def _pv_mapped_emit(self, value):
        """进度发射的单调映射门（见类注释；无状态时保持原始语义）。"""
        st = getattr(self, "_pv_state", None)
        if st is None:
            self._throttled_pv_signal_emit(value)
            return
        mapped = st["lo"] + (st["hi"] - st["lo"]) * (value / 100.0)
        val = int(mapped)
        if val < st["hwm"]:
            return  # 单调门：低于已显示进度 → 丢弃（进度条不回退）
        st["hwm"] = val
        self._throttled_pv_signal_emit(val)

    def _estimated_progress_heartbeat(self, predicted_s=None):
        """速度估计进度心跳：真实进度静默时按预测时长推进进度条（受单调门约束）。

        仅当 pv_signal 超过 0.3s 没有真实发射时，按 elapsed/predicted 发射决策级
        估计值（封顶 94%，收尾 100 由 number5_1 入口的 finally 强制发射）。
        估计值必须高于当前单调高水位（hwm）才发射——不会造成进度条回退。

        predicted_s 缺省取决策时间预算（速度估计的量纲来源）。
        返回 threading.Event，调用方结束后 set() 停止。
        """
        import threading

        stop = threading.Event()
        if predicted_s is None:
            predicted_s = decision_budget()
        t0 = time.perf_counter()
        slf = self

        def _loop():
            while not stop.wait(0.2):
                try:
                    st = getattr(slf, "_pv_state", None)
                    if st is None:
                        continue
                    # 真实进度静默判定（心跳自己的发射也会刷新时间戳，故实际节奏约 0.5s）
                    if time.time() - getattr(slf, "_last_pv_signal_time", 0.0) > 0.3:
                        est = int(min((time.perf_counter() - t0) / max(predicted_s, 0.1),
                                      0.94) * 100)
                        if est > st["hwm"]:
                            st["hwm"] = est
                            slf._throttled_pv_signal_emit(est)
                except Exception:
                    break  # 心跳失败不影响计算

        th = threading.Thread(target=_loop, daemon=True, name="est-progress")
        th.start()
        return stop

    def number5_1(self, cell_value):
        """5.1 数字统计（入口：决策级单调进度 + 速度估计心跳，主体见 _number5_1_core）。"""
        hb = None
        if native.tuned():
            # 预扫描阶段先占 [0,10)；后续阶段在核心流程中调整区间
            self._pv_state = {"lo": 0.0, "hi": 10.0, "hwm": 0}
            hb = self._estimated_progress_heartbeat()
        try:
            return self._number5_1_core(cell_value)
        finally:
            if hb is not None:
                hb.set()
                self._pv_state = None
                self._throttled_pv_signal_emit(100)  # 决策收尾（特殊值直通节流器）

    def _decide_unavailable(self, cell_value, exc=None, where="", detail=None):
        """C++ decide 决策不可用的统一出口（自动/帮助共用；**绝不回退 Python 链**）。

        用户批准方案 A 的硬约束：decide 失败 / 不可用 / 返回无效坐标时，行为必须
        「可观测错误 + 全盘安全重扫 + 警告 + 显式返回」，不得静默回退到已被删除的
        重复 Python 决策链（`utils/probability.py:706-709` 记录的「点一格两下/两个
        弹窗」历史根因即该类静默回退）。

        ``detail``（2026-11-08 新增诊断字段）：C++ 返回 dict 里的 reason /
        world_upper / prob 规模等摘要。此前只记「返回无效坐标」，无法区分
        是「盘面识别错」「世界枚举不可行」还是「C++ 内部放弃」——真机排查
        （17:54 连续 6 次失败）卡在这里没有可判读数。

        语义固定为六项：
          ① ERROR 级日志，文案含「不回退」（`DECIDE_UNAVAILABLE_MSG`），有异常时
             带 `exc_info`；
          ② 递增模块级 `_DECIDE_FAIL_SEQ[0]`（连续失败计数，成功后由调用点清零）；
          ③ 发 `warning_signal`（主窗口已接线到弹窗，见 `main.py:392-394`）；
          ④ 重建全 9 棋盘后 `self.complete_scan(...)` 全盘安全重扫；
          ⑤ `self.count += 1`（与空盘早退重扫 `:552` 同语义：本轮未产生决策，
             主循环 `solver.py:283` 的 `count >= 3` 因此仍能识别「设置不正确」）；
          ⑥ **返回重扫后的 `cell_value`**，调用方必须 `return` 它，把控制流交回
             `play()` / `help()` 主循环下一轮重新决策。

        本 helper 不点击、不写 `_last_heatmap` 新值——不在可疑盘面上产生动作。
        """
        msg = DECIDE_UNAVAILABLE_MSG.format(where=where or "unknown")
        _DECIDE_FAIL_SEQ[0] += 1
        # 【端到端探针】决策失败是可观测的功能事件：落盘以便分析器统计
        # 「decide 失败率 / 失败类型」，而不是只靠翻日志。
        probe.error("decide_unavailable", where or "unknown",
                    seq=_DECIDE_FAIL_SEQ[0],
                    exc=type(exc).__name__ if exc is not None else None)
        # 诊断：把 C++ 返回的关键字段一并记录，否则「无效坐标」无任何可判读数。
        _diag = ""
        if detail:
            try:
                _diag = (" ｜ decide 返回: "
                         + ", ".join(f"{k}={detail[k]}" for k in detail))
            except Exception:  # noqa: BLE001 —— 诊断拼装失败不影响主流程
                _diag = f" ｜ decide 返回: {detail!r}"
        logger.error("%s（第 %d 次连续）%s", msg, _DECIDE_FAIL_SEQ[0], _diag,
                     exc_info=exc is not None)
        # 弹窗去重（方案 A 裁决 4 的防风暴约束）：只在**一次连续失败的首发**
        # 弹一次 warning；后续同一串失败只留 ERROR 日志。否则帮助模式的
        # `help()` 5 轮 number5_1 重试会连弹 5 次（`utils/solver.py:532-546`），
        # 与「一次失败一次提示」的用户体验冲突。成功路径会把计数清零
        # （`:DECIDE_FAIL_SEQ[0] = 0`），下一串失败重新弹一次。
        if _DECIDE_FAIL_SEQ[0] == 1:
            try:
                self.warning_signal.emit(msg)
            except Exception as e:  # noqa: BLE001 —— 发射失败不得掩盖决策失败
                logger.debug("warning_signal 发射失败（忽略）: %s", e)
        h = cell_value.shape[0] - 2
        w = cell_value.shape[1] - 2
        fresh = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                fresh[j, i] = 9
        try:
            fresh = self.complete_scan(fresh)
        except LowConfidenceScanError as e:
            # 安全重扫同样可能因局末失败弹窗而不可信。此时**不能**让异常继续
            # 上抛（会绕过本次 helper 的失败语义、甚至直接掀掉自动循环线程）：
            # 保留重建的全 9 棋盘并原样返回，交回主循环去做胜负弹窗判定。
            logger.warning("决策不可用后的安全重扫不确定，返回未开棋盘交回主循环：%s", e)
        self.count += 1
        return fresh

    def _click_decision_cell(self, cell):
        """按统一 1-based (x,y) 契约点击一个 C++ 决策格。"""
        x, y = int(cell[0]), int(cell[1])
        if x < 1 or y < 1:
            raise ValueError(f"invalid decision cell: {(x, y)}")
        with probe.span("click.pyautogui"):
            pyautogui.click(
                self.bx + x * self.cell_width,
                self.by + y * self.cell_width,
            )

    @staticmethod
    def _parse_transitions(r):
        """把 C++ decide 返回的 transitions 规整成 UI 契约列表。

        帮助模式由 C++ 在决策出口统一产出（OPTIMAL 层自带，其余层补前瞻）。
        值为**条件概率**：以「点击该格后安全」为前提，各可观测分支的概率
        （同分支概率之和 = 1）。每个分支带 ``board`` = 点击后的完整 7×7
        局部局面，供浮层画多个小棋盘。其它模式返回 None ⇒ UI 显示"暂无"。
        绝不在此合成任何数值或局面。
        """
        raw = (r or {}).get("transitions")
        if not raw:
            return None
        out = []
        for item in raw:
            try:
                board = item.get("board")
                out.append({
                    "probability": float(item["probability"]),
                    "new_cells": [tuple(int(v) for v in c)
                                  for c in (item.get("new_cells") or [])],
                    "label": str(item.get("label") or ""),
                    "board": [[int(v) for v in row] for row in board]
                             if board else None,
                })
            except (KeyError, TypeError, ValueError):
                continue
        return out or None

    def _emit_live_heatmap(self, cell_value, phase="decision"):
        """在 C++ decide 内部每轮向 UI 推送最新决策，而不是等 number5_1 返回。

        自动模式的 decide→click→rescan 循环可能持续很久；若只由 solver.play 在
        number5_1 返回后发信号，界面下方文字和热力图会整段时间停在旧值。这里复用
        solver 的唯一发射口，保持 payload 结构一致；played/win 从 play() 在进入
        number5_1 前写入的短生命周期字段读取，不参与算法决策。
        """
        emit = getattr(self, "_emit_heatmap", None)
        if not callable(emit):
            return
        try:
            emit(
                cell_value,
                getattr(self, "_heatmap_played", 0),
                getattr(self, "_heatmap_win", 0),
                phase=phase,
            )
        except Exception as e:  # noqa: BLE001 —— UI 推送失败不得打断点击链
            logger.debug("实时热力图发射失败（忽略）: %s", e)

    def _number5_1_core(self, cell_value):
        """
        5.1 数字统计
        :param cell_value:格子值
        :return:
        """
        confidence = 0
        w = cell_value.shape[1] - 2
        h = cell_value.shape[0] - 2
        # 耗时预算状态初始化（仅 tuned 模式）。方案 A 删除自动模式 Python 决策链
        # 后，**预算裁剪的消费点**（part_solve 枚举段的 limit 压缩、win_rate 阈值
        # 读写）已随之移除；但这一层状态仍需初始化，原因有二：
        #   ① `_ps_refit_k` 的拟合结果经 `_tb_save` 落盘 `state/time_budget.json`，
        #      是跨进程持久行为（bench/verify_fitk_window.py 等据此验收）；
        #   ② bench（`bench/sweep_prod_params.py`、`bench/ab_policy.py`）与
        #      `sim/eval_harness.py` 直接读写实例属性 `_time_budget`。
        if native.tuned() and not hasattr(self, "_time_budget"):
            self._time_budget = _tb_load()
            _ps_refit_k(self._time_budget)   # 每次运行启动：用历史样本自动拟合 k
        # win_rate 内存降级警告去重集合。
        # 注：产生该警告的 _wr_memory_ok 与 win_rate 方法已于 2026-11-09 按用户
        # 裁决删除（C++ 决策链与 Python 生产侧均零调用）。集合本身保留，因为
        # `_wr_mem_warned` 仍在若干历史 bench 脚本与测试的预置路径上被引用。
        if not hasattr(self, "_wr_mem_warned"):
            self._wr_mem_warned = set()
        # win_rate 节点数超限触发次数（可观测：降级是否发生过、发生几次）
        if not hasattr(self, "_wr_nodecap_hits"):
            self._wr_nodecap_hits = 0

        num9 = 0  # 所有未开方格（cell_value == 9）的数量
        # ⚠️ num10 = **已被标记为雷的格数**（cell_value == 10 的格数），
        # 不是"数字 10 的格"。cell_value 编码：9=未开，10=标记雷，0..8=已开数字。
        # 关键推论：真实剩余雷数 = a - num10，很多地方据此推导（如
        # rem = a - num10 - mine_total）。搞错会让世界雷数整体偏移。
        num10 = 0
        for index in range(1, w + 1):
            for j in range(1, h + 1):
                if cell_value[j, index] == 10:
                    num10 += 1

        # 找到所有未开方格
        bg = np.zeros((h, w), dtype=np.uint8)
        for index in range(w):
            for j in range(h):
                if (
                        cell_value[j + 1, index + 1] == 9
                        or cell_value[j + 1, index + 1] == 10
                ):
                    bg[j, index] = 255

        # 计算255到0的最小距离，以找到最边缘的9与10
        res = cv.distanceTransform(bg, cv.DIST_L12, 0)  # cv.DIST_L12: 平方相加开根号。

        # ⚠️ clicks / clicks9 的划分依据是「距最近未开格/标记格的距离」，不是别的：
        #   clicks  = **前沿**未开格（与已开数字格相邻 ⇒ 受邻域数字约束，需枚举解）
        #   clicks9 = **内部**未开格（远离已知信息 ⇒ 只受"剩余总雷数"这一个全局约束）
        # 名字里的 "9" 来自 cell_value 的编码 9=未开，**不是**"9 个"或"第九组"。
        # 两者混淆会让 part_solve 把无约束格当有约束格算（或反之），概率全错。
        clicks = []  # 外围未开方格（前沿，有局部约束）
        clicks9 = []  # 内部未开方格

        for i in range(w):
            for j in range(h):
                if cell_value[j + 1, i + 1] == 9:
                    num9 += 1
                if res[j, i] <= 1.5 and cell_value[j + 1, i + 1] == 9:
                    clicks.append(tuple((i + 1, j + 1)))
                elif res[j, i] > 1.5 and cell_value[j + 1, i + 1] == 9:
                    clicks9.append(tuple((i + 1, j + 1)))
        if len(clicks9) == 0 and len(clicks) == 0:  # 识别错误或没检测到胜利/失败窗口
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9
            try:
                cell_value = self.complete_scan(cell_value)
            except LowConfidenceScanError as e:
                # 局末失败弹窗盖住棋盘时扫描不可信：本轮作废并返回未开棋盘，
                # **不计 count、不弹警告**——否则 play() 的 `count >= 3` 会把
                # 「刚死了一局」误判成「设置错误」而停掉自动循环（用户裁决
                # 2026-11-07：扫描不确定不得影响自动循环执行）。
                # 同时清空旧热力图，避免 UI 把上一局概率误显示成当前进度。
                self._last_heatmap = {}
                logger.warning("全盘扫描不确定，本轮作废（不影响自动循环）：%s", e)
                return cell_value
            self.count += 1
            return cell_value

        # ---- 盘面逻辑一致性校验（用户指令 2026-11-08）----
        # 视觉扫描偶发错误会产出逻辑上不可能的盘面（数字与邻域冲突、
        # 总雷数超过未开格数等）。用这种盘面求解只会得到错误概率，
        # 且 C++ 侧表现不一致（实测 empty_group / ValueError / no_closed_cell）。
        # 策略（用户裁决）：矛盾则**退回重扫**，最多 3 次；仍矛盾才报错。
        _BOARD_RETRY_MAX = 3
        for _attempt in range(1, _BOARD_RETRY_MAX + 1):
            _ok, _problems = validate_board_consistency(
                cell_value, self.a, w, h)
            if _ok:
                break
            _detail = format_board_problems(_problems)
            logger.warning(
                "盘面逻辑矛盾（第 %d/%d 次校验）：%s",
                _attempt, _BOARD_RETRY_MAX, _detail)
            if _attempt >= _BOARD_RETRY_MAX:
                # 3 次仍矛盾：本轮作废，交由上层弹窗提醒用户检查设置/校准。
                msg = (f"盘面识别结果逻辑矛盾（已重扫 {_BOARD_RETRY_MAX} 次）："
                       f"{_detail}。请检查设置中的总雷数/宽高，或重新校准窗口位置"
                       f"与模板。")
                logger.error("%s", msg)
                try:
                    self.warning_signal.emit(msg)
                except Exception:  # noqa: BLE001 —— 告警失败不得掩盖原始问题
                    pass
                self._last_heatmap = {}
                self.count += 1
                return cell_value
            # 退回重扫：清空全盘后重新识别（保留"全未开"作为扫描起点）
            try:
                _blank = np.zeros((h + 2, w + 2), dtype="int32")
                for _i in range(1, w + 1):
                    for _j in range(1, h + 1):
                        _blank[_j, _i] = 9
                cell_value = self.complete_scan(_blank, False)
                self.cell_value = cell_value
            except LowConfidenceScanError as e:
                logger.warning("盘面矛盾后重扫不可信（第 %d 次）：%s",
                               _attempt, e)
                if _attempt >= _BOARD_RETRY_MAX:
                    self._last_heatmap = {}
                    self.count += 1
                    return cell_value
            except Exception as e:  # noqa: BLE001 —— 重扫故障不得杀死线程
                logger.warning("盘面矛盾后重扫失败（第 %d 次）：%s",
                               _attempt, e)
                if _attempt >= _BOARD_RETRY_MAX:
                    self._last_heatmap = {}
                    self.count += 1
                    return cell_value

        # ---- C++ 三层统一决策（OPTIMAL / 自适应前瞻 / pbs+PSEQ）----
        # 2000 局角落基线 38.05%（vs 中心 24% 旧基线，+14pp 总提升）。
        # **自动模式与帮助模式的唯一决策路径**（用户批准方案 A：C++ decide 可用时
        # 不可达的重复 Python 决策链已删除；帮助模式的 mine_clear1/mine_clear3_1
        # Python 规则推理链也已按用户指令 2026-11-07 移除）。
        #
        # 帮助模式（is_play=False）放宽（用户指令：「帮助人类的话可以适当
        # 提高推理的预算」「可以稍微把预算放高一点」）。放宽值全部**相对自动
        # 模式基线推导**，不另起炉灶：
        #   wr_threshold: 自动基线 = 6 + LIMIT_BONUS_WIN_RATE(=2) = 8（见
        #                 native.py 的 LIMIT_BONUS_WIN_RATE）；帮助 = 基线 +
        #                 HELP_WR_BONUS(10) = 18（更多局面进自适应前瞻层）。
        #   node_cap:     自动 = 200 万；帮助 = HELP_NODE_CAP(600 万)。
        #   决策预算:     自动 = DECISION_TIME_BUDGET(30s)；帮助 ×3 = 90s
        #                 （经 decision_budget()，由 solver.help() 设乘数）。
        # 自动模式参数与 38.05% 基线完全一致，不受影响。
        #
        # ⚠️ 失败语义（用户硬裁决）：decide 抛异常 / 返回无效坐标 / has_decide()
        # 为 False 一律走 _decide_unavailable（ERROR 日志 + warning_signal +
        # 全盘安全重扫 + 显式 return），**绝不**静默回退到第二套 Python 决策实现。
        if native.has_decide():
            _help_mode = not self.is_play
            # ⚠️ 2026-11-09：原先这里有一整套「自适应 wr_threshold 接线」
            #   （读 state/time_budget.json → 传 C++ → 按 limitation + 耗时调阈值
            #    → 写回 json）。用户裁决「wr_threshold winrate 分支都取消了，
            #   应该取消这个参数」后，该链路已整体删除，理由链：
            #     ① C++ 的 win_rate 准入 `limitation <= wr_threshold` 随 LAYER 2 删除；
            #     ② wr_threshold 在 C++ 函数体内零消费（传了也被忽略）；
            #     ③ 因此「读阈值→传参→据结果调阈值→写盘」是自我循环的空转，
            #        日志看似自适应、实际零效果。
            #   保留的放宽仍有效：node_cap（帮助模式 600 万）与决策预算（×3）。
            # 内存闸门 1.5GB 与 node_cap 200万完全不动（用户硬约束）。
            try:
                # 先发一帧“正在计算下一步”，否则 C++ 决策期间自动页会继续
                # 显示上一轮文字，用户误以为界面卡住或与真实进度脱节。
                self._emit_live_heatmap(cell_value, phase="computing")
                _t_decide = time.perf_counter()
                r = native.decide(
                    cell_value, self.a, w, h,
                    node_cap=HELP_NODE_CAP if _help_mode else 2_000_000,
                    is_play=self.is_play,
                )
                _decide_ms = (time.perf_counter() - _t_decide) * 1000.0
                # 【记录点 1】首轮 decide：真实输入规模 + 输出 + 实测耗时。
                # 只观测不改行为（异常内部吞掉），用于复核 limitation / 内存门
                # 的争议结论。MSW_DECIDE_TRACE=0 可关闭。
                decide_trace.record(
                    cell_value, self.a, w, h, r, _decide_ms,
                    node_cap=HELP_NODE_CAP if _help_mode else 2_000_000,
                    is_play=self.is_play, tag="first")
                # 【端到端探针】同一次 decide 的遥测（MSW_E2E_PROBE=1 才生效）。
                probe.decision(r, _decide_ms, tag="first",
                               help_mode=bool(_help_mode))
                # （原「自适应阈值升降档」已删除：wr_threshold 参数本身已取消，
                #   见上方说明。这里不再观测 limitation 去调任何东西。）
                if r and r["x"] >= 1 and r["y"] >= 1:
                    _DECIDE_FAIL_SEQ[0] = 0  # 成功即清零（连续失败计数语义）

                    # ================= decide 循环（用户拍板 2026-11-06）=================
                    # 自动模式（is_play=True）：内嵌「decide → 点击 → rescan →
                    # 再 decide」循环，复刻 40% 压测口径的决策节奏。
                    # 背景（同 seed 对拍已证）：压测纯 decide 循环每步都调
                    # decide → certain_safe 连锁批量点击（60% 命中率），100 局
                    # 40%；而本函数原实现单次 decide 后返回主循环，decide 频率
                    # 仅 410/百局、certain_safe 25/百局（压测 1424/百局），胜率
                    # 只有 ~20%——免费推理（mine_clear）填不平节奏差。
                    # 停止条件（防死循环）：① decide 返回无效坐标；② 一轮点击后
                    # 盘面无任何变化（rescan 后 num9 未减少）；③ 无未开格（胜利）。
                    # ⚠️ **不做逐点击踩雷检测**（用户裁决 2026-11-07）：局末棋盘
                    # 出现雷/失败弹窗时扫描本来就不准，属正常节奏，不该打断循环；
                    # 整局胜负由主循环的双弹窗检测接管。
                    # 帮助模式（is_play=False）保持单次 decide（现状），不点击。
                    # =====================================================================
                    if not self.is_play:
                        # ---- 帮助模式：C++ decide 循环（只收集候选，不自动点击）----
                        # 与自动模式共用**同一条** C++ decide 决策源；区别只在执行者：
                        # 帮助模式由人类点「点击所有确定方格」执行，故此处绝不点击。
                        #
                        # 🔴 关键修复 1（用户实测「点击所有确定方格不能用」）：
                        # decide 的 certain_safe 分支把**整批**已证明安全格放在
                        # ``r["tied"]`` 里（C++ 注释：返回整批，调用方应逐个点击），
                        # 而旧实现只取 ``r["x"], r["y"]`` 这**单格**，其余同批确定格
                        # 被整体丢弃 ⇒ 候选表只有 1 格，按钮点下去只动一格。
                        # 现在把整批写入候选表。
                        #
                        # 🔴 关键修复 2：帮助模式此前**不是循环**（decide 一次就
                        # return），与自动模式的 decide 循环节奏不对称，用户要求
                        # 「统一成为 C++ decide 循环」。现在循环调用 decide，每轮把
                        # 新出现的候选并入 pos_dict_list，直到：
                        #   ① decide 返回无效坐标；② 某轮没有任何新增候选（说明
                        #   当前盘面的确定信息已被收集干净，继续只会重复同一批）。
                        # 帮助模式不点击 ⇒ 盘面不变，故终止条件主要靠 ②。
                        _help_loop_max = 16  # 防御性上限
                        _rounds = 0
                        _added_total = 0
                        while _rounds < _help_loop_max:
                            _rounds += 1
                            rx, ry = int(r["x"]), int(r["y"])
                            reason = str(r["reason"])
                            confidence = float(r.get("confidence", 0))
                            prob = r.get("prob") or {}
                            tied = [(int(c[0]), int(c[1]))
                                    for c in (r.get("tied") or [])]
                            # 帮助页热力图：最近一次决策的逐格概率 + 最佳格
                            _prob_hm = {}
                            for k, v in prob.items():
                                try:
                                    _prob_hm[(int(k[0]), int(k[1]))] = float(v)
                                except (TypeError, ValueError, IndexError):
                                    continue
                            self._last_heatmap = {
                                "prob": _prob_hm,
                                "best": (rx, ry),
                                "best_conf": round(confidence, 5),
                                "total": float(r.get("world_upper", 0) or 0),
                                # 真实状态转移：帮助模式由 C++ 在决策出口统一
                                # 补精确世界前瞻（OPTIMAL 层自带），其它模式为 None。
                                "transitions": self._parse_transitions(r),
                                "transition_meta": _help_transition_meta(r),
                            }
                            # 🔴 候选表真源 = 逐格安全概率 prob（与 main.py
                            # _help_action_targets 完全一致），不再只收单点 (x,y)。
                            # 用户实测「最后连推荐点击的格子都没有了」：win_rate
                            # 阶段 C++ 返回 tied 56 个确定安全格、prob==1.0 也
                            # 是 56 个，但旧逻辑只在 certain_safe 分支取整批，
                            # win_rate/pbs/limitation 分支只收 (x,y) 一格 ⇒
                            # 棋盘上只剩 1 个推荐标记，其余 55 格全部消失。
                            # 现在把所有 prob>=1 的确定安全格都做成候选；
                            # prob<=0 的确定雷也纳入（供「点击所有确定方格」标雷）。
                            _safe_cells = set()
                            _mine_cells = set()
                            # proven_safe：仅「已证明安全」的格（prob>=1 或
                            # certain_safe 的 tied）。P0-1 修复前把 decide 的
                            # best 无条件塞进 _safe_cells，导致「点击所有确定
                            # 方格」会点非确定格（review2 architect P0-1）。
                            _proven_safe = set()
                            for _k, _v in prob.items():
                                try:
                                    _kx, _ky = int(_k[0]), int(_k[1])
                                    _p = float(_v)
                                except (TypeError, ValueError, IndexError):
                                    continue
                                if _p >= 0.999999:
                                    _safe_cells.add((_kx, _ky))
                                    _proven_safe.add((_kx, _ky))
                                elif _p <= 0.000001:
                                    _mine_cells.add((_kx, _ky))
                            # tied 里的确定安全格作为交叉补充（旧语义保留）
                            if reason == "certain_safe":
                                for _c in tied:
                                    _safe_cells.add(_c)
                                    _proven_safe.add(_c)
                            # 至少给出决策单点，避免任何分支下候选为空：
                            # best 只作为 UI 推荐标记（is_best），**不进入
                            # 确定安全集合**，也不标 proven_safe；批量点击由
                            # main._help_action_targets 只按 proven_safe 收集。
                            _best_only = None
                            if rx >= 1 and ry >= 1 and (rx, ry) not in _proven_safe:
                                _best_only = (rx, ry)
                                _safe_cells.add((rx, ry))
                            # 不截断：expert 盘最多 480 格，全部候选都展示
                            # 才不会再出现「推荐格只剩几个」的观感；64 上限
                            # 是旧单点决策时代的防御值，已不适用。
                            _batch = sorted(_safe_cells)
                            _mine_batch = sorted(_mine_cells)
                            _added_this_round = 0
                            for (bx, by) in _batch:
                                if (bx, by) in self.appended_pos:
                                    continue
                                # 每个候选必须拿到**唯一递增序号**：main.py 用它
                                # 渲染候选格文字并据此绑定点击/预览。
                                self.num += 1
                                # confidence 用逐格安全概率（不是 decide.confidence
                                # ——后者在 win_rate 是策略胜率，实测=1.0 会被
                                # 误读成「该格 100% 安全」）。
                                _p_cell = 1.0
                                try:
                                    _p_cell = float(prob.get((bx, by), 1.0))
                                except (TypeError, ValueError):
                                    pass
                                self.pos_dict_list.append({
                                    "pos": (bx, by),
                                    "confidence": round(_p_cell, 5),
                                    "num": self.num,
                                    "is_mine": False,
                                    "is_best": True,
                                    # P0-1：只有「已证明安全」才能进批量点击。
                                    # best 兜底格（_best_only）为 False。
                                    "proven_safe": (bx, by) in _proven_safe,
                                    "exp": f"C++ 三层决策 ({reason})",
                                    "is_recommend": True,
                                })
                                self.appended_pos.add((bx, by))
                                _added_this_round += 1
                            for (bx, by) in _mine_batch:
                                if (bx, by) in self.appended_pos:
                                    continue
                                self.num += 1
                                self.pos_dict_list.append({
                                    "pos": (bx, by),
                                    "confidence": 0.0,
                                    "num": self.num,
                                    "is_mine": True,
                                    "is_best": False,
                                    "exp": f"C++ 三层决策 ({reason})",
                                    "is_recommend": False,
                                })
                                self.appended_pos.add((bx, by))
                                _added_this_round += 1
                            _added_total += _added_this_round
                            # 决策一产生就刷新一次：帮助页下方文字/棋盘立刻反映
                            self._emit_live_heatmap(cell_value, phase="decision")
                            # 终止条件：本轮无新增候选（盘面未变，继续只会重复）；
                            # 或 decide 已无有效坐标（该格已开完/无建议）。
                            if _added_this_round == 0 or not _batch:
                                break
                            # 帮助模式不点击 ⇒ 盘面不变。再 decide 一次以确认是否还有
                            # 未收集的候选（例如 tied 被 64 上限截断时）。
                            try:
                                r = native.decide(
                                    cell_value, self.a, w, h,
                                    node_cap=HELP_NODE_CAP,
                                    is_play=self.is_play,
                                )
                            except Exception as e:
                                return self._decide_unavailable(
                                    cell_value, exc=e,
                                    where="帮助 decide 循环内 decide 异常")
                            if not (r and r["x"] >= 1 and r["y"] >= 1):
                                break
                        self._emit_help_heatmap(cell_value)
                        # 帮助模式决策成功也清零 count（自旧链 :1112 迁移）：
                        # count 是模块内跨呼叫的失败计数，若只在自动模式清零，
                        # 帮助模式的失败会泄漏到随后的自动模式 play()，被
                        # `count >= 3` 误报成「请检查设置」。
                        self.count = 0
                        return cell_value

                    # ---- 自动模式：decide 循环 ----
                    _loop_max = 512  # 单次 number5_1 内 decide 上限（防御性）
                    _iter = 0
                    while True:
                        _iter += 1
                        # 协作式取消安全点（紧急热键 / GUI 停止按钮共用）。
                        # decide 循环每轮会真实点击 + 重扫，若不在这里检查，
                        # 用户按 ESC 后最长还要等 512 轮才退出 ⇒ 等于无法终止。
                        if self._stop_requested():
                            logger.info("收到停止请求，自动模式在 decide 循环内安全退出")
                            self.count = 0
                            return cell_value
                        if _iter > _loop_max:
                            logger.warning("decide 循环达上限 %d，跳出", _loop_max)
                            break
                        rx, ry = int(r["x"]), int(r["y"])
                        reason = str(r["reason"])
                        confidence = float(r.get("confidence", 0))
                        prob = r.get("prob") or {}
                        tied = [(int(c[0]), int(c[1])) for c in (r.get("tied") or [])]
                        # 热力图（每轮更新，界面实时反映当前决策）
                        _prob_hm = {}
                        for k, v in prob.items():
                            try:
                                _prob_hm[(int(k[0]), int(k[1]))] = float(v)
                            except (TypeError, ValueError, IndexError):
                                continue
                        self._last_heatmap = {
                            "prob": _prob_hm,
                            "best": (rx, ry),
                            "best_conf": round(confidence, 5),
                            "total": float(r.get("world_upper", 0) or 0),
                        }
                        # 决策一产生就刷新一次：UI 下方的当前建议/概率不再等待
                        # 整个 number5_1 返回后才变化。
                        self._emit_live_heatmap(cell_value, phase="decision")
                        # 本轮要点的格子（certain_safe=批量，其余=单点）
                        if reason == "certain_safe" and tied:
                            _to_click = tied[:64]
                        elif rx >= 1 and ry >= 1:
                            _to_click = [(rx, ry)]
                        else:
                            break  # decide 放弃（无效坐标）→ 交回主循环推理
                        _num9_before = int(np.sum(cell_value == 9))
                        for (cx, cy) in _to_click:
                            # 批量点击内部也要响应取消：certain_safe 一次最多
                            # 点 64 格（每格 29ms ≈ 1.9s），按 ESC 后不应继续
                            # 把整批点完。
                            if self._stop_requested():
                                logger.info("收到停止请求，批量点击中途安全退出")
                                self.count = 0
                                return cell_value
                            if cell_value[cy, cx] != 9:
                                continue  # 已开/已标记（certain_safe 批量可能含旧格）
                            # C++/棋盘坐标统一为 1-based；点击像素由唯一 helper
                            # 计算，避免批量 certain_safe 与单点路径出现偏移分歧。
                            # 探针：记录该击的自评安全率（prob 里该格的「不是雷概率」，
                            # 缺省时退回 decide 的 confidence）——事后用于校准与判死因。
                            probe.click(
                                cx, cy,
                                conf=prob.get((cx, cy), confidence),
                                reason=reason, source="decide",
                                num9_before=_num9_before,
                                batch=len(_to_click))
                            self._click_decision_cell((cx, cy))
                            # ⚠️ 点完**只点击、不探测**（用户裁决 2026-11-07）：
                            # 这里曾逐点击 `_locate("./image/lose.bmp")` 判死并
                            # `time.sleep(setting.sleep)`，现两者都删除——
                            # ① 局末棋盘出现雷/失败弹窗时扫描本来就不准，属正常节奏；
                            # ② 胜负判定统一由主循环 `utils/solver.py` 的
                            #    「游戏胜利/游戏失败」弹窗检测接管（本切点未改）。
                            # 点击节奏由 pyautogui.PAUSE(=setting.sleep) 自带。
                        self._suppress_rescan_warning = True
                        try:
                            # 自动模式局末失败弹窗会让棋盘像素暂时不可识别；
                            # 不弹出“请重新截图/校准”告警，交给主循环处理胜负。
                            # 使用实例级短生命周期标记而非新增调用参数，保持
                            # rescan_after_click 对旧替身/外部调用方的兼容性。
                            cell_value = self.rescan_after_click(cell_value)
                        except LowConfidenceScanError as e:
                            # 扫描不确定（典型场景：局末失败弹窗盖住棋盘）**不是
                            # 决策失败**：本轮作废、原样交回盘面，不计 count、不弹
                            # 警告。否则 play() 的 `count >= 3` 会把「死了一局」
                            # 误判成「设置错误」并停机——用户明确要求该错误不得
                            # 影响自动循环继续执行。
                            logger.warning(
                                "decide 循环重扫不确定，本轮作废（不影响自动循环）：%s", e)
                            return cell_value
                        finally:
                            self._suppress_rescan_warning = False
                        # 点击后重扫得到最新事实棋盘；即使本轮没有进展，也要立即
                        # 把新 cell_value 推给 UI，避免下方文字/棋盘停在旧帧。
                        self._emit_live_heatmap(cell_value, phase="rescanned")
                        # 无进展守卫：点击后 num9 未减少 → 防死循环，跳出
                        _num9_after = int(np.sum(cell_value == 9))
                        if _num9_after >= _num9_before:
                            logger.debug("decide 循环无进展（num9 %d→%d），跳出",
                                         _num9_before, _num9_after)
                            break
                        # 胜利守卫：无未开格了
                        if _num9_after == 0:
                            break
                        # 继续 decide（盘面已更新；decide 异常 → 统一失败出口）。
                        try:
                            _t_decide = time.perf_counter()
                            r = native.decide(
                                cell_value, self.a, w, h,
                                node_cap=HELP_NODE_CAP if _help_mode else 2_000_000,
                                is_play=self.is_play,
                            )
                            _decide_ms = (time.perf_counter() - _t_decide) * 1000.0
                            # 【记录点 2】decide 循环内的每轮：同上，tag=loop。
                            decide_trace.record(
                                cell_value, self.a, w, h, r, _decide_ms,
                                node_cap=(HELP_NODE_CAP if _help_mode
                                          else 2_000_000),
                                is_play=self.is_play, tag="loop")
                            probe.decision(r, _decide_ms, tag="loop",
                                           help_mode=bool(_help_mode))
                            # （原「循环内自适应升降档」已删除：wr_threshold 取消，
                            #   见首轮 decide 处的说明。）
                        except Exception as e:
                            # 不能只 break 后落到「正常返回」——那样下一轮主循环
                            # 会以为本轮决策成功，也不再产生安全重扫语义。
                            return self._decide_unavailable(
                                cell_value, exc=e, where="decide 循环内 decide 异常")
                        if not (r and r["x"] >= 1 and r["y"] >= 1):
                            return self._decide_unavailable(
                                cell_value,
                                where="decide 循环内返回无效坐标",
                                detail=_decide_diag(r))
                    # 自动模式 decide 循环正常结束（无进展/胜利/重扫不确定/达上限）：
                    # `self.count = 0` 在此清零（自旧链 :1112 迁移）——只有真正
                    # 走完一轮 decide 决策才算成功，否则 play() 的 `count >= 3`
                    # 会把「识别失败」误报成「请检查设置」。
                    self.count = 0
                    return cell_value
                # 首轮 decide 返回 None / 无效坐标（x<1 或 y<1）→ 统一失败出口。
                # ⚠️ 必须显式 return：控制流继续下行会落到已删除的 Python 决策链
                # 位置（历史「点一格两下/两个弹窗」根因即此类悬空控制流）。
                return self._decide_unavailable(
                    cell_value, where="decide 返回无效坐标",
                    detail=_decide_diag(r))
            except Exception as e:
                # 用户硬裁决：decide 失败 = 可观测错误 + 全盘安全重扫，**不回退**。
                # 历史根因（旧注释 :706-709）：漏 import setting 的 NameError 曾被
                # 裸 except 吞掉 → 静默跌回 Python 链重复点击。现统一走 helper。
                # where 写「decide 分支」而非「decide 调用」是因为这个 try 覆盖整个
                # 分支（含点击/重扫/热力图组装）——如实标注才不会把点击路径的
                # 异常误归因成 C++ decide 的失败。
                return self._decide_unavailable(
                    cell_value, exc=e, where="decide 分支异常")
        # ---- decide 不可用（旧 pyd / MSW_DISABLE_NATIVE=1 / 无 decide 导出）----
        # 方案 A 明确：不可用也计入「决策不可用」，走同一 helper，不执行任何
        # Python 枚举（否则等于保留一条静默回退通道）。
        return self._decide_unavailable(cell_value, where="has_decide=False")

