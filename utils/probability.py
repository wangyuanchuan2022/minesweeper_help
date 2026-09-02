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
import math
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
# part_solve 按组大小呈指数增长（每 +1 格约 ×2），用指数模型
#   t(L) ≈ base_ms · 2^(L - 33)
# 预测各组耗时，并按实测在线校准（指数平滑）；模型状态挂在 solver 实例
# （属性 _time_budget）上跨决策记忆。仅 native.tuned()（C++ 可用且未设
# MSW_NATIVE_TUNE=0）时启用，纯 Python 回退自动恢复原始静态行为。
# ---------------------------------------------------------------------------
DECISION_TIME_BUDGET = 30.0  # number5_1 单次决策总预算（秒，含视觉扫描）——用户确认的上限
_PS_REF_SIZE = 33            # 模型参考组大小（实测标定点：C++ 2.3ms / Python 976ms）
_PS_SAFETY = 2.0             # 预测安全系数（组形状差异可达 10x + 模型误差）
_PS_RESERVE_S = 3.0          # 枚举阶段为之后的 win_rate / pbs / 决策输出预留的时间（秒）
# win_rate 阈值不设上限（用户要求）：起点 6+2=8，由 _wr_feedback 按实测在预算内
# 自由升降——快则逐级上爬覆盖更大局面，慢（>预算×0.75）则降档回 pbs。


def _ps_default_base_ms():
    """模型初值：33 格组在当前模式下的典型耗时（毫秒）。

    C++ 取实测（2.3ms）的约 4 倍作保守初值（10ms），首次遭遇大组时宁可少算一点
    也不突破预算；随后由实测逐次校准收敛到真实值。Python 侧仅在 native 部分回退
    场景下由观测自校正，初值影响不大。
    """
    return 10.0 if native.available else 1500.0


def _ps_base_ms(state):
    """读取模型基准耗时；模式切换（C++↔纯 Python）时重置。"""
    base = state.get("ps_base_ms")
    if base is None or state.get("ps_native") is not native.available:
        base = _ps_default_base_ms()
        state["ps_native"] = native.available
    return base


def _ps_predict_ms(state, size):
    """预测 size 格组的 part_solve 耗时（毫秒）。"""
    return _ps_base_ms(state) * (2.0 ** max(size - _PS_REF_SIZE, -20)) * _PS_SAFETY


def _ps_observe(state, size, ms):
    """按实测耗时更新模型（指数平滑，新观测权重 0.5）。"""
    implied = ms / (2.0 ** max(size - _PS_REF_SIZE, -20))
    state["ps_base_ms"] = 0.5 * _ps_base_ms(state) + 0.5 * implied


def _budget_remaining(state, t_start):
    """距决策预算截止的剩余秒数（扣除枚举后阶段预留）。"""
    return t_start + DECISION_TIME_BUDGET - _PS_RESERVE_S - time.perf_counter()


def _wr_feedback(tb, t_start):
    """win_rate 阈值的实测反馈调节（native.tuned() 下启用，不设上限）。

    观测：最近一次真正调用 win_rate 的决策耗时（存于 tb["wr"]，见 number5_1）。
    规则（阈值只影响「limitation 萅过阈值的部分改走 pbs 近似」）：
      - 上次 win_rate 耗时 > 预算×0.75 → 阈值降到 max(6, 当前阈值−1)：win_rate
        拖垮了决策，回退到更省的 pbs；
      - 连续 3 次 win_rate 耗时 < 预算×0.25 → 阈值 +1：算力余量充足，让更优的
        win_rate 覆盖更大的局面（无上限，逐步上爬）；
      - 其余情形（预算×0.25 ~ ×0.75 之间）保持不变——控制器自然稳定在该区间。
    阈值持久化在 tb["wr"]["t"] 中，跨决策记忆。纯 Python（native 不可用）不调用
    本函数，阈值恒 6。
    """
    th = 6 + native.LIMIT_BONUS_WIN_RATE  # 起点：6+2=8
    wr = tb.get("wr")
    if wr is None:
        return th
    th = wr.get("t", th)
    budget_ms = DECISION_TIME_BUDGET * 1000.0
    if wr["ms"] > budget_ms * 0.75:            # 上次 win_rate 超预算 → 惩罚降档
        th = max(6, th - 1)
        tb["wr"]["fast_streak"] = 0
    elif wr["ms"] < budget_ms * 0.25:          # 上次很快 → 累计，3 次后奖励升档（无上限）
        fast_streak = wr.get("fast_streak", 0) + 1
        if fast_streak >= 3:
            th += 1
            fast_streak = 0
        tb["wr"]["fast_streak"] = fast_streak
    return th


class ProbabilityMixin:
    def number5_1(self, cell_value):
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
            self._time_budget = {}
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
            res_list = []
            canopen_res = np.array([])
            ck = []  # res_list中res的长度
            total = 1

            self.Visible_signal.emit(True)

            is_removed = False
            for index in range(len(click_list)):
                # 运算
                try:
                    self.pv_signal.emit(0)
                    _res, _canopen_res = self.checked[tuple(click_list[index])]
                    _total = len(_res)
                    canopen_res = np.hstack((canopen_res, _canopen_res))
                    total *= _total
                    res_list.append(_res)
                    ck.append(_total)
                    self.pv_signal.emit(100)
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
                    # win_rate 阈值实测反馈（见 _wr_feedback）：超预算降档、连续快则升档
                    wr_threshold = _wr_feedback(_tb, t_decision_start)
                    logger.debug("wr_threshold=%s（limitation=%.2f）", wr_threshold, limitation)

                if limitation <= wr_threshold:  # 小情况可以计算胜率
                    _t_wr = time.perf_counter()
                    win_rate, clicks, total, clicks2p = self.win_rate(clicks, clicks9, res_list, cell_value, ck, num10)
                    if _tb is not None:
                        # 记录本次 win_rate 观测（保留 fast_streak 字段，供下次 _wr_feedback 使用）
                        _wr_d = _tb.setdefault("wr", {})
                        _wr_d["t"] = wr_threshold
                        _wr_d["ms"] = (time.perf_counter() - _t_wr) * 1000.0
                    win_rate = np.around(win_rate, decimals=5)
                    self.text_signal.emit(f"此局面下的胜率为{max(win_rate): 0.4f}。\n")
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

                else:  # 大情况
                    pos, confidence, total = self.process_bigger_situation(total, num9, num10, clicks, clicks9,
                                                                           res_list, ck, cell_value, pos)
                    self.till_now_winrate *= confidence
                    self.text_signal.emit(f"走到此局面，还没死的概率为{self.till_now_winrate: 0.4f}。\n")

        logger.debug("决策完成：limitation=%.1f，总耗时 %.0f ms（预算 %.0fs；超时则检查上方各阶段）",
                     limitation,
                     (time.perf_counter() - t_decision_start) * 1000.0,
                     DECISION_TIME_BUDGET)
        self.text_signal.emit(f"共{total: 0.2f}种解。")
        if total == 0:
            self.text_signal.emit("随机选择。\n")
            self.text_signal.emit("您可以通过增加设置中的limit使枚举更加全面，但limit每增加1计算所需的时间增加1倍")
        else:
            self.text_signal.emit("\n")
        self.text_signal.emit(str(pos))
        self.text_signal.emit(f" confidence: {(confidence * 100): 0.2f}%\n")

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
        self._throttled_pv_signal_emit(0)
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
                self._throttled_pv_signal_emit(n_value)
                o_value = n_value
            num += 1

        self._throttled_pv_signal_emit(100)
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
                    self._throttled_pv_signal_emit, self.Visible_signal.emit)
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

        return pos, confidence, total

    def win_rate(self, clicks, clicks9, res_list, cell_value: np.ndarray, ck, num10):
        # 优先 C++（mscore.win_rate），失败/不可用自动回退纯 Python
        if native.available:
            try:
                _res, _clicks, _total, _clicks2p = native.mscore.win_rate(
                    clicks, clicks9, native.as_groups(res_list), cell_value, ck,
                    num10, self.a, self.w, self.h, self.is_play,
                    self._throttled_pv_signal_emit)
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

        self._throttled_pv_signal_emit(0)
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

            self._throttled_pv_signal_emit(int((i + 1) / len(clicks) * 100))
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
                    self._throttled_pv_signal_emit)
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
        self._throttled_pv_signal_emit(0)
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
                self._throttled_pv_signal_emit(n_value)
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

        self._throttled_pv_signal_emit(100)
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
                    self._throttled_pv_signal_emit)
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

                self._throttled_pv_signal_emit(int(completed * 100))

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
                self._throttled_pv_signal_emit(int(completed * 100))

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
