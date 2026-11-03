# -*- coding: utf-8 -*-
"""概率枚举与胜率计算：number5_1 主入口、部分枚举(part_solve)、
试解评估(try_solve)、大局面近似(process_bigger_situation)与
期望胜率搜索(win_rate)。

自 utils/util.py 拆分而来。``ProbabilityMixin`` 不单独使用，
由 ``utils.solver.Solver`` 混入，依赖实例属性：
``w``/``h``/``a``/``p``/``limit``/``is_play``/``num``/``count``/``memory``/
``checked``/``pos_dict_list``/``appended_pos``/``bx``/``by``/``cell_width``，
以及 ``cell_around``/``get_set_1``/``complete_scan``/``mine_clear1``/
``mine_clear3_1``/``_locate``/``_load_stats``/``_flush_stats``/
``_throttled_pv_signal_emit`` 与各 Qt 信号。
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

logger = get_logger(__name__)

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
_PS_REF_SIZE = 33            # 模型参考组大小（实测标定点：C++ 2.3ms / Python 976ms）
_PS_SAFETY = 2.0             # 预测安全系数（组形状差异可达 10x + 模型误差）
_PS_RESERVE_S = 3.0          # 枚举阶段为之后的 win_rate / pbs / 决策输出预留的时间（秒）
# win_rate 阈值调节（用户规则：步长 0.5，无上限，下限 0）——起点 6+2=8，
# 见 _wr_update：升档需边界采样(|limitation-阈值|≤Δ)且耗时<预算×_WR_FAST_FRAC；
# 降档为安全阀（耗时>预算×_WR_SLOW_FRAC，不要求边界，否则升上去收不回来）
_WR_STEP = 0.5             # 每次升降 0.5
_WR_EDGE_DELTA = 0.25      # 升档的“边界采样”邻域
_WR_FAST_FRAC = 0.25       # 升档耗时上限（预算比例）
_WR_SLOW_FRAC = 1       # 降档耗时下限（预算比例）；=1 即 win_rate 真超总预算才降档（安全阀，勿按旧版 75% 理解）

# ---------------------------------------------------------------------------
# win_rate 节点数硬上限（防内存爆炸的最小闸门）
#
# ==================== 为什么需要它、以及为什么"压缩世界表示"不够 ====================
# win_rate 的内存结构有两项：
#     ① 世界列表：nworlds × 2304B（每世界一整盘 (h+2)(w+2) int32）
#     ② memo 缓存：live_nodes × ~200B（std::string 键 + unordered_map 节点）
# 关键事实：**memo 只写不删**（cpp/mscore.cpp wr_f 末尾只赋值），
# 所以 live_nodes == 累计访问节点数，且它随 (候选数 × 世界分叉 × 深度) 组合增长。
# 用实测参数做的容量模型（bench/mem_model.py）显示：生产中位 4 世界时 ①②都是
# 微秒级；但单用例实测到 1e5 节点时，②约占 19MB 而①不足 0.15MB —— 即
# **节点开销完全主导**。把世界压到 64B（位集）在该场景几乎不省内存。
# 因此真正的防爆手段是给节点数封顶，而不是压缩世界表示。
#
# ==================== 取值（按内存预算标定）====================
# 默认**启用**（防爆闸门默认关闭等于没有防护）。
#
# 标定唯一依据 = 内存预算 + 实测每节点字节：
#   实测每节点内存（bench/calibrate_node_cap.py，真实 RSS 峰值采样）：
#       cap=2e3    → ΔRSS 0.00 MB
#       cap=1e4    → ΔRSS 0.10 MB
#       cap=5e4    → ΔRSS 0.73 MB
#       cap=2e5    → ΔRSS 4.04 MB
#       不限制      → ΔRSS 8.25 MB
#   ⇒ 每节点 **中位 15.2 B、最大 21.2 B**。取 25 B/节点（留余量）作口径：
#       2e6 × 25B ≈ 48 MB
#   对照 1.5GB 预算（与 _wr_memory_ok 同口径）/ 留 8x 余量 ⇒ 2e6 安全。
#
# ⚠️ **不要**用"本机用例是否会触发"来反推 cap：
#   防护器的预期行为就是**正常路径不触发、异常路径及时拦截**。
#   实测（bench 用例逐例二分）本机最大节点需求仅 424,114，
#   因此在 2e6 下零触发是**正确**的，不构成"闸门无效"的理由——
#   无效与否由「强制超限负测 + 峰值 RSS」证明，见：
#     bench/verify_node_cap.py        超限必抛、消息可辨识
#     bench/verify_node_cap_e2e.py    异常穿越调用链进安全降级 + 计数可观测
#     bench/calibrate_node_cap.py     真实 RSS：cap=2e5 时 ΔRSS 4.04MB
# 若某天出现比实测最坏还大 4.7 倍的异常局面（>1e6 节点），闸门会拦截并降级，
# 这正是它存在的意义。
#
# 生产参照：上一轮 12 局 win_rate 只触发 8 次、世界数中位 4，
# 单次节点数远低于 2e6 ⇒ **正常对局行为与加闸前逐位一致**（对拍为证）。
#
# ==================== 超限语义 ====================
# C++ 抛 runtime_error("win_rate node cap exceeded ...") → 本文件**按消息子串**
# 识别后转成 _WrNodeCapExceeded → number5_1 捕获后**降级走
# process_bigger_situation（贪心）**。绝不回退到纯 Python（那里同样无上限）。
# 注意：**只有**含 "node cap exceeded" 的异常才降级；其他 C++ runtime_error
# 仍走原有的"回退纯 Python"语义，不会被静默吞成贪心结果
# （负向测试 bench/verify_node_cap_e2e.py 第 I 节为证）。
# 每次触发计数在 self._wr_nodecap_hits，可观测。
# ---------------------------------------------------------------------------
_WR_NODE_CAP_RECOMMENDED = 2000000


def _wr_node_cap():
    """读取 win_rate 节点数硬上限（默认 2e6，实测标定）。

    环境变量 MSW_WR_NODE_CAP：正整数覆盖；**0 = 显式关闭**（仅对拍调试用）；
    负值/非法值回落默认值并只警告一次。
    """
    global _WR_NODE_CAP_WARNED
    raw = os.environ.get("MSW_WR_NODE_CAP", "").strip()
    if not raw:
        return _WR_NODE_CAP_RECOMMENDED
    try:
        v = int(raw)
        if v < 0:
            return _WR_NODE_CAP_RECOMMENDED
        return v                     # 0 = 显式关闭（对拍用）
    except ValueError:
        if not _WR_NODE_CAP_WARNED:
            _WR_NODE_CAP_WARNED = True
            logger.warning("MSW_WR_NODE_CAP=%r 不是整数，按默认 %d 处理", raw,
                           _WR_NODE_CAP_RECOMMENDED)
        return _WR_NODE_CAP_RECOMMENDED


_WR_NODE_CAP_WARNED = False


class _WrNodeCapExceeded(Exception):
    """win_rate 节点数超硬上限（用于与普通 C++ 失败区分，走不同降级路径）。"""


def _ps_default_base_ms():
    """模型初值：33 格组在当前模式下的典型耗时（毫秒）。

    C++ 取实测（2.3ms）的约 4 倍作保守初值（10ms），首次遭遇大组时宁可少算一点
    也不突破预算；随后由实测逐次校准收敛到真实值。Python 侧仅在 native 部分回退
    场景下由观测自校正，初值影响不大。
    """
    return 10.0 if native.available else 1500.0


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


def _budget_remaining(state, t_start):
    """距决策预算截止的剩余秒数（扣除枚举后阶段预留）。"""
    return t_start + DECISION_TIME_BUDGET - _PS_RESERVE_S - time.perf_counter()


def _wr_current_threshold(tb):
    """返回当前 win_rate 阈值（无观测时为起点 8；持久化在 tb["wr"]["t"]）。

    仅 native.tuned() 的 number5_1 决策调用；纯 Python（native 不可用）阈值恒 6。
    """
    start = 6.0 + native.LIMIT_BONUS_WIN_RATE  # 起点：6+2=8
    wr = tb.get("wr")
    if wr is None:
        return start
    return float(wr.get("t", start))


def _wr_update(tb, limitation, ms):
    """按一次真实的 win_rate 观测更新阈值。

    用户规则（每次只升降 0.5；无上限，下限 0）：
      升档（须同时满足）：
        - 本次真实 limitation 落在阈值 ±0.25 之内（边界采样：证明阈值临界处
          win_rate 也很快，才敢把阈值外推覆盖更大局面）；
        - 且本次 win_rate 耗时 < 预算×25%。
        → 阈值 +0.5
      降档（安全阀，任何情形）：
        - 耗时 > 预算×_WR_SLOW_FRAC（当前 =1，真超预算）→ 阈值 −0.5（下限 0）。
        注：降档不要求边界邻域——若降档也要求邻域，阈值被升上去后真实 limitation
        分布一旦整体低于阈值就再也收不回来（升档只发生在被边界“碰到”时）。
    其余情形阈值不变。返回更新后的阈值并写入 tb["wr"]（含 limitation 观测）。
    """
    budget_ms = DECISION_TIME_BUDGET * 1000.0
    th = _wr_current_threshold(tb)
    if ms < budget_ms * _WR_FAST_FRAC and abs(limitation - th) <= _WR_EDGE_DELTA:
        th += 0.5
    elif ms > budget_ms * _WR_SLOW_FRAC:
        th = max(0.0, th - 0.5)
    wr = tb.setdefault("wr", {})
    wr["t"] = th
    wr["limitation"] = limitation
    wr["ms"] = ms
    _tb_save(tb)
    return th


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
            predicted_s = DECISION_TIME_BUDGET
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

    def _number5_1_core(self, cell_value):
        """
        5.1 数字统计
        :param cell_value:格子值
        :return:
        """
        confidence = 0
        w = cell_value.shape[1] - 2
        h = cell_value.shape[0] - 2
        # 耗时预算控制（仅 tuned 模式启用；状态跨决策记忆，见模块级说明）
        t_decision_start = time.perf_counter()
        if native.tuned() and not hasattr(self, "_time_budget"):
            self._time_budget = _tb_load()
            _ps_refit_k(self._time_budget)   # 每次运行启动：用历史样本自动拟合 k
        _tb = getattr(self, "_time_budget", None) if native.tuned() else None
        # win_rate 内存降级警告去重集合（见 _wr_memory_ok）
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
            cell_value = self.complete_scan(cell_value)
            self.count += 1
            return cell_value

        limitation = 12
        # win_rate 触发阈值：C++ 提速后按实测放宽（原 6；纯 Python / MSW_NATIVE_TUNE=0 保持 6）
        wr_threshold = 6 + (native.LIMIT_BONUS_WIN_RATE if native.tuned() else 0)
        if len(clicks) == 0:  # 没有可以判断的格子
            _prob = {}  # 随机分支无概率数据（热力图由界面浅灰底兜底）
            total = 0
            res = []
            op_num = []
            for i, j in clicks9:
                op5x5_num = self.open_num5x5(cell_value, (i, j))
                op_num.append(op5x5_num)

            np_op_num = np.array(op_num)
            np_op_num = np.where(np_op_num == max(np_op_num), 1, 0)
            p = np_op_num / np_op_num.sum()
            pos = [clicks9[np.random.choice(np.arange(len(clicks9)), p=p)]]  # 随机选择
            confidence = 1 - (self.a - num10) / len(clicks9)  # 不是雷的概率
            self.pos_dict_list.append(
                {
                    "pos": pos[0],
                    "confidence": round(confidence, 5),
                    "num": self.num,
                    "is_mine": False,
                    "is_best": False,
                    "exp": "随机选择。",
                    "is_recommend": False,
                }
            )
        else:
            # 将clicks分组，组与组之间没有公共区域（没有公共数字格）
            click_list = [tuple([tuple(clicks[0])])]
            set_list = [
                self.get_set_1(clicks[0][0], clicks[0][1], cell_value)
            ]  # click_list没一个坐标组对应的周边数字格
            for pos in clicks[1:]:
                pos = tuple(pos)
                x, y = pos
                indexes = []
                se = self.get_set_1(x, y, cell_value)
                for index in range(len(click_list)):
                    if len(se & set_list[index]) > 0:  # &：交集 |：并集
                        indexes.append(index)

                if len(indexes) == 0:  # 与click_list中的任意一组都没有交集，单分一组
                    set_list.append(se)
                    click_list.append(tuple([pos]))
                else:
                    # 将所有与pos有交集的组合并
                    temp = []
                    b = 0
                    for index in indexes:
                        index -= b  # pop掉了b个，故要减去b
                        for p in click_list.pop(index):
                            temp.append(p)
                        b += 1
                    temp.append(pos)
                    click_list.append(tuple(temp))

                    # 更新set_list
                    temp = set()
                    b = 0
                    for index in indexes:
                        index -= b
                        temp = temp | set_list.pop(index)  # |：并集运算符
                        b += 1
                    temp = temp | se
                    set_list.append(temp)

            # 同序化clicks与click_list
            clicks = []
            for poses in click_list:
                for i, j in poses:
                    clicks.append(tuple((i, j)))

            # 计算limit
            temp = np.zeros(len(click_list))
            for i in range(len(click_list)):
                temp[i] = len(click_list[i])
            temp = temp >= 15
            t_sum = temp.sum()
            # C++ 提速 ~424x 后按同等墙钟预算放宽组枚举上限（纯 Python 保持原值）
            base_limit = self.limit + (native.LIMIT_BONUS_PART_SOLVE if native.tuned() else 0)
            limit = (
                base_limit - int(math.log2(t_sum) / 2) if t_sum != 0 else base_limit
            )  # 20大约20s 19 10s 18 5s（纯 Python 标定；C++ 下每 +1 约 2 倍算量但仅 ~1/424 耗时）
            if _tb is not None:
                # 预算控制：按指数模型预测各组耗时，压缩 limit 直到总预测 ≤ 剩余预算
                _rem = _budget_remaining(_tb, t_decision_start)
                _pred_total = float("nan")
                while limit > 5:
                    _pred_total = sum(
                        _ps_predict_ms(_tb, s) / 1000.0
                        for s in (len(g) for g in click_list)
                        if s <= limit + 3
                    )
                    if _pred_total <= _rem:
                        break
                    limit -= 1
                logger.debug("limit=%s（预算剩余 %.2fs，预测总耗时 %.2fs）",
                             limit, _rem, _pred_total)
                # 决策级进度：枚举段 [10,80) 按各组预测耗时加权（速度估计），
                # 之后逐组把 _pv_state 区间设为该组的 [lo, hi)
                _pv_sizes = [len(g) for g in click_list if len(g) <= limit + 3]
                _pv_w = [_ps_predict_ms(_tb, s) for s in _pv_sizes]
                _pv_wsum = sum(_pv_w) or 1.0
                _pv_bounds = []
                _pv_acc = 0.0
                for wgt in _pv_w:
                    lo = 10.0 + 70.0 * _pv_acc / _pv_wsum
                    _pv_acc += wgt
                    _pv_bounds.append((lo, 10.0 + 70.0 * _pv_acc / _pv_wsum))
                _pv_k = 0  # 组指针（跳过被移除的组）
            res_list = []
            canopen_res = np.array([])
            ck = []  # res_list中res的长度
            total = 1

            self.Visible_signal.emit(True)

            is_removed = False
            for index in range(len(click_list)):
                # 运算
                try:
                    if _tb is not None and _pv_k < len(_pv_bounds):
                        self._pv_state["lo"], self._pv_state["hi"] = _pv_bounds[_pv_k]
                        _pv_k += 1
                    self._pv_mapped_emit(0)
                    _res, _canopen_res = self.checked[tuple(click_list[index])]
                    _total = len(_res)
                    canopen_res = np.hstack((canopen_res, _canopen_res))
                    total *= _total
                    res_list.append(_res)
                    ck.append(_total)
                    self._pv_mapped_emit(100)
                except KeyError:
                    _over_budget = (
                        _tb is not None
                        and _ps_predict_ms(_tb, len(click_list[index])) / 1000.0
                        > t_decision_start + DECISION_TIME_BUDGET - _PS_RESERVE_S
                        - time.perf_counter()
                    )
                    if (
                        len(click_list[index]) > limit + 3 or _over_budget
                    ):  # 大于limit/超出预算时因为算量过大而无法判断
                        is_removed = True
                        # ⚠️ 这里是 clicks → clicks9 的**语义迁移**：
                        # 该组算量过大被放弃枚举，于是把它的前沿格**降级并入内部格**，
                        # 当作"只受总雷数约束"处理（近似）。这不是排序，也不是过滤。
                        # 后果：这些格失去局部约束信息，概率变粗，但避免指数爆炸。
                        for pos in click_list[index]:
                            clicks.remove(pos)
                            clicks9.append(pos)
                    else:
                        if _tb is not None and _pv_k < len(_pv_bounds):
                            self._pv_state["lo"], self._pv_state["hi"] = _pv_bounds[_pv_k]
                            _pv_k += 1
                        for li in list(self.checked.keys()):
                            if len(set(li) & set(tuple(click_list[index]))) != 0:
                                self.checked.pop(li)
                        try:
                            _t_ps = time.perf_counter()
                            _res, _total, _canopen_res = self.part_solve(
                                click_list[index],
                                cell_value,
                                num10,
                                num9 - len(click_list[index]),
                                set_list[index],
                                False,
                            )
                            if _tb is not None:
                                _ps_observe(_tb, len(click_list[index]),
                                            (time.perf_counter() - _t_ps) * 1000.0)
                        except KeyError:
                            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                            for i in range(1, w + 1):
                                for j in range(1, h + 1):
                                    cell_value[j, i] = 9
                            cell_value = self.complete_scan(cell_value)
                            self.count += 1
                            self.Visible_signal.emit(False)
                            return cell_value

                        if len(_res) == 0:
                            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                            for i in range(1, w + 1):
                                for j in range(1, h + 1):
                                    cell_value[j, i] = 9
                            cell_value = self.complete_scan(cell_value)
                            self.count += 1
                            self.Visible_signal.emit(False)
                            return cell_value

                        canopen_res = np.hstack((canopen_res, _canopen_res))
                        total *= _total
                        res_list.append(_res)
                        ck.append(_total)

                        self.checked[tuple(click_list[index])] = (_res, _canopen_res)

            # 阶段计时：辅助定位超预算耗时落在哪一段
            logger.debug("枚举阶段：%d 组待解，距决策开始 %.0f ms（超时若发生在此段为 part_solve 枚举）",
                         len(click_list),
                         (time.perf_counter() - t_decision_start) * 1000.0)

            if is_removed and (not self.is_play):
                self.warning_signal_2.emit(
                    "由于计算量的限制，一部分情况未枚举，结果可能不准确\n"
                    "您可以通过增加设置中的limit使枚举更全面，但limit\n"
                    "每增加1计算所需的时间增加1倍"
                )

            _prob = {}  # 兜底：候选格概率字典（1-based 坐标 → 不是雷概率），各决策分支会覆盖
            # ⚠️ 不是死代码：外层 else（443 行）已保证 len(clicks) > 0，但分支中又对clicks进行了修改；
            # 真正的随机分支在 419 行外层（曾因把 _prob 兜底误加在这里导致随机分支
            # UnboundLocalError）。保留仅为避免误删历史逻辑。
            if len(clicks) == 0:
                self.Visible_signal.emit(False)
                res = []
                total = 0

                op_num = []
                for i, j in clicks9:
                    op5x5_num = self.open_num5x5(cell_value, (i, j))
                    op_num.append(op5x5_num)

                np_op_num = np.array(op_num)
                np_op_num = np.where(np_op_num == max(np_op_num), 1, 0)
                p = np_op_num / np_op_num.sum()
                pos = [clicks9[np.random.choice(np.arange(len(clicks9)), p=p)]]

                confidence = 1 - (self.a - num10) / len(clicks9)
                if not self.is_play:
                    self.pos_dict_list.append(
                        {
                            "pos": pos[0],
                            "confidence": round(confidence, 5),
                            "num": self.num,
                            "is_mine": False,
                            "is_best": False,
                            "exp": "随机选择。",
                            "is_recommend": False,
                        }
                    )

            else:
                pos = []
                # ⚠️ limitation 不是"限制值"，而是**局面难度代理指标**（越大越难算）：
                #     len(clicks9) 项 = 无局部约束的内部格数（这些格会组合爆炸）
                #     log2(total) 项 = 世界总数的量级
                # 它与 wr_threshold（**策略参数**，"允许多难的局面上前瞻"）方向一致
                # 但含义不同，切勿混为一谈。
                # 另注：self.limit（不带 -ation）是完全不同的东西 = 组枚举上限；
                # 超过它的组会被放弃并降级并入 clicks9（见上方 is_removed 分支）。
                limitation = len(clicks9) * 0.8 + math.log2(total)
                logger.debug("limitation: %s", limitation)

                if _tb is not None:
                    # win_rate 阈值读取（持久化于 _tb["wr"]["t"]；更新发生在观测记录点 _wr_update）
                    wr_threshold = _wr_current_threshold(_tb)
                    logger.debug("wr_threshold=%s（limitation=%.2f）", wr_threshold, limitation)
                    # 决策级进度：进入决策段 [80,100]
                    self._pv_state["lo"], self._pv_state["hi"] = 80.0, 100.0

                if limitation <= wr_threshold and self._wr_memory_ok(
                        clicks, clicks9, total):
                    _t_wr = time.perf_counter()
                    # 是否有可信的 clicks2p（只有真的跑完 win_rate 才有）。
                    # node_cap 降级 / 走贪心时该变量不存在，后段会用到它算 confidence。
                    _wr_have_c2p = False
                    try:
                        win_rate, clicks, total, clicks2p = self.win_rate(
                            clicks, clicks9, res_list, cell_value, ck, num10)
                    except _WrNodeCapExceeded:
                        # 节点数超硬上限 → 本步降级为贪心分支（process_bigger_situation）。
                        # 这是**有意的**精度降级：win_rate 的 memo 只写不删，
                        # 节点数会组合爆炸；继续算就是把整机内存顶爆。
                        # 绝不让它落到外层 except（那会被当作"求解失败"→ 触发
                        # 完整重扫，反而更糟），也绝不回退纯 Python（同样无上限）。
                        pos, confidence, total, _prob = self.process_bigger_situation(
                            total, num9, num10, clicks, clicks9, res_list, ck,
                            cell_value, pos)
                        self.till_now_winrate *= confidence
                        self._wr_nodecap_hits += 1
                    else:
                        _wr_have_c2p = True
                        if _tb is not None:
                            # 实测反馈：边界采样且快 → +0.5；超时 → −0.5（见 _wr_update）
                            wr_threshold = _wr_update(_tb, limitation,
                                                      (time.perf_counter() - _t_wr) * 1000.0)
                            logger.debug("win_rate 实测 %.0f ms @limitation=%.2f → 阈值 %.1f",
                                         (time.perf_counter() - _t_wr) * 1000.0, limitation,
                                         wr_threshold)
                        win_rate = np.around(win_rate, decimals=5)
                        where_max = np.where(win_rate == np.max(win_rate), 1, 0)
                        p = where_max / where_max.sum()
                        arg = np.random.choice(np.arange(len(clicks)), p=p)
                        pos = [clicks[arg]]
                        confidence = win_rate[arg]
                        self.Visible_signal.emit(False)

                        if not self.is_play:
                            for p in range(len(clicks)):
                                if tuple(clicks[p]) not in self.appended_pos:
                                    if tuple(clicks[p]) == pos[0]:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": clicks[p],
                                                "confidence": round(win_rate[p], 5),
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"胜率计算得出",
                                                "is_recommend": True
                                            }
                                        )
                                        self.appended_pos.add(tuple(clicks[p]))
                                    else:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": clicks[p],
                                                "confidence": round(win_rate[p], 5),
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"胜率计算得出",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple(clicks[p]))

                        # 热力图：win_rate 的逐格"不是雷"概率（键与决策坐标同为 1-based）
                        _prob = {tuple(k): float(v) for k, v in clicks2p.items()}

                else:  # 大情况
                    pos, confidence, total, _prob = self.process_bigger_situation(
                        total, num9, num10, clicks, clicks9, res_list, ck, cell_value, pos)
                    self.till_now_winrate *= confidence

        logger.debug("决策完成：limitation=%.1f，总耗时 %.0f ms（预算 %.0fs；超时则检查上方各阶段）",
                     limitation,
                     (time.perf_counter() - t_decision_start) * 1000.0,
                     DECISION_TIME_BUDGET)
        # 组装热力图数据（自动模式界面渲染用）：候选格概率（1-based 坐标）
        # + 最佳点击 + 总局面数。_prob 已由各决策分支就地设置：
        #   win_rate 分支=clicks2p、pbs 分支=process_bigger_situation 第 4 元返回、
        #   随机分支=空表（界面用均匀先验兜底）。
        # ⚠️ 不要在这里重新判断 limitation <= wr_threshold：win_rate 观测会经
        # _wr_update 实时升降阈值，组装段可能因此走到与决策分支不同的路径；
        # 也不要从 zip(clicks, res) 重建——本作用域没有可靠的标量概率数组 res
        # （曾致 TypeError: only size-1 arrays can be converted）。
        self._last_heatmap = {
            "prob": _prob,
            "best": tuple(pos[0]) if len(pos) else None,
            "best_conf": float(confidence) if len(pos) else None,  # 当前点击格不是雷的概率/胜率
            "total": float(total),
        }

        for p in pos:
            if self.is_play:
                # pass
                pyautogui.click(
                    self.bx + p[0] * self.cell_width, self.by + p[1] * self.cell_width
                )

                time.sleep(0.1)

                if limitation <= wr_threshold:
                    # ⚠️ 只有真的走了 win_rate 分支才有 clicks2p。
                    # node_cap 降级（或 _wr_memory_ok 拦下走贪心）时该变量不存在，
                    # 直接下标会 UnboundLocalError（端到端负向测试实测抓到）。
                    # 降级时保留上面 process_bigger_situation 给出的 confidence。
                    if _wr_have_c2p and (p[0], p[1]) in clicks2p:
                        confidence = clicks2p[(p[0], p[1])]

                _index = confidence * 100
                _index = round(_index)
                _index = str(_index)
                _lose, _, _ = self._locate("./image/lose.bmp")

                data = self._load_stats()
                try:
                    if _lose:
                        data[_index]["lose"] += 1
                    else:
                        data[_index]["win"] += 1
                except KeyError:
                    if _lose:
                        data[_index] = {"lose": 1, "win": 0}
                    else:
                        data[_index] = {"lose": 0, "win": 1}

                self._stats_updates += 1
                if self._stats_updates >= 20:
                    self._flush_stats()
                    self._stats_updates = 0

        self.count = 0

        cell_value = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                cell_value[j, i] = 9
        cell_value = self.complete_scan(cell_value)

        return cell_value

    def try_solve(self, i, j, cell_value, clicks, num9, num10):
        res = 0

        for mine_num in range(num10, num10 + num9 + 1):
            if mine_num == 0:
                res += 8 * (1 - self.p) ** num9
                continue
            test_value = cell_value.copy()
            test_value[j, i] = mine_num

            resolved_count = 0
            try:
                for _ in range(2):
                    test_value = self.mine_clear1(test_value)
                    test_value = self.mine_clear3_1(test_value)
                test_value = self.mine_clear1(test_value)
                # count cells resolved by deduction (no longer 9)
                for n in range(j - 1, j + 2):
                    for m in range(i - 1, i + 2):
                        if cell_value[n, m] == 9 and test_value[n, m] != 9:
                            resolved_count += 1
            except Exception:
                pass

            # 计算结果
            res += (
                    resolved_count
                    * (1 - self.p) ** (num9 - mine_num + num10)
                    * (self.p) ** (mine_num - num10)
                    * C_num(num9, mine_num - num10)
            )

        res /= num9 + 1
        return res

    @staticmethod
    def _pbs_group_histograms(res_l):
        """把一组的解矩阵转成雷数直方图。

        返回 (H, Hc)：
          H[m]      = 该组雷数为 m 的解数
          Hc[c][m]  = 该组雷数为 m 且第 c 个格为雷的解数
        """
        n = 0 if len(res_l) == 0 else len(res_l[0])
        H = {}
        Hc = [{} for _ in range(n)]
        for row in res_l:
            r = np.asarray(row)
            m = int(r.sum())
            H[m] = H.get(m, 0) + 1
            for c in range(n):
                if r[c]:
                    Hc[c][m] = Hc[c].get(m, 0) + 1
        return H, Hc

    @staticmethod
    def _pbs_convolve(hists):
        """把多组雷数直方图卷积成全局分布 T(t) = #{Σm_g = t} 的加权计数。"""
        T = {0: 1}
        for H in hists:
            newT = {}
            for t, wt in T.items():
                for m, wm in H.items():
                    newT[t + m] = newT.get(t + m, 0) + wt * wm
            T = newT
        return T

    def _pbs_compute_dp(self, num10, clicks, clicks9, res_list):
        """生成函数 DP：正确的跨组概率聚合（修复"各组雷数独立"错误假设）。

        数学依据：全局可行世界的权重必须同时满足各组内部约束与"总雷数"耦合，
        即 W(m_1..m_G) = ∏H_g(m_g) · C(U, R - Σm_g)，其中 U 是无约束内部格数、
        R 是还需放置的雷数。旧的近似分支对每组独立归一化，丢掉了 C(U, R-Σm)
        这一跨组项，在组间雷数分布不均时会产生系统性偏差（实测可达数十个百分点）。

        返回 (res, mine_num, total)：
          res[c]  = 第 c 个前沿格"不是雷"的概率
          mine_num= 前沿期望雷数
          total   = 归一化常数（0 表示无可行世界）
        """
        U = len(clicks9)                       # 无约束内部格
        R = self.a - num10                     # 还需放置的雷数（含内部）
        n_frontier = len(clicks)

        hists = [self._pbs_group_histograms(rl) for rl in res_list]
        if not hists:
            return np.zeros(n_frontier, np.float32), 0.0, 0.0
        Hs = [h[0] for h in hists]
        Hcs = [h[1] for h in hists]
        T = self._pbs_convolve(Hs)

        def _feasible(t):
            return 0 <= (R - t) <= U

        denom = 0.0
        for t, wt in T.items():
            if _feasible(t):
                denom += wt * math.comb(U, R - t) if (R - t) >= 0 else 0.0
        if denom == 0.0:
            return np.zeros(n_frontier, np.float32), 0.0, 0.0

        res = np.zeros(n_frontier, np.float32)
        est_mines = 0.0
        pos = 0
        for gi, (H, Hc) in enumerate(zip(Hs, Hcs)):
            others = [h for k, h in enumerate(Hs) if k != gi]
            Tm = self._pbs_convolve(others)
            # 本组雷数期望（用于返回 mine_num）
            for m, wm in H.items():
                acc = 0.0
                for t, wt in Tm.items():
                    r = R - m - t
                    if 0 <= r <= U:
                        acc += wt * math.comb(U, r)
                est_mines += m * wm * acc
            for c in range(len(Hc)):
                num = 0.0
                for m, wmc in Hc[c].items():
                    for t, wt in Tm.items():
                        r = R - m - t
                        if 0 <= r <= U:
                            num += wmc * wt * math.comb(U, r)
                res[pos] = np.float32(1.0 - (num / denom))
                pos += 1
        mine_num = est_mines / denom
        return res, float(mine_num), float(denom)

    def _pbs_compute_python(self, total, num10, clicks, clicks9, res_list, ck):
        """process_bigger_situation 的纯 Python 计算核心（C++ 不可用/失败时的回退路径）。

        与 C++ mscore.pbs_compute 语义逐位一致；返回 (res, mine_num, total)。

        ⚠️ total > 10000 的"近似分支"历史上对每组独立归一化，忽略总雷数耦合，
        在组间雷数分布不均时产生系统性偏差（本仓实测可达数十个百分点）。
        现改为走 `_pbs_compute_dp` 的生成函数 DP（数学上精确，代价是 O(G·R²) 状态）。
        """
        if total > 10000:  # 组间笛卡尔积过大：不再逐世界枚举，改用生成函数 DP
            self.Visible_signal.emit(False)
            dp_res, dp_mine, dp_denom = self._pbs_compute_dp(
                num10, clicks, clicks9, res_list)
            if dp_denom == 0.0:
                return np.array([]), 0.0, total
            return dp_res, dp_mine, dp_denom

        res = []
        _total = 0
        min_val = self.a - len(clicks9)
        mine_num = []

        o_value = 0
        num = 0
        self._pv_mapped_emit(0)
        for index_list in A(ck):
            _mine_num = 0  # 一个方案中的雷数
            r = np.array([])
            for i in range(len(index_list)):
                _mine_num += res_list[i][index_list[i]].sum()
                r = np.hstack([r, res_list[i][index_list[i]]])

            if min_val <= (_mine_num + num10) <= self.a:
                mine_num.append(_mine_num)
                _total += 1
                res.append(r)
            n_value = int((num / total) * 100)
            if n_value - o_value >= 1:
                self._pv_mapped_emit(n_value)
                o_value = n_value
            num += 1

        self._pv_mapped_emit(100)
        self.Visible_signal.emit(False)
        total = 0
        estimated_mine_num = 0
        if not mine_num:
            # 兜底：A(ck) 的每一种组合都不满足雷数上下界 ⇒ 约束集自相矛盾
            # （典型场景：残局中雷基本已被标记、内部未开格很少，或设置里总雷数填错）。
            # 历史上这里直接 min([]) 抛 ValueError，且 C++ 回退路径抛同样的错，
            # 导致整个决策崩溃而非降级。改为退回到"均匀先验"：
            # 把剩余雷数均匀摊到全部未开格上，给出不偏不倚的建议并继续对局。
            _unknown = len(clicks) + len(clicks9)
            _p_mine = 0.0 if _unknown == 0 else min(
                1.0, max(0.0, (self.a - num10) / _unknown))
            logger.warning(
                "pbs 约束集自相矛盾（无可行雷数组合），退回均匀先验："
                "未开格=%d 剩余雷=%d p_mine=%.4f", _unknown, self.a - num10, _p_mine)
            return (np.full(len(clicks), 1.0 - _p_mine, dtype=np.float32),
                    _p_mine * len(clicks), 0.0)
        min_mine_cnt = min(mine_num)
        x_min = self.a - min_mine_cnt - num10
        __res = np.zeros(len(clicks), dtype=np.float32)

        for i in range(len(mine_num)):
            p = combination_ratio(self.a - mine_num[i] - num10, x_min, len(clicks9))
            estimated_mine_num += p * mine_num[i]
            if i == 0:
                __res = res[i].astype(np.float32) * p
            else:
                __res += res[i].astype(np.float32) * p
            total += p
        res = __res.copy()
        res = res / total
        res = 1 - res
        mine_num = estimated_mine_num / total

        return res, mine_num, total

    def process_bigger_situation(self, total, num9, num10, clicks, clicks9, res_list, ck, cell_value, pos):
        confidence = 0

        # 计算核心：优先 C++（mscore.pbs_compute），失败/不可用自动回退纯 Python
        if native.available:
            try:
                res, mine_num, total = native.mscore.pbs_compute(
                    total, num10, clicks, clicks9,
                    native.as_groups(res_list), ck, self.a,
                    self._pv_mapped_emit, self.Visible_signal.emit)
            except Exception as e:
                logger.warning("C++ pbs_compute 失败，回退纯 Python 实现：%s", e)
                res, mine_num, total = self._pbs_compute_python(
                    total, num10, clicks, clicks9, res_list, ck)
        else:
            res, mine_num, total = self._pbs_compute_python(
                total, num10, clicks, clicks9, res_list, ck)

        if 1 in res:  # 有确定不为雷的地方
            for index in range(len(res)):
                if res[index] >= 0.99:
                    pos.append(clicks[index])
                    confidence = 1
                    if not self.is_play:
                        if tuple(clicks[index]) not in self.appended_pos:
                            self.pos_dict_list.append(
                                {
                                    "pos": clicks[index],
                                    "num": self.num,
                                    "confidence": 1,
                                    "is_mine": False,
                                    "is_best": True,
                                    "exp": "枚举得出",
                                    "is_recommend": False,
                                }
                            )
                            self.appended_pos.add(tuple(clicks[index]))
        else:
            if len(res) == 0:
                cell_value = np.zeros((self.h + 2, self.w + 2), dtype="int32")
                for i in range(1, self.w + 1):
                    for j in range(1, self.h + 1):
                        cell_value[j, i] = 9
                cell_value = self.complete_scan(cell_value)
                self.count += 1
                return cell_value

            max_loc = np.argmax(res)
            max_val = res[max_loc]  # 最大值
            _a = np.arange(len(clicks))
            np.random.shuffle(_a)
            poses = []
            for p in _a:
                if 0.0005 >= max_val - res[p] >= -0.0005:
                    poses.append(clicks[p])

            op_num = []
            for i, j in poses:
                op5x5_num = self.open_num5x5(cell_value, (i, j))
                op_num.append(op5x5_num)

            np_op_num = np.array(op_num)
            np_op_num = np.where(np_op_num == max(np_op_num), 1, 0)
            p = np_op_num / np_op_num.sum()
            pos = [poses[np.random.choice(np.arange(len(poses)), p=p)]]

            mine9 = self.a - mine_num - num10  # 剩余雷数

            is_recommend = True
            confidence = round(max_val, 5)  # 不是雷的概率
            if len(clicks9) != 0:
                _confidence = round(1 - (mine9 / len(clicks9)), 5)
                if _confidence > confidence:  # 剩余未开方格不是雷的概率大于最大概率
                    is_recommend = False

                    op_num = []
                    for i, j in clicks9:
                        op5x5_num = self.open_num5x5(cell_value, (i, j))
                        op_num.append(op5x5_num)

                    np_op_num = np.array(op_num)
                    np_op_num = np.where(np_op_num == max(np_op_num), 1, 0)
                    p = np_op_num / np_op_num.sum()
                    pos = [clicks9[np.random.choice(np.arange(len(clicks9)), p=p)]]

                    opennum_res = np.zeros(len(clicks9))

                    if not self.is_play:
                        for k, (i, j) in enumerate(clicks9):
                            if (i, j) in pos:
                                self.pos_dict_list.append(
                                    {
                                        "pos": (i, j),
                                        "confidence": _confidence,
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": False,
                                        "exp": f"枚举得出, 预计可以确定的方格数：{round(opennum_res[k], 2)}",
                                        "is_recommend": True,
                                    }
                                )
                            else:
                                self.pos_dict_list.append(
                                    {
                                        "pos": (i, j),
                                        "confidence": _confidence,
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": False,
                                        "exp": f"枚举得出",
                                        "is_recommend": False,
                                    }
                                )
                    confidence = _confidence  # 剩余未开方格不是雷的概率
                    if confidence == 1:
                        pos = clicks9

            if not self.is_play:
                for p in range(len(clicks)):
                    if 0.005 >= max_val - res[p] >= -0.005:
                        if tuple(clicks[p]) not in self.appended_pos:
                            if tuple(clicks[p]) == pos[0]:
                                self.pos_dict_list.append(
                                    {
                                        "pos": clicks[p],
                                        "confidence": round(res[p], 5),
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": False,
                                        "exp": f"枚举得出",
                                        "is_recommend": True
                                        if is_recommend
                                        else False,
                                    }
                                )
                                self.appended_pos.add(tuple(clicks[p]))
                            else:
                                self.pos_dict_list.append(
                                    {
                                        "pos": clicks[p],
                                        "confidence": round(res[p], 5),
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": False,
                                        "exp": f"枚举得出",
                                        "is_recommend": False,
                                    }
                                )
                                self.appended_pos.add(tuple(clicks[p]))
                    else:
                        if tuple(clicks[p]) not in self.appended_pos:
                            self.pos_dict_list.append(
                                {
                                    "pos": clicks[p],
                                    "confidence": round(res[p], 5),
                                    "num": self.num,
                                    "is_mine": False,
                                    "is_best": False,
                                    "exp": f"枚举得出",
                                    "is_recommend": False,
                                }
                            )
                            self.appended_pos.add(tuple(clicks[p]))

        # 附带候选点击格概率字典（1-based 坐标 → 不是雷概率），供热力图渲染
        _prob = {tuple(c): float(r) for c, r in zip(clicks, res)}
        return pos, confidence, total, _prob

    def _wr_memory_ok(self, clicks, clicks9, total):
        """win_rate 的内存预算闸门：规模过大时直接降级，不进 win_rate。

        ==================== 为什么需要这道闸门 ====================
        `win_rate`（cpp/mscore.cpp 的 py_win_rate / wr_f）的成本结构是
            nworlds × 2304B × n_candidates × 递归层数
        （每个"世界"是整盘 (h+2)×(w+2)=576 int32 = 2304B；阶段2 对每个候选格
        把每个世界整盘拷一次，递归每层再拷一次）。这是**乘积**结构：世界数
        与候选格数一大，内存就呈 GB 级增长。

        生产实测（12 局，默认阈值）：win_rate 只触发 8 次、n_cand 中位数 13、
        世界数中位数 4、单次内存约 0.15MB —— **正常对局不会触发这道闸门**。
        它只在异常/极端局面（设置错误、残局约束自相矛盾、外部强行放宽
        wr_threshold）时兜底，避免把整机内存撑爆。

        必须**在调用前**判断，而不是依赖 C++ 抛异常，原因有三：
          ① C++ 抛异常后本文件会回退**纯 Python** 实现，那里没有上限、
             同样吃内存 —— 只拦 C++ 等于没拦；
          ② 异常抛在计算中途时已经分配过一部分内存，峰值仍可能顶爆；
          ③ 频繁抛异常 + logger.warning 会刷屏并拖慢决策。

        超限时返回 False，调用方走 process_bigger_situation（贪心最大安全率），
        这是**有意的降级**：少一点前瞻精度，换整机不被打爆。
        """
        n_cand = 1 + len(clicks) + len(clicks9)
        # 阶段2 峰值估算（与 cpp/mscore.cpp 的预算判断同一公式、同一上限）
        stage2_bytes = float(total) * float(n_cand) * 2304.0
        limit_bytes = 1536.0 * 1024.0 * 1024.0      # 1.5 GB
        if stage2_bytes <= limit_bytes:
            return True
        # 同一 (total, n_cand) 组合只警告一次，避免每步刷屏
        key = (int(total), int(n_cand))
        if key not in self._wr_mem_warned:
            self._wr_mem_warned.add(key)
            logger.warning(
                "win_rate 预计内存 %.0f MB 超限（total=%d，候选格=%d）→ "
                "本局改用贪心概率分支（前瞻降级，不影响正确性）",
                stage2_bytes / 1e6, total, n_cand)
        return False

    def win_rate(self, clicks, clicks9, res_list, cell_value: np.ndarray, ck, num10):
        """精确前瞻搜索（"把每种未来都玩一遍"），返回逐候选格的胜率。

        ==================== 入参（名字极易误解，逐条看）====================
          clicks   —— **前沿**未开格（受邻域数字约束）
          clicks9  —— **内部**未开格（只受剩余总雷数约束）。"9" 来自编码 9=未开
          res_list —— **每组的全部解**（不是"结果列表"）：
                      res_list[i] 形状 [该组解数 × 该组前沿格数]，元素 0/1
                      ⚠️ res_list[i] 的列**只对应第 i 组自己的前沿格**，不是全局 clicks；
                         跨组引用必须加组偏移（曾经的 bug 就出在这里）
          ck       —— **每组的解数**（ck[i] == len(res_list[i])），
                      total = ∏ck[i] = 世界总数。不是"检查"，也不是"每组格数"
          num10    —— **已标记为雷的格数**（cell_value==10 的格数）
          返回 (res, clicks_out, total, clicks2p)

        ==================== 返回值（三个高频误解）====================
          res       —— **逐候选格的胜率数组**，不是标量。要选格得自己 argmax
          clicks_out—— **已按 clicks2p 降序重排**后的候选格！顺序与入参不同，
                       按下标对齐入参 clicks 会错位；要匹配请按**坐标**
          total     —— 世界总数
          clicks2p  —— dict[(x,y)] → "该格不是雷"的概率（safety）。
                       按**排序前**的原始插入序构建，与 clicks_out 也是按坐标匹配

        ==================== 它不是"精确最优胜率" ====================
        cpp/mscore.cpp 的 wr_f 有四处与真实扫雷规则不符（详见仓库
        docs/变量名与算法细节.md 第 2.2 节），因此这是**启发式估值**，不是 V*。
        需要真值时用 bench/arms_lookahead.py 的 ExactEngine（忠实 flood-fill）。

        ==================== 降级语义 ====================
        节点数超 node_cap 时抛 _WrNodeCapExceeded，由 number5_1 捕获后
        **降级走 process_bigger_situation（贪心）**。绝不回退纯 Python
        （那里同样无上限，退回等于没拦）。
        """
        # 优先 C++（mscore.win_rate），失败/不可用自动回退纯 Python
        if native.available:
            try:
                _res, _clicks, _total, _clicks2p = native.mscore.win_rate(
                    clicks, clicks9, native.as_groups(res_list), cell_value, ck,
                    num10, self.a, self.w, self.h, self.is_play,
                    self._pv_mapped_emit, _wr_node_cap())
            except _WrNodeCapExceeded as e:
                # ⚠️ 节点数超硬上限：这**不能**回退到纯 Python 实现——
                # 纯 Python 的 f() 同样无上限，退过去等于没拦，正是此前
                # 把内存顶爆的路径。正确做法是抛给 number5_1 让它走
                # process_bigger_situation（贪心），即有意的精度降级。
                self._wr_nodecap_hits += 1
                logger.warning(
                    "win_rate 节点数超上限（%s）→ 本步降级为贪心分支"
                    "（前瞻精度降级，不崩溃、不影响正确性）", e)
                raise
            except Exception as e:
                # C++ 的 node cap 异常以 RuntimeError 形式回来（pybind11 转换），
                # 这里按消息识别并转成本模块的专用异常，使其走降级路径
                # 而不是"回退纯 Python"。识别失败才按普通失败处理。
                if "node cap exceeded" in str(e):
                    self._wr_nodecap_hits += 1
                    logger.warning(
                        "win_rate 节点数超上限（%s）→ 本步降级为贪心分支"
                        "（前瞻精度降级，不崩溃、不影响正确性）", e)
                    raise _WrNodeCapExceeded(str(e)) from e
                logger.warning("C++ win_rate 失败，回退纯 Python 实现：%s", e)
                # 纯 Python 回退同样吃内存，但它只在“C++ 抛异常”时走到，
                # 而调用前的 _wr_memory_ok 已按同一公式拦掉超限规模 ⇒
                # 能到这里的规模必然在预算内，无需重复设限。
                # 此处绝不能再抛异常：number5_1 外层会把任何异常当作
                # “求解失败”→ 触发完整重扫，那不是降级而是更糟的副作用。

        cell_value_list = []
        for index_list in A(ck):
            _cell_value = cell_value.copy()
            r = np.array([])
            for i in range(len(index_list)):
                r = np.hstack([r, res_list[i][index_list[i]]])

            _cell_value = cell_value.copy()
            for i in np.argwhere(r == 1):
                u, v = clicks[i[0]]
                _cell_value[v, u] = 10

            if self.a - num10 - sum(r) == 0:
                cell_value_list.append(_cell_value)
                continue
            if self.a > num10 + sum(r) + len(clicks9):
                continue

            gl = get_list(self.a - num10 - sum(r), self.a - num10 - sum(r), len(clicks9))
            next(gl)
            for index_l in gl:
                new_cell_value = _cell_value.copy()
                for j in index_l:
                    u, v = clicks9[j]
                    new_cell_value[v, u] = 10
                cell_value_list.append(new_cell_value)

        depth_limit = 200 / len(clicks)

        self.memory = {}

        def hash_cell_value(l: np.ndarray):
            __cell_value = np.where(l > 9, 9, l)
            str_cell_value = __cell_value.tostring()
            hashed_cell_value = hashlib.md5(str_cell_value).hexdigest()
            return hashed_cell_value

        def f(clicks: list, _cell_value_list, depth=1):
            assert len(_cell_value_list) > 0
            try:
                return self.memory[hash_cell_value(_cell_value_list[0])]
            except KeyError:
                pass

            if len(_cell_value_list) == 1:
                return 1
            if depth > depth_limit:  # 防止死循环
                return 1

            _np_cell_value_list = np.array(_cell_value_list)
            total = len(_cell_value_list)

            clicks2p = {}
            for u, v in clicks:
                clicks2p[(u, v)] = 1 - len(np.argwhere(_np_cell_value_list[:, v, u] == 10)) / total
            clicks = sorted(clicks, key=lambda x: clicks2p[x], reverse=True)

            _res = []
            for i in range(len(clicks)):
                if i > 1 and clicks2p[clicks[i]] < max(_res):  # 剪枝
                    continue

                win_p = 0
                u, v = clicks[i]
                new_cell_value_dict = defaultdict(list)
                for _cell_value in _cell_value_list:
                    if _cell_value[v, u] != 10:
                        new_cell_value = _cell_value.copy()
                        new_uv_value = self.cell_around(u, v, new_cell_value)[1]
                        new_cell_value[v, u] = new_uv_value
                        new_cell_value_dict[new_uv_value].append(new_cell_value)

                for new_uv_value, new_cell_value_list in new_cell_value_dict.items():
                    trans_prob = len(new_cell_value_list) / total
                    new_clicks = clicks.copy()
                    new_clicks.pop(i)
                    win_r = f(new_clicks, new_cell_value_list, depth + 1)
                    win_p += trans_prob * win_r
                _res.append(win_p)

            self.memory[hash_cell_value(_cell_value_list[0])] = max(_res)

            return max(_res)

        total = len(cell_value_list)
        clicks += clicks9
        res = []

        np_cell_value_list = np.array(cell_value_list)
        clicks2p = {}
        for u, v in clicks:
            clicks2p[(u, v)] = 1 - len(np.argwhere(np_cell_value_list[:, v, u] == 10)) / total
        clicks = sorted(clicks, key=lambda x: clicks2p[x], reverse=True)

        self._pv_mapped_emit(0)
        for i in range(len(clicks)):
            if i > 1 and clicks2p[clicks[i]] < max(res) and self.is_play:  # 剪枝
                res.append(0)
                continue

            win_p = 0
            u, v = clicks[i]
            new_cell_value_dict = defaultdict(list)
            for _cell_value in cell_value_list:
                if _cell_value[v, u] != 10:
                    new_cell_value = _cell_value.copy()
                    new_uv_value = self.cell_around(u, v, new_cell_value)[1]
                    new_cell_value[v, u] = new_uv_value
                    new_cell_value_dict[new_uv_value].append(new_cell_value)

            for new_uv_value, new_cell_value_list in new_cell_value_dict.items():
                trans_prob = len(new_cell_value_list) / total
                new_clicks = clicks.copy()
                new_clicks.pop(i)
                win_r = f(new_clicks, new_cell_value_list)
                win_p += trans_prob * win_r

            self._pv_mapped_emit(int((i + 1) / len(clicks) * 100))
            res.append(win_p)

        self.memory = {}

        return res, clicks, len(cell_value_list), clicks2p

    def open_num5x5(self, cell_value, pos):
        """
        计算5x5格子中已经打开的格子数
        :param cell_value:
        :param pos:
        :return:
        """
        x, y = pos
        value = cell_value.copy()
        res = 0
        for i in range(x - 2, x + 3):
            for j in range(y - 2, y + 3):
                if i < 1 or i > self.w or j < 1 or j > self.h:
                    res += 1
                    continue
                if 0 <= value[j, i] <= 8:
                    res += 1

        return res

    def part_solve_single(self, clicks, cell_value, num10, num9, cs, _try=True):
        """
        根据点击的坐标，计算出可能的值
        :param _try:
        :param clicks: 点击的坐标
        :param cell_value: 格子中的值
        :param num10: 10的个数
        :param num9: 9的个数
        :param cs: 雷的坐标
        :return: 可能的值
        """
        # 优先 C++（mscore.part_solve_single），失败/不可用自动回退纯 Python；
        # _try=True 路径依赖 try_solve（Python 侧逻辑），不加速
        if not _try and native.available:
            try:
                _arr, _num = native.mscore.part_solve_single(
                    clicks, cell_value, cs, num10, num9, self.a, self.w, self.h,
                    self._pv_mapped_emit)
                return list(_arr), int(_num), np.zeros(len(clicks))
            except Exception as e:
                logger.warning("C++ part_solve_single 失败，回退纯 Python 实现：%s", e)

        canopen_res = np.zeros(len(clicks))
        res_list = []
        list_getter = get_list(self.a - num10 - num9, self.a - num10, len(clicks))
        _total = next(list_getter)
        num = 0
        num_solve = 0
        o_value = 0
        self._pv_mapped_emit(0)
        for index_list in list_getter:
            # copy 防止改变原数组
            value = cell_value.copy()
            # 将尝试的坐标设为雷。
            for loc in index_list:
                value[clicks[loc][1], clicks[loc][0]] = 10

            flag = 0  # 0 符合条件 -1 不符合条件
            for i, j in cs:
                if value[j, i] != self.cell_around(i, j, value)[1]:
                    flag = -1
                    break

            res = np.zeros(len(clicks), dtype=np.int32)
            if flag == 0:  # 符合条件
                if _try:
                    for loc in set(range(len(clicks))) - set(index_list):
                        _value = cell_value.copy()
                        num9 = 0
                        num10 = 0
                        i, j = clicks[loc]
                        for u in range(i - 1, i + 2):
                            for v in range(j - 1, j + 2):
                                if value[v, u] == 9 and ((u, v) not in clicks):
                                    num9 += 1
                                elif value[v, u] == 10:
                                    num10 += 1
                        can_open = self.try_solve(i, j, _value, clicks, num9, num10)
                        canopen_res[loc] += can_open

                num_solve += 1
                for loc in index_list:
                    res[loc] += 1
                res_list.append(res)

            n_value = int((num / _total) * 100)
            if n_value - o_value >= 1:
                self._pv_mapped_emit(n_value)
                o_value = n_value
            num += 1

        # 没有雷的情况
        value = cell_value.copy()

        flag = 0
        for i, j in cs:
            if value[j, i] != self.cell_around(i, j, value)[1]:  # 不符合条件的
                flag = -1
                break

        if flag == 0:  # 符合条件
            res_list.append(np.zeros(len(clicks), dtype=np.int32))

        if num_solve != 0:
            canopen_res /= num_solve

        self._pv_mapped_emit(100)
        return res_list, len(res_list), canopen_res

    def part_solve(self, clicks, cell_value, num10, num9, cs, _try=False):
        """
        根据点击的坐标，计算出可能的值
        :param _try:
        :param clicks: 点击的坐标
        :param cell_value: 格子中的值
        :param num10: 10的个数
        :param num9: 9的个数
        :param cs: 雷的坐标
        :return: 可能的值
        """
        # 优先 C++（mscore.part_solve），失败/不可用自动回退纯 Python；
        # _try=True 路径依赖 try_solve（Python 侧逻辑），不加速
        if not _try and native.available:
            try:
                _arr, _num = native.mscore.part_solve(
                    clicks, cell_value, self.a, self.w, self.h,
                    self._pv_mapped_emit)
                return list(_arr), int(_num), np.zeros(len(clicks))
            except Exception as e:
                logger.warning("C++ part_solve 失败，回退纯 Python 实现：%s", e)

        _cs = defaultdict(list)
        for i, j in clicks:
            for u in range(i - 1, i + 2):
                for v in range(j - 1, j + 2):
                    if 1 <= cell_value[v, u] <= 8:
                        _cs[(i, j)].append((u, v))
        _cs = dict(_cs)

        clicks = list(clicks)
        logger.debug("part_solve 待枚举格子数: %s", len(clicks))

        def f(cell_value, state: list, clicks: list, res: list, completed=0, depth=1):
            if len(clicks) == 1:
                x, y = clicks[0]
                value = cell_value.copy()

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    if value[j, i] != self.cell_around(i, j, value)[1]:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(0)
                    _state = np.array(_state)
                    res.append(_state)

                completed += 1 / 2 ** depth

                value[y, x] = 10
                num10 = len(np.argwhere(value == 10))
                if num10 > self.a:
                    return res, completed

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    if value[j, i] != self.cell_around(i, j, value)[1]:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(1)
                    _state = np.array(_state)
                    res.append(_state)

                completed += 1 / 2 ** depth

                return res, completed

            else:
                x, y = clicks[0]
                _clicks = clicks.copy()
                _clicks.pop(0)
                value = cell_value.copy()

                value[y, x] = 11

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    _num9, _num10 = self.cell_around(i, j, value)
                    if value[j, i] > _num9 + _num10 or value[j, i] < _num10:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(0)
                    res, completed = f(value.copy(), _state, _clicks, res, completed, depth + 1)
                else:
                    completed += 1 / 2 ** depth

                self._pv_mapped_emit(int(completed * 100))

                value[y, x] = 10
                num10 = len(np.argwhere(value == 10))
                if num10 > self.a:
                    return res, completed

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    _num9, _num10 = self.cell_around(i, j, value)
                    if value[j, i] > _num9 + _num10 or value[j, i] < _num10:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(1)
                    res, completed = f(value.copy(), _state, _clicks, res, completed, depth + 1)
                else:
                    completed += 1 / 2 ** depth
                self._pv_mapped_emit(int(completed * 100))

                return res, completed

        canopen_res = np.zeros(len(clicks))
        # clicks = sorted(clicks, key=lambda x: x[1] + x[0])
        res_l, _ = f(cell_value, [], clicks, [])

        if _try:
            # 计算每个方格可以开的格子数
            for index_list in res_l:
                # copy 防止改变原数组
                value = cell_value.copy()
                # 将尝试的坐标设为雷。
                for loc in index_list:
                    value[clicks[loc][1], clicks[loc][0]] = 10

                for loc in set(range(len(clicks))) - set(index_list):
                    _value = cell_value.copy()
                    num9 = 0
                    num10 = 0
                    i, j = clicks[loc]
                    for u in range(i - 1, i + 2):
                        for v in range(j - 1, j + 2):
                            if value[v, u] == 9 and ((u, v) not in clicks):
                                num9 += 1
                            elif value[v, u] == 10:
                                num10 += 1
                    can_open = self.try_solve(i, j, _value, clicks, num9, num10)
                    canopen_res[loc] += can_open

        num_solve = len(res_l)
        if num_solve != 0:
            canopen_res /= num_solve

        return res_l, num_solve, canopen_res
