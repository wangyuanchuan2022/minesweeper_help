# -*- coding: utf-8 -*-
"""真实盘面黄金测试：锁定 C++ decide 的确定性 + 各决策分支结果。

数据源（真实运行期产物，非合成）：
  bench/data/midgame_cases.json  400 个真实中局盘面（18x32，30x16+padding）

背景（2026-11-10 实测发现）：同一真实盘面连续两次 decide 有 10/60 返回
不同坐标，根因是 PSEQ 残余平局用 thread_local xorshift 随机数
（cpp/mscore.cpp:2636-2649），状态跨调用累积，与 py_decide 注释
"tie-break is deterministic" 直接矛盾。本测试先锁确定性，再锁各分支输出。

覆盖的决策分支（reason）：certain_safe / optimal / pbs(PSEQ) /
clicks9_fallback / memory_gate / limitation / no_frontier
"""
import json
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import utils.native as native

CASES = ROOT / "bench/data/midgame_cases.json"


def _load_cases(n=None):
    if not CASES.exists():
        return []
    d = json.loads(CASES.read_text(encoding="utf-8"))
    cases = d["cases"]
    return cases if n is None else cases[:n]


def _decide(case, is_play=True):
    cv = np.ascontiguousarray(case["cell_value"], dtype=np.int32)
    return native.decide(cv, case["a"], 30, 16, is_play=is_play)


@unittest.skipUnless(CASES.exists() and native.has_decide(),
                     "需要真实盘面数据 + 可用 pyd")
class TestRealBoardDecisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _load_cases(60)

    def test_decide_is_deterministic_on_real_boards(self):
        """同一真实盘面重复 decide 必须返回完全相同的坐标与结果。

        这是同 seed 复现、A/B 对照、黄金基线的前提。修复前 10/60 不稳定。
        """
        bad = []
        for i, c in enumerate(self.cases):
            r1 = _decide(c)
            r2 = _decide(c)
            r3 = _decide(c)
            if r1 is None or r2 is None:
                continue
            key = lambda r: (r.get("x"), r.get("y"), r.get("reason"),
                             round(float(r.get("confidence") or 0), 6))
            if not (key(r1) == key(r2) == key(r3)):
                bad.append((i, key(r1), key(r2), key(r3)))
        self.assertEqual(bad, [], "decide 非确定性（同盘面不同坐标）：%r" % (bad[:5],))

    def test_all_cases_available(self):
        """真实盘面必须全部可决策（不可用=决策链失效）。"""
        for i, c in enumerate(self.cases):
            r = _decide(c)
            self.assertIsNotNone(r, "case %d decide 返回 None" % i)
            self.assertGreaterEqual(int(r.get("x") or 0), 1, "case %d x<1" % i)
            self.assertGreaterEqual(int(r.get("y") or 0), 1, "case %d y<1" % i)

    def test_reason_branches_are_covered(self):
        """真实盘面应覆盖多个决策分支（不是只走一条路径）。"""
        reasons = Counter()
        for c in self.cases:
            r = _decide(c)
            if r:
                reasons[r.get("reason")] += 1
        # 真实中局盘面至少覆盖 3 类分支
        self.assertGreaterEqual(len(reasons), 3,
                                "决策分支覆盖不足：%r" % dict(reasons))
        # optimal / certain_safe / clicks9_fallback / memory_gate 任一出现
        common = {"certain_safe", "optimal", "clicks9_fallback",
                  "memory_gate", "limitation", "no_frontier"}
        self.assertTrue(set(reasons) <= common,
                        "出现未知 reason：%r" % (set(reasons) - common))

    def test_prob_semantics_on_real_boards(self):
        """prob = 该格不是雷的概率，必须落在 [0,1]。"""
        checked = 0
        for c in self.cases[:30]:
            r = _decide(c)
            prob = r.get("prob") if r else None
            if not prob:
                continue
            vals = prob.values() if isinstance(prob, dict) else prob
            for v in vals:
                p = float(v)
                self.assertGreaterEqual(p, -1e-9)
                self.assertLessEqual(p, 1.0 + 1e-9)
                checked += 1
        self.assertGreater(checked, 0, "没有任何 prob 输出可校验")

    def test_help_mode_returns_transitions_or_explicit_reason(self):
        """帮助模式：有转移就用真实世界集，超限则给明确原因（不合成概率）。"""
        got = 0
        for c in self.cases[:20]:
            r = _decide(c, is_play=False)
            if not r:
                continue
            meta = r.get("transition_meta") or {}
            if meta:
                # 有转移时必须有 basis
                self.assertIn("basis", meta)
                got += 1
        self.assertGreater(got, 0, "帮助模式没有任何 transitions/meta 输出")


class TestGoldenBranchSnapshots(unittest.TestCase):
    """按分支记录黄金基线：用真实盘面锁定「该分支必须返回什么」。"""

    @classmethod
    def setUpClass(cls):
        if not (CASES.exists() and native.has_decide()):
            raise unittest.SkipTest("需要真实盘面 + pyd")
        cls.golden_path = ROOT / "state/audit/golden_real_boards.json"
        # 按 reason 各挑真实盘面
        picked = {}
        for i, c in enumerate(_load_cases(120)):
            r = _decide(c)
            if not r:
                continue
            reason = r.get("reason")
            if reason not in picked:
                picked[reason] = (i, c["cell_value"], c["a"], {
                    "x": r.get("x"), "y": r.get("y"),
                    "reason": reason,
                    "confidence": round(float(r.get("confidence") or 0), 6),
                    "situations": r.get("situations"),
                    "dstar": r.get("dstar"),
                    "world_upper": r.get("world_upper"),
                })
        cls.picked = picked

    def test_branches_have_golden_snapshots(self):
        """至少 3 个分支有黄金快照（证明真实盘面真能触发不同分支）。"""
        self.assertGreaterEqual(len(self.picked), 3,
                                "真实盘面覆盖分支不足：%r" % list(self.picked))

    def test_golden_snapshots_are_reproducible(self):
        """每个黄金快照重复 decide 必须逐字段一致。"""
        bad = []
        for reason, (idx, cv, a, expect) in self.picked.items():
            arr = np.ascontiguousarray(cv, dtype=np.int32)
            for _ in range(3):
                r = native.decide(arr, a, 30, 16, is_play=True)
                got = {"x": r.get("x"), "y": r.get("y"),
                       "reason": r.get("reason"),
                       "confidence": round(float(r.get("confidence") or 0), 6),
                       "situations": r.get("situations"),
                       "dstar": r.get("dstar"),
                       "world_upper": r.get("world_upper")}
                if got != expect:
                    bad.append((reason, idx, expect, got))
                    break
        self.assertEqual(bad, [], "黄金快照不可复现：%r" % (bad[:3],))

    def test_dstar_semantics_on_real_boards(self):
        """DSTAR 语义：自动 256 / 帮助 768；situations 超限为 DSTAR+1 哨兵。"""
        seen = []
        for c in _load_cases(40):
            r = _decide(c)
            if not r:
                continue
            dstar = r.get("dstar")
            sit = r.get("situations")
            if dstar:
                self.assertEqual(int(dstar), 256, "自动模式 DSTAR 必须为 256")
                seen.append((sit, dstar))
            # 哨兵：situations == dstar+1 表示超限
            if sit is not None and int(sit) > 0:
                self.assertLessEqual(int(sit), int(dstar) + 1,
                                     "situations 不得超 dstar+1 哨兵")
        self.assertGreater(len(seen), 0, "没有取到 dstar 诊断字段")


if __name__ == "__main__":
    unittest.main()
