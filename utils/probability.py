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
    """把一次实测 (size, ms) 样本记录到 data_time.json（标准 JSON 数组）。

    供自动拟合增长因子用（_ps_refit_k）；过滤明显非真实的观测（非有限值/
    亚毫秒构造值）。读-改-写整文件并原子替换（文件始终是合法 JSON），写失败
    仅记日志，绝不影响决策主流程。成功返回最新样本列表（供拟合复用），
    被过滤或失败返回 None。
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
    k = _ps_fit_k(rows[:], native.available)
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

        num9 = 0  # 所有未开方格的数量
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

        clicks = []  # 外围未开方格
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
                limitation = len(clicks9) * 0.8 + math.log2(total)
                logger.debug("limitation: %s", limitation)

                if _tb is not None:
                    # win_rate 阈值读取（持久化于 _tb["wr"]["t"]；更新发生在观测记录点 _wr_update）
                    wr_threshold = _wr_current_threshold(_tb)
                    logger.debug("wr_threshold=%s（limitation=%.2f）", wr_threshold, limitation)
                    # 决策级进度：进入决策段 [80,100]
                    self._pv_state["lo"], self._pv_state["hi"] = 80.0, 100.0

                if limitation <= wr_threshold:  # 小情况可以计算胜率
                    _t_wr = time.perf_counter()
                    win_rate, clicks, total, clicks2p = self.win_rate(clicks, clicks9, res_list, cell_value, ck, num10)
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

    def _pbs_compute_python(self, total, num10, clicks, clicks9, res_list, ck):
        """process_bigger_situation 的纯 Python 计算核心（C++ 不可用/失败时的回退路径）。

        与 C++ mscore.pbs_compute 语义逐位一致；返回 (res, mine_num, total)。
        """
        if total > 10000:  # total太大全排列计算量太大
            self.Visible_signal.emit(False)
            mine_num = 0
            res = np.array([])
            for res_l in res_list:
                estimated_mine_num = 0
                min_mine_cnt = min([sum(x) for x in res_l])
                _all = len(set(clicks) | set(clicks9)) - len(res_l[0])
                _all = int(_all)
                x_min = self.a - min_mine_cnt - num10
                _total = 0
                _res_s = []
                for _res in res_l:
                    _mine_num = sum(_res)
                    p = combination_ratio(self.a - _mine_num - num10, x_min, _all)
                    __res = _res * p
                    estimated_mine_num += p * _mine_num
                    _res_s.append(__res)
                    _total += p
                res_l = np.array(_res_s)
                res_l = res_l.sum(axis=0)
                mine_num += estimated_mine_num / _total
                res_l /= _total
                res_l = 1 - res_l
                res = np.hstack((res, res_l))
            return res, mine_num, total

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

    def win_rate(self, clicks, clicks9, res_list, cell_value: np.ndarray, ck, num10):
        # 优先 C++（mscore.win_rate），失败/不可用自动回退纯 Python
        if native.available:
            try:
                _res, _clicks, _total, _clicks2p = native.mscore.win_rate(
                    clicks, clicks9, native.as_groups(res_list), cell_value, ck,
                    num10, self.a, self.w, self.h, self.is_play,
                    self._pv_mapped_emit)
                self.memory = {}
                return list(_res), list(_clicks), int(_total), dict(_clicks2p)
            except Exception as e:
                logger.warning("C++ win_rate 失败，回退纯 Python 实现：%s", e)

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
