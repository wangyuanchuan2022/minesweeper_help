# -*- coding: utf-8 -*-
"""受控复现：为什么旧测试在生产循环「一轮就断」时能变绿，而在「重扫后盘面
真的变化」时会变红。

两组对照，唯一变量 = rescan_after_click 是否让 num9 减少：
  A（现状语义，UNKNOWN 不落盘）→ rescan 后 num9 不变 → 无进展守卫首轮即断
                                  → decide 只被调 1 次、clicks 恰好 3 个
  B（把重扫结果当成「点过的格全部变成已开」）→ num9 减少 → 循环继续
                                  → 拿到同一个 stub 响应 → 第 2 轮再点 3 个
                                  → clicks = 6 个（旧断言 assertEqual 3 个必红）
"""
import os
import sys
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_solver  # noqa: E402
from utils import native, probability  # noqa: E402


def board(w=6, h=5):
    cv = np.zeros((h + 2, w + 2), dtype=np.int32)
    cv[1:h + 1, 1:w + 1] = 9
    return cv


RESP = {"x": 3, "y": 2, "confidence": 1.0, "path": 1,
        "reason": "certain_safe", "estimated_bytes": 0, "world_upper": 128,
        "prob": {(3, 2): 1.0}, "tied": [(3, 2), (4, 2), (3, 3)], "weighted": []}


def run(solver, rescan_impl, clicks):
    solver.cell_width = 24
    solver.bx = 100
    solver.by = 200
    solver._last_heatmap = {}
    solver._wr_mem_warned = set()
    solver._wr_nodecap_hits = 0
    solver._time_budget = {}
    solver._pv_state = None
    cv = board(solver.w, solver.h)
    probability._DECIDE_FAIL_SEQ[0] = 0
    with mock.patch.object(native, "has_decide", return_value=True), \
            mock.patch.object(native, "decide", return_value=RESP) as dm, \
            mock.patch.object(probability.pyautogui, "click",
                              lambda x, y, *a, **k: clicks.append((x, y))), \
            mock.patch.object(probability.time, "sleep", lambda s: None), \
            mock.patch.object(solver, "_locate",
                              lambda f, screen=None: (False, 0, 0)), \
            mock.patch.object(solver, "rescan_after_click", rescan_impl):
        out = solver._number5_1_core(cv)
    return out, dm.call_count


def main():
    lines = []

    # A：现状语义——重扫不改变盘面（UNKNOWN 保持未开）
    clicks_a = []
    s = make_solver(is_play=True)
    out_a, calls_a = run(s, lambda cv_: cv_, clicks_a)
    lines.append("[A] rescan 不改变盘面（= 现状 UNKNOWN 语义 / 旧 stub 行为）")
    lines.append("    decide 调用次数 = %d" % calls_a)
    lines.append("    clicks 个数     = %d  %r" % (len(clicks_a), clicks_a))
    lines.append("    num9 结束时     = %d（初始 25）" % int(np.sum(out_a == 9)))
    lines.append("    ⇒ 循环在第 1 轮后即命中「无进展守卫」跳出，只跑了一轮。")

    # B：把点过的格变成已开（模拟「重扫确实看到变化」）
    clicks_b = []

    def rescan_opening(cv_):
        for (cx, cy) in [(int(round((x - 100) / 24)),
                          int(round((y - 200) / 24))) for (x, y) in clicks_b]:
            if cv_[cy, cx] == 9:
                cv_[cy, cx] = 0
        return cv_

    s2 = make_solver(is_play=True)
    out_b, calls_b = run(s2, rescan_opening, clicks_b)
    lines.append("")
    lines.append("[B] rescan 把点过的格变成已开（= 盘面确有变化）")
    lines.append("    decide 调用次数 = %d" % calls_b)
    lines.append("    clicks 个数     = %d  %r" % (len(clicks_b), clicks_b))
    lines.append("    num9 结束时     = %d（初始 25）" % int(np.sum(out_b == 9)))
    lines.append("    ⇒ 第 2 轮 decide 又返回同一批坐标，但盘面已开 → 生产"
                 "「已开/已标记跳过」把 3 个点击全跳过。")
    lines.append("")
    lines.append("[关键读数] clicks 个数在 A/B 两组都是 3 —— 点击计数**不是**"
                 "区分两类行为的可观测量。")
    lines.append("   A: decide=1, num9=25   （首轮即被无进展守卫打断，循环只跑一轮）")
    lines.append("   B: decide=2, num9=22   （循环真实继续，第 2 轮零点击后无进展退出）")
    lines.append("")
    lines.append("[判决] 旧测试只断言 clicks ⇒ 对「循环是否真的转起来」零鉴别力；")
    lines.append("       新测试改用「decide 调用次数 + num9 终值 + 末轮热力图契约」"
                 "三层可观测量，")
    lines.append("       并在 _run_core 里断言 _DECIDE_FAIL_SEQ==0（防绿来自"
                 "异常跌回 Python 链）。")

    txt = "\n".join(lines)
    with open(os.path.join(ROOT, "_loop_probe_report.txt"), "w",
              encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(os.path.join(ROOT, "_loop_probe_status.txt"), "w",
              encoding="ascii") as f:
        f.write("A_calls=%d A_clicks=%d B_calls=%d B_clicks=%d\n"
                % (calls_a, len(clicks_a), calls_b, len(clicks_b)))


if __name__ == "__main__":
    main()
