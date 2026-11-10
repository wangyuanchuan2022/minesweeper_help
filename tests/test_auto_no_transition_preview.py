# -*- coding: utf-8 -*-
"""P0-3 回归：Auto 界面只显示热力图，不接状态转移预览（用户铁律）。

评审发现（review2 architect P0-3）：main.update_heatmap 在 Auto 页给所有
有概率的格挂 hover 预览，_preview_payload_for_heatmap_cell 会同步调用
C++ transitions_for，缓存未命中时最多重建 20 万世界，且冻结 GUI 主线程。
用户明确要求「Auto 界面不需要接这个状态转移接线，只需要显示热力图就可以了」。

本测试锁住：update_heatmap 不再给 heatmap_btn_list 挂预览。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"


class TestAutoNoTransitionPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MAIN.read_text(encoding="utf-8")

    def _method_body(self, name):
        """截取 def <name>(...) 到下一个顶层 def 之间的源码（不含注释行判断）。"""
        lines = self.text.splitlines()
        start = None
        for i, ln in enumerate(lines):
            if re.match(r"\s*def\s+" + re.escape(name) + r"\s*\(", ln):
                start = i
                break
        self.assertIsNotNone(start, "找不到方法 " + name)
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = len(lines)
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if ln.strip() and not ln.lstrip().startswith("#"):
                cur = len(ln) - len(ln.lstrip())
                if cur <= indent and re.match(r"\s*def\s+", ln):
                    end = j
                    break
        return "\n".join(lines[start:end])

    def test_update_heatmap_does_not_attach_preview_to_heatmap_grid(self):
        body = self._method_body("update_heatmap")
        real = [ln for ln in body.splitlines()
                if not ln.lstrip().startswith("#")]
        joined = "\n".join(real)
        self.assertNotIn("_attach_preview(", joined,
                         "Auto 页 update_heatmap 仍在挂 hover 预览（P0-3）")

    def test_help_preview_provider_still_exists(self):
        # 帮助页的 7x7 预览是用户要的功能，不能一起删
        self.assertIn("def _preview_payload_for_candidate", self.text)
        self.assertIn("def _preview_payload_for_board_cell", self.text)

    def test_transitions_for_cell_still_available_for_help(self):
        self.assertIn("def _transitions_for_cell", self.text)


if __name__ == "__main__":
    unittest.main()
