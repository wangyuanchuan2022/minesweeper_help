# -*- coding: utf-8 -*-
"""P0-1 回归：帮助模式「点击所有确定方格」不得点击非确定格。

评审发现（review2/20261109_audit_all/architect.md P0-1，逐字锚定成立）：
  utils/probability.py:1027-1028 无条件把 decide 的 best 坐标塞进
  _safe_cells；候选表所有项 is_best=True；main.py:2063 以 is_best 判点击
  ⇒ 「最佳但不确定」的格子被当成「确定安全」批量点击。

本测试锁住修复后的语义：
  - proven_safe=True 仅来自 prob>=0.999999 或 certain_safe 的 tied
  - _help_action_targets 只按 proven_safe 收集点击目标
  - best 但仍不确定的格子保留 is_best（UI 推荐标记），但绝不进 clicks
"""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestHelpActionTargetsProvenSafe(unittest.TestCase):
    """main._help_action_targets 的点击集合只收 proven_safe。"""

    @staticmethod
    def _call(candidates, cv=None, prob_map=None):
        # 不实例化完整 MyMainWindow（需 Qt/真实窗口），直接以最小对象
        # 绑定未绑定方法；属性按 main.py:2034-2064 的真实契约逐个补齐。
        import main
        import numpy as np
        from types import SimpleNamespace
        board = cv if cv is not None else np.full((18, 32), 9, dtype=np.int32)
        obj = SimpleNamespace(
            _last_heatmap_payload={"prob": prob_map or {}, "cell_value": board},
            _last_candidates=candidates,
            _last_cell_value=board,
            _conf=lambda item: float(item.get("confidence", 0.0)),
            w=30, h=16,
        )
        return main.MyMainWindow._help_action_targets(obj)

    def test_only_proven_safe_cells_are_clicked(self):
        # 真实编码：cv 里 9=未开；三个未开格
        import numpy as np
        cv = np.full((18, 32), 9, dtype=np.int32)
        cands = [
            {"pos": (5, 5), "confidence": 1.0, "is_best": True,
             "is_mine": False, "proven_safe": True},      # 真确定安全
            {"pos": (6, 5), "confidence": 0.72, "is_best": True,
             "is_mine": False, "proven_safe": False},     # best 但不确定
            {"pos": (7, 5), "confidence": 0.0, "is_mine": True,
             "is_best": False, "proven_safe": False},     # 确定雷
        ]
        clicks, mines = self._call(cands, cv=cv)
        self.assertIn((5, 5), clicks)
        self.assertNotIn((6, 5), clicks, "best 但非确定的格必须不能被批量点击")
        self.assertIn((7, 5), mines)

    def test_legacy_candidate_without_proven_safe_not_clicked(self):
        # 兼容：没有 proven_safe 字段的旧候选不能因 is_best 被点（fail-closed）
        import numpy as np
        cv = np.full((18, 32), 9, dtype=np.int32)
        cands = [{"pos": (3, 3), "confidence": 0.95,
                  "is_best": True, "is_mine": False}]
        clicks, _ = self._call(cands, cv=cv)
        self.assertNotIn((3, 3), clicks)

    def test_confidence_one_without_proven_safe_still_not_clicked(self):
        # 关键负向：confidence=1.0 也不能替代 proven_safe（旧逻辑的漏洞）
        import numpy as np
        cv = np.full((18, 32), 9, dtype=np.int32)
        cands = [{"pos": (2, 2), "confidence": 1.0,
                  "is_best": True, "is_mine": False, "proven_safe": False}]
        clicks, _ = self._call(cands, cv=cv)
        self.assertNotIn((2, 2), clicks)

    def test_prob_map_proven_safe_still_clicked(self):
        import numpy as np
        cv = np.full((18, 32), 9, dtype=np.int32)
        clicks, _ = self._call([], cv=cv, prob_map={(4, 4): 1.0})
        self.assertIn((4, 4), clicks)


if __name__ == "__main__":
    unittest.main()
