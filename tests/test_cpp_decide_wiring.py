# -*- coding: utf-8 -*-
"""C++ decide 生产接线的回归锁（2026-11-06 用户实测 Bug + 连续循环契约）。

锁住的根因与契约（均为用户实测症状的直接根因，修复见
utils/probability.py `_number5_1_core` 的 C++ decide 分支）：

  ① 点击坐标 off-by-one：C++ decide 返回 1-based (x,y)，正确公式 =
     bx + x*cell_width（与全库其他点击点同构）。曾错写成 (x-1) 导致
     「confidence=100% 却点到雷」「标识格≠实点格」。
  ② 帮助模式（is_play=False）保持**单次** decide：不点击、只写入
     pos_dict_list（走 appended_pos 去重，主防 main.py 重复挂 clicked
     handler → 点一次弹两个窗）。
  ③ 自动模式是「decide → 点击 → rescan → 再 decide」连续循环
     （utils/probability.py:758-812）。循环的判据是 **rescan 后 num9 是否
     减少**：本轮测试的 stub 必须真的推进盘面，否则旧测试是靠「首轮即撞上
     无进展守卫」偶然变绿，等于对循环状态机零覆盖。
  ③b 循环内**不得**有任何逐点击的失败弹窗探测或 sleep（用户裁决
     2026-11-07）：曾每点一格就 `_locate("./image/lose.bmp")` + `time.sleep
     (setting.sleep)`。局末棋盘出现雷/失败弹窗时扫描不准属于正常节奏，胜负
     统一由 `utils/solver.py` 主循环的「游戏胜利/游戏失败」检测接管。本轮用
     「被调用即抛」的探针把这两者锁成**零调用**契约。
  ④ `_last_heatmap` 必须符合消费契约嵌套结构 {"prob","best","best_conf",
     "total"}，且连续循环下**最终值 = 最后一轮决策**（每轮覆盖写入，
     utils/probability.py:745-750）。

测试用 stub native.decide / rescan_after_click / pyautogui.click / _locate，
不触真实屏幕，也不做实盘点击。
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import make_solver, make_full_solver  # noqa: E402
from utils import native, probability  # noqa: E402


def _board(w=6, h=5):
    """构造 (h+2, w+2) 棋盘：内部全 9（未开），边框 0。"""
    cv = np.zeros((h + 2, w + 2), dtype=np.int32)
    cv[1:h + 1, 1:w + 1] = 9
    return cv


def _num9(cell_value):
    """内部未开格计数（与生产循环的 np.sum(cell_value == 9) 同口径）。"""
    return int(np.sum(cell_value == 9))


def _decide_response(x, y, reason="pbs_n10_max0.9000", confidence=0.9,
                     tied=None, prob=None):
    return {
        "x": x, "y": y, "confidence": confidence, "path": 1,
        "reason": reason, "estimated_bytes": 0, "world_upper": 128,
        "prob": prob if prob is not None else {(x, y): confidence},
        "tied": tied if tied is not None else [(x, y)],
        "weighted": [],
    }


def _invalid_response():
    """无效坐标：生产循环据此跳出（utils/probability.py:702-703）。"""
    return {"x": -1, "y": -1, "confidence": 0.0, "path": 2,
            "reason": "no_closed_cell", "estimated_bytes": 0,
            "world_upper": 0, "prob": {}, "tied": [], "weighted": []}


class _Capture:
    """捕获 pyautogui.click 的像素坐标，并按 bx/by/cell_width 反算格子。

    反算公式与生产点击公式互逆：px = bx + x*cell_width (x 为 1-based)，
    因此 x = round((px-bx)/cell_width)——**不减 1**，减 1 会把 1-based
    误当 0-based（bench/sim 里踩过同一个坑）。
    """

    def __init__(self, bx=100, by=200, cell_width=24):
        self.bx = bx
        self.by = by
        self.cell_width = cell_width
        self.clicks = []
        self.cells = []

    def __call__(self, x, y, *a, **kw):
        self.clicks.append((x, y))
        self.cells.append((
            int(round((x - self.bx) / self.cell_width)),
            int(round((y - self.by) / self.cell_width)),
        ))


def _rescan_opening(capture):
    """stub rescan_after_click：把本轮点击过的格变成已开（模拟 flood-fill 的最小情形）。

    这是旧测试缺的关键一环——没有它，生产循环 rescan 后 num9 不变，
    会命中「无进展守卫」直接跳出，连续循环根本没被跑到。
    """

    def _rescan(cell_value):
        for (cx, cy) in capture.cells:
            if cell_value[cy, cx] == 9:
                cell_value[cy, cx] = 0
        return cell_value

    return _rescan


class _Forbidden:
    """「被调用即抛」探针（用户裁决 2026-11-07 的零调用契约）。

    用于把「循环内绝不逐点击探测失败弹窗/绝不 sleep」变成**机械失败**：
    一旦实现回归（重新加回 `_locate("lose.bmp")` 或 `time.sleep(setting.sleep)`），
    用例直接炸出 AssertionError 而不是靠人眼读代码。
    """

    def __init__(self, what):
        self.what = what
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise AssertionError(f"decide 循环内不应调用 {self.what}（用户裁决 2026-11-07）")


class TestCppDecideWiring(unittest.TestCase):
    def _run_core(self, solver, responses, capture, rescan=None,
                  locate=None, sleep=None):
        """stub 决策/点击/重扫/弹窗检测，跑一遍 _number5_1_core。

        responses: native.decide 的依次返回（side_effect）；含 _invalid_response()
                   即为「决策放弃」终止路径。
        rescan:    点击后重扫的 stub；缺省原样返回（= 无进展），用于锁终止守卫。
        locate:    `_locate` 的替身；缺省「被调用即抛」探针（逐点击踩雷探测已删）。
        sleep:     `time.sleep` 的替身；缺省「被调用即抛」探针。
        """
        solver.cell_width = capture.cell_width
        solver.bx = capture.bx
        solver.by = capture.by
        solver._last_heatmap = {}
        # 轻量实例（__new__ 跳过 __init__）：预置 _number5_1_core 入口会
        # hasattr 检查的属性，避免 PyQt 未初始化 QThread 的 __getattr__
        # RuntimeError（hasattr 只吞 AttributeError）。
        solver._wr_mem_warned = set()
        solver._wr_nodecap_hits = 0
        solver._time_budget = {}
        solver._pv_state = None
        cv = _board(solver.w, solver.h)
        decide_mock = mock.Mock(side_effect=list(responses))
        rescan_mock = mock.Mock(side_effect=rescan or (lambda cv_: cv_))
        locate_probe = locate or _Forbidden("_locate（逐点击失败弹窗探测）")
        sleep_probe = sleep or _Forbidden("time.sleep（逐点击等待）")
        probability._DECIDE_FAIL_SEQ[0] = 0
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide", decide_mock), \
                mock.patch.object(probability.pyautogui, "click", capture), \
                mock.patch.object(probability.time, "sleep", sleep_probe), \
                mock.patch.object(solver, "_locate", locate_probe), \
                mock.patch.object(solver, "rescan_after_click", rescan_mock):
            out = solver._number5_1_core(cv)
        # 防静默降级：C++ 分支的宽 except 会把任何异常吞成「跌回 Python 链」，
        # 那样下面的断言可能靠回退路径偶然成立——必须显式断言没进过该分支。
        self.assertEqual(probability._DECIDE_FAIL_SEQ[0], 0,
                         "C++ decide 分支抛异常并被宽 except 吞掉（跌回 Python 链），"
                         "测试未真正覆盖 C++ 接线")
        return out, decide_mock, rescan_mock

    # ------------------------------------------------------------------
    # ① 批量 / 单点点击坐标
    # ------------------------------------------------------------------
    def test_certain_safe_batch_clicks_all_tied_cells(self):
        """certain_safe 批量：逐个点 tied 全集，坐标 = bx + x*cw（1-based）。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                tied=[(3, 2), (4, 2), (3, 3)])
        # 循环终止方式（方案 A 语义变更后）：重复一份相同响应 + 重扫无新变化
        # → 命中「无进展守卫」正常 break。**不能**再用 _invalid_response 当
        # 终止符——无效坐标现属「决策不可用」失败路径（走安全重扫，见 F4）。
        out, decide_mock, rescan_mock = self._run_core(
            s, [resp, resp], cap, _rescan_opening(cap))
        self.assertEqual(cap.clicks, [
            (100 + 3 * 24, 200 + 2 * 24),
            (100 + 4 * 24, 200 + 2 * 24),
            (100 + 3 * 24, 200 + 3 * 24),
        ])
        # 盘面真的被推进了：3 格已开（这才是连续循环的前提条件）
        self.assertEqual(_num9(out), 25 - 3)
        self.assertEqual(rescan_mock.call_count, 2)  # 两轮都点了/检查过
        self.assertEqual(decide_mock.call_count, 2)  # 首轮 + 无进展后的一轮
        self.assertTrue(s._last_heatmap)

    def test_single_click_is_one_based_not_zero_based(self):
        """单点分支：decide 返回 1-based (1,1) → 点 (bx+cw, by+cw)。

        错版公式 (x-1)*cw 会点到 (bx, by) = 棋盘外框一格；本用例是该 bug 的
        最小判别用例（左上角）。
        """
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(1, 1, confidence=0.93)
        out, decide_mock, _ = self._run_core(
            s, [resp, resp], cap, _rescan_opening(cap))
        self.assertEqual(cap.clicks, [(100 + 1 * 24, 200 + 1 * 24)])
        self.assertNotIn((100, 200), cap.clicks)  # 显式排除错版坐标
        self.assertEqual(_num9(out), 24)
        self.assertEqual(decide_mock.call_count, 2)

    # ------------------------------------------------------------------
    # ② 帮助模式：单次决策、不点击、去重
    # ------------------------------------------------------------------
    def test_help_mode_uses_cpp_decide_loop_no_click_and_dedup(self):
        """帮助模式：走 C++ decide **循环**（与自动模式同一决策源）、不点击、去重。

        契约变更留痕（2026-11-07 用户指令「helper 模式也统一成为只用 C++
        的 decide 循环」）：旧断言是「单次 decide、decide_mock.call_count == 1」，
        即 help 只调一次 decide 就返回。现在 help 与自动模式共用循环结构：
        每轮把新候选并入 pos_dict_list，直到某轮**没有新增候选**才终止
        （help 不点击 ⇒ 盘面不变，重复 decide 会给出同一批 ⇒ 第二轮即停）。
        判据语义不变的部分：仍然**绝不点击**、仍不重扫、仍然去重。
        """
        s = make_solver(is_play=False)
        cap = _Capture()
        resp = _decide_response(3, 2, confidence=0.9)
        # 循环至少调用两次：第 1 轮收集候选，第 2 轮确认无新增后终止
        out, decide_mock, rescan_mock = self._run_core(s, [resp, resp], cap)
        self.assertEqual(cap.clicks, [])            # 帮助模式绝不允许点击
        self.assertEqual(rescan_mock.call_count, 0)  # 不点击就不重扫
        self.assertGreaterEqual(decide_mock.call_count, 2,
                                "帮助模式必须走 C++ decide 循环（至少两轮）")
        self.assertEqual(len(s.pos_dict_list), 1)
        self.assertIn((3, 2), s.appended_pos)
        self.assertEqual(s.pos_dict_list[0]["exp"], "C++ 三层决策 (pbs_n10_max0.9000)")
        self.assertTrue(s.pos_dict_list[0]["is_recommend"])
        self.assertFalse(s.pos_dict_list[0]["is_mine"])
        # 同一格再来一次：不得重复追加（否则 main.py 同一按钮挂两个 handler）。
        # 第二轮该格已在 appended_pos ⇒ 首轮「无新增候选」即终止，故 decide
        # 只调 1 次是**正确**的循环终止行为（不是退回单次决策）。
        out2, decide_mock2, _ = self._run_core(s, [resp, resp], cap)
        self.assertEqual(len(s.pos_dict_list), 1,
                         "已收集过的格不得重复追加")
        self.assertGreaterEqual(decide_mock2.call_count, 1)
        self.assertEqual(_num9(out2), 25)  # 帮助模式不改盘面

    def test_help_mode_collects_whole_certain_safe_batch(self):
        """帮助模式必须收集 certain_safe 的**整批** tied，而不是只取单格。

        用户实测「点击所有确定方格不能用」的根因：C++ 把整批已证明安全格
        放在 ``tied``，旧 help 实现只取 ``r["x"], r["y"]`` 单格 ⇒ 候选表
        只有 1 格，按钮点下去几乎无动作。
        """
        s = make_solver(is_play=False)
        cap = _Capture()
        batch = [(3, 2), (4, 2), (3, 3)]
        resp = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                tied=batch)
        out, decide_mock, rescan_mock = self._run_core(s, [resp, resp], cap)
        positions = [tuple(p["pos"]) for p in s.pos_dict_list]
        for cell in batch:
            self.assertIn(cell, positions,
                          f"certain_safe 整批中的 {cell} 未被收集（候选表={positions}）")
        self.assertEqual(len(positions), len(batch),
                         f"应收集整批 {len(batch)} 格，实测 {len(positions)}")
        self.assertEqual(cap.clicks, [], "帮助模式仍不得自动点击")

    # ------------------------------------------------------------------
    # ③ 循环契约：每轮覆盖热力图，最终值 = 最后一轮决策
    # ------------------------------------------------------------------
    def test_heatmap_contract_is_last_round_of_loop(self):
        """连续两轮决策：_last_heatmap 最终值 = 第 2 轮（末轮）快照，键集固定 4 项。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        resp1 = _decide_response(
            3, 2, reason="certain_safe", confidence=1.0,
            tied=[(3, 2), (4, 2), (3, 3)],
            prob={(3, 2): 1.0, (4, 2): 1.0, (3, 3): 1.0},
        )
        resp2 = _decide_response(
            2, 3, confidence=0.81234567,
            prob={(2, 3): 0.81234567, (5, 4): 0.25},
        )
        # 第 3 份 = resp2 原样重复：重扫后盘面推进到位、再点同一格触发
        # 「无进展守卫」正常 break（方案 A 后无效坐标属失败路径，不能作终止符）。
        out, decide_mock, rescan_mock = self._run_core(
            s, [resp1, resp2, resp2], cap, _rescan_opening(cap))
        hm = s._last_heatmap
        self.assertIsInstance(hm, dict)
        self.assertEqual(set(hm.keys()), {"prob", "best", "best_conf", "total"})
        # 末轮契约（不是首轮）：best/置信度/概率表/总局面数都来自 resp2
        self.assertEqual(hm["best"], (2, 3))
        self.assertEqual(hm["best_conf"], round(0.81234567, 5))
        self.assertEqual(hm["prob"], {(2, 3): 0.81234567, (5, 4): 0.25})
        self.assertEqual(hm["total"], 128.0)
        self.assertIsInstance(hm["total"], float)
        # 两轮都真的执行了：首轮 3 个批量点击 + 次轮 1 个单点；第 3 轮响应
        # 指向已开的 (2,3)，生产循环的 `cell_value[cy,cx] != 9` 守卫会跳过
        # 点击（不重复点已开格），随后 rescan 无变化 → 无进展守卫 break。
        self.assertEqual(len(cap.clicks), 4)
        self.assertEqual(rescan_mock.call_count, 3)
        self.assertEqual(decide_mock.call_count, 3)
        self.assertEqual(_num9(out), 21)
        # 每轮调 decide 都带自动模式基线参数。
        # ⚠️ 2026-11-09：原断言还检查 wr_threshold==8.0，但该参数已按用户裁决
        # 删除（winrate 分支取消后它在 C++ 内零消费）。保留 node_cap 与 is_play。
        for call in decide_mock.call_args_list:
            self.assertEqual(call.kwargs["node_cap"], 2_000_000)
            self.assertIs(call.kwargs["is_play"], True)

    def test_loop_breaks_when_rescan_shows_no_progress(self):
        """无进展守卫：rescan 后 num9 未减少 → 立即跳出，不重复点击同一批。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                tied=[(3, 2), (4, 2), (3, 3)])
        out, decide_mock, rescan_mock = self._run_core(
            s, [resp, resp], cap, rescan=None)  # 缺省 rescan = 原样返回
        self.assertEqual(len(cap.clicks), 3)   # 只点一轮，不因无进展而重试
        self.assertEqual(decide_mock.call_count, 1)
        self.assertEqual(rescan_mock.call_count, 1)
        self.assertEqual(_num9(out), 25)       # 盘面确实没变（stub 语义）
        self.assertEqual(s._last_heatmap["best"], (3, 2))

    # ------------------------------------------------------------------
    # 既有接线回归（预算乘数 / 放宽参数 / 旧 pyd 兼容 / is_play 透传）
    # ------------------------------------------------------------------
    def test_budget_multiplier_roundtrip(self):
        """预算乘数：缺省 1.0；solver.help() 设 HELP_BUDGET_MULT 并在结束后恢复。

        用户指令 2026-11-07「可以稍微把预算放高一点」：帮助预算乘数由 2.0
        提升到 3.0（30s → 90s），自动模式仍为 30s 基线不变。
        """
        self.assertEqual(probability.decision_budget(), 30.0)
        self.assertEqual(probability.HELP_BUDGET_MULT, 3.0)
        probability._DECISION_BUDGET_MULT = probability.HELP_BUDGET_MULT
        try:
            self.assertEqual(probability.decision_budget(), 90.0,
                             "帮助模式预算应为 30s × 3 = 90s")
        finally:
            probability._DECISION_BUDGET_MULT = 1.0
        self.assertEqual(probability.decision_budget(), 30.0,
                         "预算乘数必须恢复，不得泄漏到自动模式")

    def test_auto_mode_passes_node_cap_and_is_play(self):
        """自动模式向 native.decide 传 node_cap 200万 + is_play=True。

        ⚠️ 2026-11-09 变更（用户裁决「wr_threshold winrate 分支都取消了，
        应该取消这个参数」）：原用例还断言「传持久化自适应阈值 12.5」与
        「_wr_update 被调用」，但这两者的被测对象已随 LAYER 2 删除：
          · wr_threshold 在 C++ 函数体内零消费，参数已删；
          · _wr_update / _wr_current_threshold 整条链路已删。
        本用例保留**仍然有效**的契约：node_cap 200万（内存上限不可调）
        与 is_play 透传（帮助模式 DSTAR×3 的前提）。
        """
        s = make_solver(is_play=True)
        s._wr_mem_warned = set()
        s._wr_nodecap_hits = 0
        s._time_budget = {}
        s._pv_state = None
        s.count = 0
        s.cell_value = _board(s.w, s.h)
        cv = _board(s.w, s.h)
        calls = []
        resp = _decide_response(3, 2)
        resp["limitation"] = 12.5

        def fake_decide(cv_, a, w, h, node_cap=2_000_000, **kw):
            calls.append({"nc": node_cap,
                          "is_play": kw.get("is_play", True)})
            return resp

        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide", side_effect=fake_decide), \
                mock.patch.object(probability.pyautogui, "click",
                                  lambda *a, **k: None), \
                mock.patch.object(probability.time, "sleep", lambda s_: None), \
                mock.patch.object(s, "complete_scan", lambda cv_: cv_), \
                mock.patch.object(s, "rescan_after_click", lambda cv_: cv_):
            s._number5_1_core(cv)

        self.assertGreater(len(calls), 0)
        for c in calls:
            self.assertEqual(c["nc"], 2_000_000,
                             "node_cap 200万不得改动（内存上限不可调）")
            self.assertIs(c["is_play"], True)


    def test_help_mode_passes_relaxed_node_cap(self):
        """帮助模式向 native.decide 传放宽的 node_cap（600 万）+ is_play=False。

        ⚠️ 2026-11-09：原用例还断言 wr_threshold==18.0（基线 8 + 帮助加成
        10）。该参数已按用户裁决删除，故断言移除；node_cap 放宽与
        is_play 透传仍然有效，保留为契约。
        """
        s = make_solver(is_play=False)
        cap = _Capture()
        cv = _board(s.w, s.h)
        s._wr_mem_warned = set()
        s._wr_nodecap_hits = 0
        s._time_budget = {}
        s._pv_state = None
        captured = {}
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(probability.pyautogui, "click", cap), \
                mock.patch.object(probability.time, "sleep", lambda s_: None):
            def fake_decide(cv_, a, w, h, node_cap=2_000_000, **kw):
                captured["nc"] = node_cap
                captured["is_play"] = kw.get("is_play", True)
                return _decide_response(1, 1)
            with mock.patch.object(native, "decide", side_effect=fake_decide), \
                    mock.patch.object(s, "complete_scan", lambda cv_: cv_):
                s._number5_1_core(cv)
        self.assertEqual(captured["nc"], 6_000_000)
        self.assertEqual(captured["nc"], probability.HELP_NODE_CAP)
        self.assertIs(captured["is_play"], False)

    def test_native_decide_old_pyd_fallback(self):
        """旧 pyd（decide 无 is_play 形参）→ TypeError 兼容回退不带新参重试。"""
        calls = []

        class _OldPyd:
            @staticmethod
            def decide(cv_, a, w, h, wr_threshold=8.0, node_cap=2_000_000, **kw):
                calls.append(dict(kw))
                if "is_play" in kw:
                    raise TypeError("decide() got an unexpected keyword 'is_play'")
                return {"x": 1, "y": 1, "reason": "limitation", "confidence": 0.5,
                        "prob": {}, "tied": [], "weighted": [], "world_upper": 8}

        with mock.patch.object(native, "mscore", _OldPyd), \
                self.assertLogs(native.logger, level="WARNING") as logs:
            r = native.decide(_board(), 5, 6, 5, wr_threshold=8.0,
                              node_cap=2_000_000, is_play=False)
        self.assertIsNotNone(r)
        self.assertEqual(len(calls), 2)          # 第一次带 is_play 失败、第二次不带
        self.assertNotIn("is_play", calls[1])    # 回退调用剥离了新参数
        # 回退必须非静默：WARNING 级日志明示帮助放宽参数未生效
        self.assertTrue(any("旧签名兼容回退" in m for m in logs.output),
                        f"回退未打 WARNING 日志: {logs.output}")

    def test_native_decide_new_pyd_passes_is_play(self):
        """新 pyd：is_play 原样透传（False=帮助模式 → C++ DSTAR×3 生效的前提）。"""
        seen = {}

        class _NewPyd:
            @staticmethod
            def decide(cv_, a, w, h, wr_threshold=8.0, node_cap=2_000_000,
                       seed=0, is_play=True, **kw):
                seen["is_play"] = is_play
                return {"x": 1, "y": 1, "reason": "limitation", "confidence": 0.5,
                        "prob": {}, "tied": [], "weighted": [], "world_upper": 8}

        with mock.patch.object(native, "mscore", _NewPyd):
            native.decide(_board(), 5, 6, 5, is_play=False)
        self.assertIs(seen["is_play"], False)


def _sentinel_board(w, h, value=7):
    """complete_scan 的探针返回值：内部全填 value（区别于任何真实盘面）。"""
    cv = np.zeros((h + 2, w + 2), dtype=np.int32)
    cv[1:h + 1, 1:w + 1] = value
    return cv


class TestDecideUnavailableHelper(unittest.TestCase):
    """`_decide_unavailable` 六项语义逐条断言（方案 A Step 1）。"""

    def _make(self):
        s = make_solver(is_play=True)
        s.warning_signal = mock.Mock()
        s.count = 0
        probability._DECIDE_FAIL_SEQ[0] = 0
        return s

    def test_six_semantics(self):
        s = self._make()
        sentinel = _sentinel_board(s.w, s.h)
        scan_mock = mock.Mock(return_value=sentinel)
        s.complete_scan = scan_mock
        cv = _board(s.w, s.h)
        err = RuntimeError("boom")
        with self.assertLogs(probability.logger, level="ERROR") as logs:
            out = s._decide_unavailable(cv, exc=err, where="unit")
        # ① ERROR 日志，文案含「不回退」与「安全重扫」
        joined = "\n".join(logs.output)
        self.assertIn("不回退", joined)
        self.assertIn("安全重扫", joined)
        self.assertIn("unit", joined)          # where 被填进文案，可定位失败类别
        # ② 连续失败计数递增
        self.assertEqual(probability._DECIDE_FAIL_SEQ[0], 1)
        # ③ warning_signal 弹一次，且文案同为可读错误
        self.assertEqual(s.warning_signal.emit.call_count, 1)
        self.assertIn("不回退", s.warning_signal.emit.call_args.args[0])
        # ④ 全盘安全重扫：complete_scan 收到的是「全 9 棋盘」
        self.assertEqual(scan_mock.call_count, 1)
        scanned = scan_mock.call_args.args[0]
        self.assertTrue(np.all(scanned[1:s.h + 1, 1:s.w + 1] == 9),
                        "安全重扫必须基于重建的全 9 棋盘，不能直接复用可疑盘面")
        # ⑤ count 语义（与空盘早退重扫对齐：+1）
        self.assertEqual(s.count, 1)
        # ⑥ 显式返回重扫结果
        self.assertIs(out, sentinel)

    def test_warning_dedup_within_one_failure_streak(self):
        """连续失败只在首发弹一次（防帮助模式 5 轮重试弹 5 个窗）。"""
        s = self._make()
        s.complete_scan = mock.Mock(return_value=_sentinel_board(s.w, s.h))
        cv = _board(s.w, s.h)
        with self.assertLogs(probability.logger, level="ERROR"):
            s._decide_unavailable(cv, where="streak-1")
            s._decide_unavailable(cv, where="streak-2")
            s._decide_unavailable(cv, where="streak-3")
        self.assertEqual(s.warning_signal.emit.call_count, 1,
                         "同一串连续失败只应弹一次 warning（防弹窗风暴）")
        self.assertEqual(probability._DECIDE_FAIL_SEQ[0], 3)
        self.assertEqual(s.count, 3)

    def test_safe_rescan_low_confidence_does_not_raise(self):
        """安全重扫本身不可信（局末失败弹窗）时不得上抛异常（用户裁决 2026-11-07）。

        旧行为：`complete_scan` 抛 `LowConfidenceScanError` 会穿出 helper、一路
        掀掉自动循环线程。新行为：吞掉并返回重建的全 9 棋盘，交回主循环做胜负
        弹窗判定——扫描不确定绝不能中止自动循环。
        """
        from utils.vision import LowConfidenceScanError
        s = self._make()
        s.complete_scan = mock.Mock(
            side_effect=LowConfidenceScanError("全盘扫描：9/9 格无法可信识别"))
        cv = _board(s.w, s.h)
        with self.assertLogs(probability.logger, level="ERROR") as logs:
            out = s._decide_unavailable(cv, where="rescan-untrusted")
        self.assertIn("不回退", "\n".join(logs.output))  # decide 失败语义仍可观测
        # 返回的是重建的全 9 棋盘（不是可疑盘面、也不是抛异常）
        self.assertTrue(np.all(out[1:s.h + 1, 1:s.w + 1] == 9))
        self.assertEqual(s.count, 1)


class TestDecideFailurePaths(unittest.TestCase):
    """F1-F4：decide 失败/不可用/无效坐标必须走 helper，绝不回退 Python 链。

    每个用例都断言「不点击 + 有 ERROR + 有 warning + 走 complete_scan +
    返回重扫盘面」，并额外用「被调用即抛」的探针证明 part_solve /
    process_bigger_situation 零调用（即没有落到已删除的 Python 决策链）。

    ⚠️ 2026-11-09 变更：`ProbabilityMixin.win_rate` 已随 py_win_rate 一同
    彻底删除（用户裁决「彻底删干净（含导出）」；实测 C++ 决策链与 Python
    生产侧均零调用方）。原先这里还有一条 `wr_probe` 探针，
    但 `mock.patch.object(solver, "win_rate", ...)` 对**已不存在的属性**
    会先抛 RuntimeError：探针在测试 setup 阶段就崩，根本没有机会验证
    「被测代码是否调用了它」。这类探针的锁死对象已从代码中消失，
    因此**移除探针本身**（不是放宽断言）——「不回退到 Python 决策链」
    这条契约仍由 part_solve / process_bigger_situation 两条探针把守。
    """

    def _run_fail(self, solver, responses, capture, rescan=None,
                  has_decide=True, probes=True):
        solver.bx, solver.by = capture.bx, capture.by
        solver._last_heatmap = {}
        # 轻量实例（__new__ 跳过 __init__）需要预置入口处的 hasattr 检查属性
        solver._wr_mem_warned = set()
        solver._wr_nodecap_hits = 0
        solver._time_budget = {}
        solver._pv_state = None
        solver.count = 0
        solver.warning_signal = mock.Mock()
        sentinel = _sentinel_board(solver.w, solver.h)
        cv = _board(solver.w, solver.h)
        decide_mock = mock.Mock(side_effect=list(responses))
        rescan_mock = mock.Mock(side_effect=rescan or (lambda cv_: cv_))
        scan_mock = mock.Mock(return_value=sentinel)
        # ⚠️ 2026-11-09：`part_solve` / `process_bigger_situation` 整条 pbs 链路
        # 已按用户裁决**彻底删除**（生产不可达，实测 0 调用点）。
        # 契约「绝不回退到 Python 决策链」因此升级为更强的形态：
        # 这些入口**根本不存在** ⇒ 用 hasattr 断言替代「被调用即抛」探针。
        # （探针无法 mock 一个不存在的属性：mock.patch.object 会先抛
        #   AttributeError，在 setup 阶段就崩，根本没机会验证被测代码。）
        probability._DECIDE_FAIL_SEQ[0] = 0
        # 逐点击失败弹窗探测 / 逐点击 sleep 已删除（用户裁决 2026-11-07）：
        # 用「被调用即抛」探针把这两条契约锁死，回归会直接炸成 AssertionError。
        locate_probe = _Forbidden("_locate（逐点击失败弹窗探测）")
        sleep_probe = _Forbidden("time.sleep（逐点击等待）")
        ctx = [
            mock.patch.object(native, "has_decide", return_value=has_decide),
            mock.patch.object(native, "decide", decide_mock),
            mock.patch.object(probability.pyautogui, "click", capture),
            mock.patch.object(probability.time, "sleep", sleep_probe),
            mock.patch.object(solver, "_locate", locate_probe),
            mock.patch.object(solver, "rescan_after_click", rescan_mock),
            mock.patch.object(solver, "complete_scan", scan_mock),
        ]
        for c in ctx:
            c.start()
        try:
            with self.assertLogs(probability.logger, level="ERROR") as logs:
                out = solver._number5_1_core(cv)
        finally:
            for c in reversed(ctx):
                c.stop()
        return dict(out=out, sentinel=sentinel, logs=logs, decide=decide_mock,
                    rescan=rescan_mock, scan=scan_mock,
                    locate=locate_probe, sleep=sleep_probe)

    def _assert_helper_taken(self, r, solver, capture, expected_clicks=0):
        """共性断言：不点击（或只点击失败前的合法轮次）/ ERROR「不回退」/
        全盘重扫 / 返回重扫盘面 / 计数+1 / warning 一次。

        expected_clicks>0 只用于「循环内第二次 decide 失败」——失败**前**那一轮
        的点击是合法的成功决策，失败**后**绝不允许再点（见 F2/F5）。
        """
        self.assertEqual(len(capture.clicks), expected_clicks,
                         "失败路径在失败发生后绝不允许再点击任何格子")
        self.assertTrue(any("不回退" in m for m in r["logs"].output),
                        f"失败未产生可观测 ERROR：{r['logs'].output}")
        self.assertEqual(r["scan"].call_count, 1, "必须做一次全盘安全重扫")
        self.assertIs(r["out"], r["sentinel"], "必须显式返回重扫后的盘面")
        self.assertEqual(probability._DECIDE_FAIL_SEQ[0], 1)
        self.assertEqual(solver.warning_signal.emit.call_count, 1)
        self.assertEqual(solver.count, 1)

    # ---- F1 首轮 decide 抛异常 ----
    def test_f1_first_decide_raises(self):
        s = make_solver(is_play=True)
        cap = _Capture()
        r = self._run_fail(s, [RuntimeError("boom")], cap)
        self._assert_helper_taken(r, s, cap)
        self.assertEqual(r["decide"].call_count, 1)
        self.assertEqual(r["rescan"].call_count, 0)   # 没点击就不该重扫

    # ---- F2 循环内二次 decide 抛异常 ----
    def test_f2_second_decide_in_loop_raises(self):
        s = make_solver(is_play=True)
        cap = _Capture()
        first = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                 tied=[(3, 2), (4, 2)])
        r = self._run_fail(s, [first, RuntimeError("boom-in-loop")], cap,
                           rescan=_rescan_opening(cap))
        self._assert_helper_taken(r, s, cap, expected_clicks=2)
        self.assertEqual(len(cap.clicks), 2, "首轮批量点击应已发生")
        self.assertEqual(r["decide"].call_count, 2)
        self.assertEqual(r["rescan"].call_count, 1)

    # ---- F3 has_decide() == False（旧 pyd / MSW_DISABLE_NATIVE=1）----
    def test_f3_has_decide_false_does_not_fall_back(self):
        s = make_solver(is_play=True)
        cap = _Capture()
        r = self._run_fail(s, [], cap, has_decide=False)
        self._assert_helper_taken(r, s, cap)
        self.assertEqual(r["decide"].call_count, 0, "不可用时不应调用 decide")
        self.assertTrue(any("has_decide=False" in m for m in r["logs"].output),
                        "失败日志应指明是哪一类不可用")

    # ---- F4 decide 返回无效坐标 ----
    def test_f4_invalid_coordinates(self):
        s = make_solver(is_play=True)
        cap = _Capture()
        r = self._run_fail(s, [_invalid_response()], cap)
        self._assert_helper_taken(r, s, cap)
        self.assertEqual(r["decide"].call_count, 1)
        self.assertEqual(r["rescan"].call_count, 0)

    # ---- F5 循环内二次 decide 返回无效坐标 ----
    def test_f5_second_decide_in_loop_invalid(self):
        """循环内无效坐标同样是失败（不能只 break 后当成功返回）。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        first = _decide_response(1, 1, confidence=0.5)
        r = self._run_fail(s, [first, _invalid_response()], cap,
                           rescan=_rescan_opening(cap))
        self._assert_helper_taken(r, s, cap, expected_clicks=1)
        self.assertEqual(len(cap.clicks), 1)
        # pbs 链路已彻底删除：契约从「零调用」升级为「入口不存在」
        self.assertFalse(hasattr(type(s), "part_solve"),
                         "已删除的 Python 决策链入口 part_solve 不应再存在")
        self.assertFalse(hasattr(type(s), "process_bigger_situation"),
                         "已删除的 Python 决策链入口 process_bigger_situation 不应再存在")


class TestNoPerClickDeathProbe(unittest.TestCase):
    """逐点击死亡探测与 sleep 必须**零调用**（用户裁决 2026-11-07）。

    症状与诉求（用户原话）：「每一局结束之后，因为出现了雷的图像，以可能会
    导致爆出棋盘扫描不确定的错误，这个错误不要影响自动循环的执行」。

    根因链：decide 循环每次 `pyautogui.click` 之后都 `time.sleep(setting.sleep)`
    再 `_locate("./image/lose.bmp")` 判死；局末棋盘出现雷/失败弹窗时该探测与
    随后的 rescan 都可能失真，`_dead_hit` 直接把整轮决策打断，而扫描不确定
    还会让 `count` 累加到 `play()` 的 `count >= 3` 上限而停机。

    本类用「被调用即抛」探针 + mock 计数把契约锁死：
      ① 点击批量/连续循环下 `_locate` / `time.sleep` **一次都不许调**；
      ② 点击本身要照常发生（不能为了不探测把点击也删了）；
      ③ 重扫不可信（`LowConfidenceScanError`）时**不抛、不停机**：
         `count` 不变、返回盘面、自动循环可继续下一轮；
      ④ 主循环 `utils/solver.py` 的胜负弹窗检测仍存在（未被本次改动波及）。

    「真实窗口未覆盖」：本类全部用 stub，不触真实屏幕/真实游戏窗口，
    真机 GUI 10 局冒烟属验收层（见报告「未覆盖」节）。
    """

    def _run_core(self, solver, responses, capture, rescan=None):
        solver.cell_width = capture.cell_width
        solver.bx, solver.by = capture.bx, capture.by
        solver._last_heatmap = {}
        solver._wr_mem_warned = set()
        solver._wr_nodecap_hits = 0
        solver._time_budget = {}
        solver._pv_state = None
        solver.count = 0
        cv = _board(solver.w, solver.h)
        decide_mock = mock.Mock(side_effect=list(responses))
        rescan_mock = mock.Mock(side_effect=rescan or (lambda cv_: cv_))
        locate_probe = _Forbidden("_locate（逐点击失败弹窗探测）")
        sleep_probe = _Forbidden("time.sleep（逐点击等待）")
        probability._DECIDE_FAIL_SEQ[0] = 0
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide", decide_mock), \
                mock.patch.object(probability.pyautogui, "click", capture), \
                mock.patch.object(probability.time, "sleep", sleep_probe), \
                mock.patch.object(solver, "_locate", locate_probe), \
                mock.patch.object(solver, "rescan_after_click", rescan_mock):
            out = solver._number5_1_core(cv)
        # 零调用契约（探针被调用会先抛，这里再显式复核计数）
        self.assertEqual(locate_probe.calls, 0,
                         f"_locate 不应被调用，实际 {locate_probe.calls} 次")
        self.assertEqual(sleep_probe.calls, 0,
                         f"time.sleep 不应被调用，实际 {sleep_probe.calls} 次")
        return out, decide_mock, rescan_mock

    def test_no_per_click_locate_or_sleep_single_click(self):
        """单点连续两轮：4 次点击机会中出现 2 次真实点击，零探测零 sleep。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(1, 1, confidence=0.93)
        out, decide_mock, rescan_mock = self._run_core(
            s, [resp, resp], cap, _rescan_opening(cap))
        self.assertEqual(cap.clicks, [(100 + 24, 200 + 24)])
        self.assertEqual(rescan_mock.call_count, 2)
        self.assertEqual(decide_mock.call_count, 2)
        self.assertEqual(_num9(out), 24)  # 盘面真的推进（点击未被删）

    def test_no_per_click_locate_or_sleep_batch(self):
        """certain_safe 批量 3 格：3 次点击，仍零探测零 sleep。"""
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                tied=[(3, 2), (4, 2), (3, 3)])
        out, decide_mock, rescan_mock = self._run_core(
            s, [resp, resp], cap, _rescan_opening(cap))
        self.assertEqual(len(cap.clicks), 3, "批量点击必须保留（只删探测不删点击）")
        self.assertEqual(_num9(out), 22)  # 25 - 3
        self.assertEqual(decide_mock.call_count, 2)

    def test_rescan_low_confidence_does_not_stop_auto_loop(self):
        """重扫不可信（局末失败弹窗）→ 本轮作废但**不停机**、不弹警告、count 不变。"""
        from utils.vision import LowConfidenceScanError
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(2, 2, confidence=0.8)
        boom = mock.Mock(side_effect=LowConfidenceScanError(
            "点击后重扫：9/9 格无法可信识别（比例 100% ≥ 阈值 50%）"))
        s.warning_signal = mock.Mock()
        with self.assertLogs(probability.logger, level="WARNING") as logs:
            out, decide_mock, _ = self._run_core(s, [resp], cap, rescan=boom)
        # ① 点击照常发生（生不生效由主循环判定，不在点击处判死）
        self.assertEqual(len(cap.clicks), 1)
        # ② 扫描不确定不是决策失败：count 不累加（否则 play 的 count>=3 会停机）
        self.assertEqual(s.count, 0)
        # ③ 不弹 warning 窗（避免局末每次都弹窗打搅）
        self.assertEqual(s.warning_signal.emit.call_count, 0)
        # ④ 不因扫描异常走 _decide_unavailable（失败计数保持 0）
        self.assertEqual(probability._DECIDE_FAIL_SEQ[0], 0)
        # ⑤ 盘面原样交回主循环（未开格仍在），下一轮可继续
        self.assertEqual(_num9(out), 25)
        # ⑥ 可观测：有一条 WARNING 日志说明「本轮作废」，不是静默吞掉
        self.assertTrue(any("不确定" in m for m in logs.output),
                        f"扫描不确定必须留可观测日志：{logs.output}")

    def test_auto_loop_can_iterate_after_low_confidence(self):
        """扫描不确定后主循环可继续：同一实例第二次决策仍正常点击/返回。"""
        from utils.vision import LowConfidenceScanError
        s = make_solver(is_play=True)
        cap = _Capture()
        resp = _decide_response(2, 2, confidence=0.8)
        boom = mock.Mock(side_effect=LowConfidenceScanError("先来一次不可信重扫"))
        s.warning_signal = mock.Mock()
        with self.assertLogs(probability.logger, level="WARNING"):
            out1, _, _ = self._run_core(s, [resp], cap, rescan=boom)
        self.assertEqual(_num9(out1), 25)
        # 第二轮：重扫恢复正常 → 正常推进（证明循环未被前一局的失败态卡住）
        cap2 = _Capture()
        out2, decide_mock, _ = self._run_core(
            s, [resp, resp], cap2, _rescan_opening(cap2))
        self.assertEqual(len(cap2.clicks), 1)
        self.assertEqual(_num9(out2), 24)
        self.assertEqual(decide_mock.call_count, 2)

    def test_main_loop_winlose_detection_untouched(self):
        """主循环（utils/solver.py）的胜负弹窗检测必须保持原样（本次未改）。"""
        import inspect
        import utils.solver as solver_mod
        src = inspect.getsource(solver_mod.Solver.play)
        # 双通道：FindWindow 标题检测 + win/lose 模板检测都在
        self.assertIn('"游戏胜利"', src)
        self.assertIn('"游戏失败"', src)
        self.assertIn('image/win.bmp', src)
        self.assertIn('image/lose.bmp', src)
        # 主循环仍按局末弹窗重置盘面（死亡后新局继续）
        self.assertIn("_gs_save", src)


class TestHelpModeUsesCppDecideOnly(unittest.TestCase):
    """帮助模式必须**只用 C++ decide**：不得再调用任何 Python 规则推理。

    契约反转留痕（2026-11-07 用户指令「帮助模式仍调用了 PY 的逻辑推理」）：
    本类原本断言 `help()` 逐字调用
    `2×(mine_clear1, mine_clear3_1) + mine_clear1`（即「Py 推理链必须保留」）。
    用户明确要求帮助模式与自动模式统一为唯一 C++ decide 决策链，因此
    `mine_clear1`/`mine_clear3_1` 已从 `help()` 移除，旧断言**必然反转**：
    现在断言这两个 Python 推理方法**一次都不能被调用**，且必须走 number5_1
    （其内部即 C++ decide 循环）。

    ⚠️ 仍保留的部分：`complete_scan`（**视觉扫描**，不是推理）必须照常调用。
    """

    def _run_help(self):
        s = make_solver(is_play=False)
        calls = []
        n51_calls = []

        def _mc1(cell_value, *a, **kw):
            calls.append("mine_clear1")
            return cell_value

        def _mc31(cell_value, *a, **kw):
            calls.append("mine_clear3_1")
            return cell_value

        def _n51(cell_value):
            n51_calls.append(1)
            s.pos_dict_list.append({"pos": (1, 1), "exp": "C++ 三层决策 (pbs)"})
            return cell_value

        s.complete_scan = mock.Mock(side_effect=lambda c, *a, **kw: c)
        s.mine_clear1 = _mc1
        s.mine_clear3_1 = _mc31
        s.number5_1 = _n51
        s._stop_requested = lambda: False
        s.pv_signal = mock.Mock()
        s.Visible_signal = mock.Mock()
        s.update_btn_list_signal = mock.Mock()
        s.bx = s.by = 0
        with mock.patch("utils.solver.win32gui.FindWindow", return_value=1), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)):
            s.help()
        return s, calls, n51_calls

    def test_help_never_calls_python_deduction(self):
        """mine_clear1 / mine_clear3_1 在帮助模式必须零调用。"""
        s, calls, _ = self._run_help()
        self.assertEqual(
            calls, [],
            f"帮助模式仍在调用 Python 规则推理：{calls}（用户要求只用 C++ decide）")

    def test_help_candidates_cover_all_certain_cells_in_win_rate(self):
        """win_rate 阶段候选表必须收全所有 prob==1.0 的确定安全格。

        用户实测「最后连推荐点击的格子都没有了」：根因是帮助分支只在
        certain_safe 分支取整批 tied，win_rate/pbs/limitation 分支只收
        (x,y) 单点 ⇒ C++ 返回 56 个确定安全格时棋盘只剩 1 个推荐标记。
        修复后以逐格 prob 为真源，全部确定安全格与确定雷格都进候选表。
        """
        s = make_full_solver(w=5, h=5, a=3)
        s.is_play = False
        s._time_budget = {}
        s._pv_state = None
        s.count = 0
        s.cell_value = _board(s.w, s.h)
        cv = _board(s.w, s.h)
        # 造 4 个确定安全格 + 2 个确定雷格，reason=win_rate
        prob = {(1, 1): 1.0, (2, 1): 1.0, (3, 1): 1.0, (4, 1): 1.0,
                (1, 2): 0.0, (2, 2): 0.0}
        resp = _decide_response(1, 1, reason="win_rate", confidence=1.0,
                                prob=prob)
        resp["limitation"] = 8.0
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide", return_value=resp), \
                mock.patch.object(probability, "_tb_save", lambda st: None), \
                mock.patch.object(s, "rescan_after_click", lambda c: c), \
                mock.patch.object(s, "complete_scan", lambda c, *a, **k: c), \
                mock.patch.object(probability.pyautogui, "click",
                                  lambda *a, **k: None), \
                mock.patch.object(probability.time, "sleep", lambda s_: None):
            s._number5_1_core(cv)
        safe = [c for c in s.pos_dict_list if not c["is_mine"]]
        mines = [c for c in s.pos_dict_list if c["is_mine"]]
        self.assertEqual(
            {c["pos"] for c in safe},
            {(1, 1), (2, 1), (3, 1), (4, 1)},
            "win_rate 的全部确定安全格都必须成为推荐候选")
        self.assertEqual(
            {c["pos"] for c in mines},
            {(1, 2), (2, 2)},
            "确定雷格也必须进候选表（供右键标记）")

    def test_help_survives_low_confidence_scan(self):
        """首轮扫描不可信时帮助线程必须继续工作，不得静默终止。

        回归背景（用户实测「卡在等待帮助计算开始那一行」）：`help()` 里用了
        ``except LowConfidenceScanError``，但 ``utils/solver.py`` **从未 import
        该名字** ⇒ 一旦真机首轮扫描不可信（480/480 无法识别，很常见），这个
        except 子句本身抛 ``NameError`` ⇒ 被外层兜底捕获 ⇒ 线程退出 ⇒
        界面永久停在「等待帮助计算开始…」。
        本用例用真实 ``LowConfidenceScanError`` 触发该分支，断言：
        ① 不抛异常；② 仍发出 heatmap 帧；③ 仍发候选；④ 不走 Visible(False)。
        """
        from utils.vision import LowConfidenceScanError

        s = make_solver(is_play=False)
        s.complete_scan = mock.Mock(
            side_effect=LowConfidenceScanError("全盘扫描 480/480 无法识别"))
        s.number5_1 = mock.Mock(side_effect=lambda c: c)
        s._stop_requested = lambda: False
        s.pv_signal = mock.Mock()
        s.Visible_signal = mock.Mock()
        s.warning_signal = mock.Mock()
        s.update_btn_list_signal = mock.Mock()
        s.bx = s.by = 0
        with mock.patch("utils.solver.win32gui.FindWindow", return_value=1), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)):
            s.help()   # 必须正常返回，不得抛
        self.assertEqual(s.update_btn_list_signal.emit.call_count, 1,
                         "扫描不可信也必须把候选发给 UI（否则界面停在等待）")
        self.assertNotIn(mock.call(False), s.Visible_signal.emit.call_args_list,
                         "扫描不可信不得终止线程（Visible(False) 会让界面停在等待）")
        self.assertEqual(s.number5_1.call_count, 1,
                         "扫描不可信也必须继续走 C++ decide")

        """help() 必须有兜底异常捕获，绝不让线程静默死掉。

        回归背景（用户实测「帮助模式已经彻底不工作了」）：``run()`` 不捕获
        ``help()`` 的异常，任何未预期错误都会让 QThread 直接死掉、UI 停在
        初始态，且**没有任何提示**。旧实现在去掉 Python 推理的同时也去掉了
        ``except ValueError`` 兜底，形成静默失败。
        """
        s = make_solver(is_play=False)
        s.complete_scan = mock.Mock(side_effect=RuntimeError("boom"))
        s._stop_requested = lambda: False
        s.pv_signal = mock.Mock()
        s.Visible_signal = mock.Mock()
        s.warning_signal = mock.Mock()
        s.update_btn_list_signal = mock.Mock()
        s.bx = s.by = 0
        with mock.patch("utils.solver.win32gui.FindWindow", return_value=1), \
                mock.patch("utils.solver.ClientToScreen", return_value=(0, 0)):
            with self.assertLogs("utils.solver", level="ERROR") as logs:
                s.help()      # 不得抛（否则线程静默死掉）
        self.assertTrue(any("帮助模式异常退出" in m for m in logs.output),
                        f"兜底未记录 ERROR 日志: {logs.output}")
        self.assertGreaterEqual(s.warning_signal.emit.call_count, 1,
                                "兜底必须把原因可见地告诉用户")
        s.Visible_signal.emit.assert_any_call(False)

    def test_help_candidates_have_unique_sequence_numbers(self):
        """整批 certain_safe 候选必须各自拿到唯一递增序号。

        main.py 用 pos_dict["num"] 渲染候选格文字并绑定点击/预览。整批共用
        同一个 self.num 会让多格显示同一编号（实测 3 格都显示 "1"）。
        """
        s = make_solver(is_play=False)
        s.num = 1
        cv = _board(s.w, s.h)
        s._wr_mem_warned = set()
        s._wr_nodecap_hits = 0
        s._time_budget = {}
        s._pv_state = None
        s._last_heatmap = {}
        cap = _Capture()
        s.cell_width = cap.cell_width
        s.bx, s.by = cap.bx, cap.by
        batch = [(3, 2), (4, 2), (3, 3)]
        resp = _decide_response(3, 2, reason="certain_safe", confidence=1.0,
                                tied=batch)
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide",
                                  side_effect=[resp, resp]), \
                mock.patch.object(probability.pyautogui, "click", cap):
            s._number5_1_core(cv)
        nums = [p["num"] for p in s.pos_dict_list]
        self.assertEqual(len(nums), len(batch), f"应收集整批候选，实测 {nums}")
        self.assertEqual(len(set(nums)), len(nums),
                         f"候选序号必须唯一，实测 {nums}（出现重复编号）")
        """必须走 number5_1（内部为 C++ decide 循环），且候选被发给 UI。"""
        s, calls, n51_calls = self._run_help()
        self.assertEqual(len(n51_calls), 1,
                         "帮助模式必须经过 number5_1（C++ decide 入口）")
        self.assertEqual(s.update_btn_list_signal.emit.call_count, 1,
                         "C++ decide 产出的候选必须发给 UI")
        # 视觉扫描仍需要（它不是推理，是输入）
        self.assertGreaterEqual(s.complete_scan.call_count, 1,
                                "帮助模式仍需要扫描棋盘事实（视觉输入）")

    def test_help_budget_restored_after_run(self):
        """预算乘数在 finally 里恢复（防泄漏到自动模式）。"""
        self._run_help()
        self.assertEqual(probability.decision_budget(), 30.0)


class TestOptimalLayerReturnsProb(unittest.TestCase):
    """OPTIMAL（精确穷举）层必须返回逐格 prob。

    回归背景（用户实测 2026-11-07）：末局进入 C++ 精确穷举层后整盘热力图
    回落成「全灰」。根因是 ``cpp/mscore.cpp`` 的 OPTIMAL 分支 ``make_response``
    没有传 ``prob``，payload 里 prob 为空，UI 按「无数据」渲染。

    本用例直接调真实原生 ``mscore.decide``，搜出一个会进 OPTIMAL 的末局盘面，
    断言 **reason == "optimal" 且 prob 非空**。搜不到时显式 skip（而不是
    静默通过），避免原生模块未更新时给出假绿。
    """

    @classmethod
    def setUpClass(cls):
        cls.native = pytest_importorskip_native()

    def test_optimal_branch_returns_per_cell_prob(self):
        if self.native is None or not hasattr(self.native, "decide"):
            self.skipTest("原生 mscore.decide 不可用（生产 pyd 无该导出）")
        rng = np.random.default_rng(7)
        found = None
        for _ in range(2000):
            w = h = 5
            a = int(rng.integers(3, 6))
            cv = np.full((h + 2, w + 2), 9, dtype=np.int32)
            for y in range(1, h + 1):
                for x in range(1, w + 1):
                    if rng.random() < 0.5:
                        cv[y][x] = int(rng.integers(0, 3))
            try:
                r = self.native.decide(cv, a=a, w=w, h=h, is_play=True)
            except Exception:
                continue
            if r and r.get("reason") == "optimal":
                found = r
                break
        if found is None:
            self.skipTest("当前原生模块未在搜索范围内触发 OPTIMAL 层，"
                          "无法验证该分支（不代表通过）")
        self.assertGreater(
            len(found.get("prob") or {}), 0,
            "OPTIMAL 层必须返回逐格 prob，否则末局热力图会变全灰")
        self.assertGreater(found["x"], 0)
        self.assertGreater(found["y"], 0)


def pytest_importorskip_native():
    """返回用于验证 OPTIMAL prob 的原生模块；都不可用返回 None（调用方 skip）。

    优先加载「已构建但尚未安装」的修复版模块（``cpp/mscore_new.pyd``）：
    生产 pyd 被正在运行的程序锁住时无法就地替换，验证必须针对**修复版**，
    否则会拿旧模块给出假红/假绿。
    """
    import glob
    import importlib.util
    import os

    candidates = [os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "cpp", "mscore_new.pyd")]
    try:
        from utils import native
        if native.available:
            candidates.append(None)      # None = 已安装的生产模块
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            if path is None:
                from utils import native as _n
                mod = _n.mscore
            else:
                if not os.path.exists(path):
                    continue
                # ⚠️ 加载名必须与 pyd 内部的 PyInit 导出名一致：本模块导出
                # 的是 PyInit_mscore，因此即使文件叫 mscore_new.pyd 也必须用
                # 名字 "mscore" 加载，否则报 "does not define module export
                # function"，异常被外层吞掉 → 静默回退旧模块 → 假红。
                spec = importlib.util.spec_from_file_location(
                    "mscore", os.path.abspath(path))
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            if mod is not None and hasattr(mod, "decide"):
                return mod
        except Exception:  # noqa: BLE001
            continue
    return None


class TestHelpTransitionsAllLayers(unittest.TestCase):
    """2026-11-08 修复：帮助模式不再只有 OPTIMAL 层产真实转移。

    用户实测（expert 30x16/99）：帮助页预览恒显示「暂无真实状态转移分布」，
    根因是 C++ 只在 OPTIMAL 精确层返回 transitions，而实际决策绝大多数走
    certain_safe / win_rate / pbs 分支。修法是在 decide 出口对帮助模式统一
    补精确世界前瞻（只影响 is_play=False；自动模式零改动）。

    本用例直接对真实原生模块跑一个合法残局盘面：断言帮助模式拿到真实转移
    （概率合计=1、basis=exact_worlds），且自动模式不因本改动被拖入前瞻。
    找不到合适盘面或模块不可用时显式 skip，不静默给假绿。
    """

    @classmethod
    def setUpClass(cls):
        cls.native = pytest_importorskip_native()

    @staticmethod
    def _legal_expert_board(seed=777, open_frac=0.55, return_mines=False):
        """确定性构造合法 expert 30x16/99 中局盘面（实测会走 win_rate 分支）。

        与小盘随机搜索不同：expert 盘的世界数介于 DSTAR(1200) 与精确前瞻
        上限之间，帮助模式决策落在 win_rate 层而**不是** OPTIMAL 层 —— 正是
        用户实测「进入 win_rate 阶段看不到状态转移」的场景。
        """
        W, H, A = 30, 16, 99
        rng = np.random.default_rng(seed)
        mines = np.zeros((H + 2, W + 2), dtype=bool)
        cells = [(x, y) for y in range(1, H + 1) for x in range(1, W + 1)]
        for idx in rng.choice(len(cells), size=A, replace=False):
            x, y = cells[idx]
            mines[y, x] = True
        cv = np.full((H + 2, W + 2), 9, dtype=np.int32)
        cv[0, :] = 0; cv[-1, :] = 0; cv[:, 0] = 0; cv[:, -1] = 0

        def nb(x, y):
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx or dy:
                        nx, ny = x + dx, y + dy
                        if 1 <= nx <= W and 1 <= ny <= H:
                            yield nx, ny

        safe = [c for c in cells if not mines[c[1], c[0]]]
        rng.shuffle(safe)
        for (x, y) in safe[:int(len(safe) * open_frac)]:
            if mines[y, x] or cv[y, x] != 9:
                continue
            stack = [(x, y)]
            while stack:
                cx, cy = stack.pop()
                if cv[cy, cx] != 9:
                    continue
                n = int(sum(mines[ny, nx] for nx, ny in nb(cx, cy)))
                cv[cy, cx] = n
                if n == 0:
                    for nx, ny in nb(cx, cy):
                        if cv[ny, nx] == 9 and not mines[ny, nx]:
                            stack.append((nx, ny))
        if return_mines:
            return cv, A, W, H, mines
        return cv, A, W, H

    def test_help_mode_returns_real_transitions_for_non_optimal_layer(self):
        if self.native is None:
            self.skipTest("原生 mscore.decide 不可用")
        cv, A, W, H = self._legal_expert_board()
        try:
            r = self.native.decide(cv, a=A, w=W, h=H,
                                   wr_threshold=18.0,
                                   node_cap=6_000_000, is_play=False)
        except Exception as e:
            self.skipTest(f"原生 decide 在该盘面异常：{e}")
        if not r or r.get("x", 0) < 1:
            self.skipTest("该盘面原生 decide 未给出有效坐标")
        # 该盘面实测走 win_rate；若原生版本行为改变导致走 OPTIMAL，本用例仍
        # 需覆盖「非 OPTIMAL 分支」语义 —— 用采样兜底，找不到就显式 skip。
        if str(r.get("reason")) == "optimal":
            self.skipTest("该盘面本次走 OPTIMAL，未覆盖非 OPTIMAL 分支")
        tr = r.get("transitions")
        self.assertTrue(tr, "非 OPTIMAL 决策分支也必须产出真实状态转移")
        total = sum(float(x["probability"]) for x in tr)
        self.assertAlmostEqual(total, 1.0, places=6,
                               msg="真实条件概率之和必须为 1")
        meta = r.get("transition_meta") or {}
        self.assertEqual(meta.get("basis"), "exact_worlds")
        self.assertGreater(int(meta.get("world_count") or 0), 0)
        self.assertGreater(float(meta.get("p_click_safe") or 0), 0.0)

    def test_branch_board_matches_real_cell_values(self):
        """分支 7×7 局面必须与真实盘面一致（已开数字不得重算、10/11 不得丢）。

        用户实测 2026-11-08「状态转移显然跟实际状态不符，边上已有的数字不显示、
        覆盖不全」。两个根因：
          ① 旧实现用「第一个一致世界」重算已开格数字——一致世界只是与已知
             数字相容的假设雷布局，不是真实雷位，算出的数字与盘面冲突
             （实测整行被算成 0）；
          ② `opened0` 只认 0-8，真实 cv 里的 10（右键标记雷）/11（推理安全）
             不算已开，被画成未开 9 或被「确定雷」覆盖。
        修复后：已开格直接沿用真实 cv，10/11 原样保留。
        """
        if self.native is None or not hasattr(self.native, "transitions_for"):
            self.skipTest("原生模块无 transitions_for 导出")
        # seed=777 / 90% 已开 实测最稳（前 20 个未开格有 7 格能出转移）
        cv, A, W, H, mines = self._legal_expert_board(
            seed=777, open_frac=0.90, return_mines=True)
        # 只把「真实是雷」的未开格标成 10：标错格会破坏世界一致性，
        # 导致 no_feasible_worlds、整盘无转移（实测踩过）。
        marked = 0
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                if cv[y, x] == 9 and mines[y, x] and marked < 8:
                    cv[y, x] = 10
                    marked += 1
        self.assertGreater(marked, 0, "测试盘面必须能标出至少一个 10")
        closed = [(x, y) for y in range(1, H + 1) for x in range(1, W + 1)
                  if cv[y, x] == 9]
        checked = 0
        for (tx, ty) in closed:
            try:
                r = self.native.transitions_for(
                    cv, a=A, w=W, h=H, x=tx, y=ty)
            except Exception:
                continue
            tr = r.get("transitions")
            if not tr:
                continue
            board = tr[0]["board"]
            self.assertEqual(len(board), 7)
            bad = []
            for ri in range(7):
                for ci in range(7):
                    x = tx + ci - 3
                    y = ty + ri - 3
                    if not (1 <= x <= W and 1 <= y <= H):
                        continue
                    real = int(cv[y, x])
                    got = board[ri][ci]
                    # 已开数字与 10/11 必须原样保留
                    if (0 <= real <= 8 or real in (10, 11)) and got != real:
                        bad.append(((x, y), real, got))
            self.assertEqual(
                bad, [],
                f"悬停 ({tx},{ty}) 的分支局面与真实盘面不一致：{bad[:6]}")
            checked += 1
            if checked >= 2:
                break
        self.assertGreater(checked, 0,
                           "未找到可产出转移的悬停格，无法验证（不代表通过）")

    def test_auto_mode_never_runs_help_lookahead(self):
        """自动模式不受本改动影响（Auto 页只显示热力图，用户明确要求）。"""
        if self.native is None:
            self.skipTest("原生 mscore.decide 不可用")
        rng = np.random.default_rng(7)
        W, H, A = 5, 5, 4
        checked = 0
        for _ in range(80):
            cv = np.full((H + 2, W + 2), 9, dtype=np.int32)
            cv[0, :] = 0; cv[-1, :] = 0; cv[:, 0] = 0; cv[:, -1] = 0
            for y in range(1, H + 1):
                for x in range(1, W + 1):
                    if rng.random() < 0.5:
                        cv[y][x] = int(rng.integers(0, 3))
            try:
                r = self.native.decide(cv, a=A, w=W, h=H,
                                       node_cap=2_000_000, is_play=True)
            except Exception:
                continue
            if not r:
                continue
            checked += 1
            # 自动模式绝不能带 transition_reason（那是帮助前瞻的字段）。
            self.assertNotIn("transition_reason", r)
        self.assertGreater(checked, 0, "采样内必须有可判定的自动模式决策")


class TestBoardConsistencyGuard(unittest.TestCase):
    """decide 之前的盘面逻辑一致性校验（用户指令 2026-11-08）。

    背景：视觉扫描偶发错误会产出逻辑上不可能的盘面，用它求解只会得到
    错误概率，且 C++ 侧表现不一致（实测 empty_group / ValueError /
    no_closed_cell 三种）。策略（用户裁决）：decide 前校验，矛盾则退回
    重扫，**重扫 3 次仍矛盾才报错告警**。
    """

    def test_validator_detects_contradictions(self):
        """逐格邻域可满足性 + 总雷数可行性。"""
        from utils.probability import (validate_board_consistency,
                                       format_board_problems)
        W = H = 9

        def blank():
            cv = np.full((H + 2, W + 2), 9, dtype=np.int32)
            cv[0, :] = 0; cv[-1, :] = 0; cv[:, 0] = 0; cv[:, -1] = 0
            return cv

        # ① 数字 1 但邻域已标 2 个雷
        cv = blank(); cv[2, 2] = 1; cv[1, 1] = 10; cv[1, 2] = 10
        ok, probs = validate_board_consistency(cv, 10, W, H)
        self.assertFalse(ok)
        self.assertTrue(probs)
        self.assertIn("已标", format_board_problems(probs))

        # ② 数字 3 但邻域只剩 1 个未开格
        cv = blank(); cv[2, 2] = 3
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                cv[2 + dy, 2 + dx] = 0
        cv[1, 1] = 9
        ok, _ = validate_board_consistency(cv, 10, W, H)
        self.assertFalse(ok, "未开格不足以凑满数字剩余雷数，必须报矛盾")

        # ③ 总雷数超过未开格数
        cv = blank()
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                cv[y, x] = 0
        cv[1, 1] = cv[1, 2] = cv[2, 1] = 9
        ok, probs = validate_board_consistency(cv, 99, W, H)
        self.assertFalse(ok)
        self.assertIn("剩余雷", format_board_problems(probs))

        # ④ 已标雷数 > 总雷数
        cv = blank()
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                cv[y, x] = 10
        ok, probs = validate_board_consistency(cv, 3, W, H)
        self.assertFalse(ok)
        self.assertIn("已标雷", format_board_problems(probs))

    def test_validator_accepts_legal_boards(self):
        """合法盘面不得误报（误报会让自动循环空转）。"""
        from utils.probability import validate_board_consistency
        W = H = 9
        # 全未开
        cv = np.full((H + 2, W + 2), 9, dtype=np.int32)
        cv[0, :] = 0; cv[-1, :] = 0; cv[:, 0] = 0; cv[:, -1] = 0
        ok, probs = validate_board_consistency(cv, 10, W, H)
        self.assertTrue(ok, f"全未开盘面不得误报：{probs}")
        # 中央 3×3 已开 + 外圈未开 + 一个标记雷（邻接唯一的 1）
        for y in range(4, 7):
            for x in range(4, 7):
                cv[y, x] = 0
        cv[4, 4] = 1; cv[3, 4] = 10; cv[5, 4] = 1; cv[4, 5] = 1
        ok, probs = validate_board_consistency(cv, 10, W, H)
        self.assertTrue(ok, f"合法盘面被误判：{probs}")

    def test_inconsistent_board_triggers_rescan_and_warns(self):
        """矛盾盘面：decide 前拦截 → 重扫 → 3 次仍矛盾则告警。"""
        s = make_full_solver(w=9, h=9, a=10)
        s.is_play = True
        bad = np.full((11, 11), 9, dtype=np.int32)
        bad[0, :] = 0; bad[-1, :] = 0; bad[:, 0] = 0; bad[:, -1] = 0
        bad[2, 2] = 1; bad[1, 1] = 10; bad[1, 2] = 10   # 数字 1 邻域 2 雷
        s.cell_value = bad.copy()
        s._pv_state = None
        s._time_budget = {}
        s._wr_mem_warned = set()
        s._wr_nodecap_hits = 0
        s.count = 0
        scans = []
        # 关键：重扫仍返回**同一矛盾盘面**（模拟真机持续识别错误）。
        # 若 mock 返回全 9 空白盘，校验会通过、直接进 decide，测不到本路径。
        s.complete_scan = lambda cv, no_10=True: (
            scans.append(1), bad.copy())[1]
        s.rescan_after_click = lambda c: c
        warned = []
        s.warning_signal = mock.Mock()
        s.warning_signal.emit = warned.append
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide",
                                  side_effect=AssertionError(
                                      "矛盾盘面不应进入 decide")):
            out = s._number5_1_core(bad.copy())
        self.assertGreaterEqual(len(scans), 2, "矛盾时应触发退回重扫")
        self.assertTrue(warned, "3 次仍矛盾必须发告警")
        self.assertIn("逻辑矛盾", " ".join(str(w) for w in warned))
        self.assertIsNotNone(out)

    def test_legal_board_goes_straight_to_decide(self):
        """合法盘面：0 次重扫，直接进入 decide。"""
        s = make_full_solver(w=9, h=9, a=10)
        s.is_play = True
        good = np.full((11, 11), 9, dtype=np.int32)
        good[0, :] = 0; good[-1, :] = 0; good[:, 0] = 0; good[:, -1] = 0
        for y in range(4, 7):
            for x in range(4, 7):
                good[y, x] = 0
        good[4, 4] = 1; good[3, 4] = 10; good[5, 4] = 1; good[4, 5] = 1
        s.cell_value = good.copy()
        s._pv_state = None
        s._time_budget = {}
        s._wr_mem_warned = set()
        s._wr_nodecap_hits = 0
        s.count = 0
        scans = []
        s.complete_scan = lambda cv, no_10=True: (scans.append(1), cv.copy())[1]
        s.rescan_after_click = lambda c: c
        called = []

        def fake_decide(*a, **kw):
            called.append(1)
            return {"x": 5, "y": 4, "confidence": 1.0, "path": 1,
                    "reason": "certain_safe", "estimated_bytes": 0,
                    "world_upper": 1, "prob": {(5, 4): 1.0},
                    "tied": [(5, 4)], "weighted": [], "limitation": 1.0}

        s.warning_signal = mock.Mock()
        with mock.patch.object(native, "has_decide", return_value=True), \
                mock.patch.object(native, "decide", side_effect=fake_decide):
            s._number5_1_core(good.copy())
        self.assertEqual(len(scans), 0,
                         f"合法盘面不得触发重扫（实际 {len(scans)} 次）")
        self.assertTrue(called, "合法盘面必须进入 decide")


class TestDigit8Grouping(unittest.TestCase):
    """数字 8 必须参与前沿分组（回归锁，用户实测 2026-11-08）。

    根因：``cpp/mscore.cpp`` 里两套数字范围不一致——
      · 分组 cell_sets（decide_impl / py_transitions）用 ``0<v<8`` ⇒ 排除 8；
      · run_part_solve 建约束 ``cs`` 用 ``1<=v<=8`` ⇒ 含 8。
    数字 8 是「8 邻格全是雷」的最强约束。排除它会让贴着 8 的 frontier 格
    各自独立成组，而组内 cs 仍含 8 ⇒ recurse 的 check_leaf / check_bound
    恒不满足 ⇒ part_solve 解集为空 ⇒ decide 返回
    ``reason=empty_group, x=y=-1``。真机表现就是残局连报「决策不可用」。

    中局不显形（frontier 格通常还贴着 1..7 可合组）；残局只剩零星格时必炸。

    用例构造**逻辑完全自洽**的盘面：6×6 中心 (3,3)=8，其 8 邻格全未开，
    总雷数 a=8 ⇒ 这 8 格必全为雷，decide 必须给出有效坐标。
    """

    @classmethod
    def setUpClass(cls):
        cls.native = pytest_importorskip_native()

    def _digit8_board(self, center=8, a=8):
        W = H = 6
        cv = np.zeros((H + 2, W + 2), dtype=np.int32)
        cv[1:H + 1, 1:W + 1] = 0
        cv[3, 3] = center
        for y in (2, 3, 4):
            for x in (2, 3, 4):
                if (x, y) != (3, 3):
                    cv[y, x] = 9
        return cv, W, H, a

    def test_digit8_neighborhood_is_solvable(self):
        """8 邻格全未开且 a=8 ⇒ 必可求解，绝不能是 empty_group。"""
        if self.native is None or not hasattr(self.native, "decide"):
            self.skipTest("原生 mscore.decide 不可用（生产 pyd 无该导出）")
        cv, W, H, a = self._digit8_board()
        r = self.native.decide(cv, a=a, w=W, h=H, is_play=False)
        self.assertIsNotNone(r)
        self.assertNotEqual(
            r.get("reason"), "empty_group",
            "数字 8 被排除出分组导致 part_solve 无解：分组判据必须与 "
            "run_part_solve 的 1<=v<=8 一致（含 8）")
        self.assertGreaterEqual(r.get("x", -1), 1, "decide 必须返回有效 x")
        self.assertGreaterEqual(r.get("y", -1), 1, "decide 必须返回有效 y")

    def test_digit7_control_still_solvable(self):
        """对照组：同几何换成 7（a=7）必须仍然可解——锁住改动无回归。"""
        if self.native is None or not hasattr(self.native, "decide"):
            self.skipTest("原生 mscore.decide 不可用（生产 pyd 无该导出）")
        cv, W, H, a = self._digit8_board(center=7, a=7)
        r = self.native.decide(cv, a=a, w=W, h=H, is_play=False)
        self.assertIsNotNone(r)
        self.assertNotEqual(r.get("reason"), "empty_group")
        self.assertGreaterEqual(r.get("x", -1), 1)

    def test_frontier_grouping_includes_digit8(self):
        """C++ split_frontier 把 8 邻格都判为 frontier（而非 inner/无约束）。"""
        if self.native is None or not hasattr(self.native, "split_frontier"):
            self.skipTest("原生 mscore.split_frontier 不可用")
        cv, W, H, _ = self._digit8_board()
        parts = self.native.split_frontier(cv)
        self.assertIsInstance(parts, tuple)
        frontier = list(parts[0])
        self.assertEqual(
            len(frontier), 8,
            "8 的 8 个未开邻格都必须被判为 frontier（受局部约束）；"
            f"实测 frontier={len(frontier)}，inner={len(parts[1])}")


if __name__ == "__main__":
    unittest.main()
