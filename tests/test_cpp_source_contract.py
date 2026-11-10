# -*- coding: utf-8 -*-
"""C++ 源码契约静态回归（P0-2 / P0-4）。

不重编译 pyd（用户铁律：运行时 pyd 被锁定、不擅自替换），改为对
cpp/mscore.cpp 源码做逐字契约检查，锁住两席评审的 P0 修复：
  - P0-2：counts 不得是固定 64 行栈数组（无界 nTied 会越界写）
  - P0-4：决策层 mem 预算必须钳死 DEFAULT_MEM_BUDGET，不被形参穿透
  - 语法检查由 output/_cpp_syntax_check.cmd（cl /Zs）在提交前跑过
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "cpp" / "mscore.cpp"


class TestCppSourceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SRC.read_text(encoding="utf-8")

    def test_p0_2_no_fixed_64_counts_array(self):
        # 只检查真实声明行；注释里引用旧代码不算（L1-14 门线误触发教训）
        decl_pat = re.compile(r"\bint\s+counts\s*\[\s*64\s*\]")
        bad = [ln for ln in self.text.splitlines()
               if not ln.lstrip().startswith("//") and decl_pat.search(ln)]
        self.assertEqual(bad, [], "counts[64][10] 固定数组仍存在，P0-2 未修复")
        # 必须改为按 nTied 动态分配
        self.assertRegex(
            self.text,
            r"std::vector<std::array<int,\s*10>>\s+counts\s*\(\s*nTied",
        )

    def test_p0_2_no_unbounded_index_without_dynamic_counts(self):
        # counts[ti][mn]++ 仍在（算法不变），但 counts 已是 vector
        self.assertIn("counts[ti][mn]++", self.text)

    def test_p0_4_mem_budget_clamped_to_default(self):
        # 决策层不得再出现 mem_budget > 0 的条件赋值
        self.assertNotIn("e.budget = mem_budget > 0", self.text)
        self.assertIn("e.budget = DEFAULT_MEM_BUDGET;", self.text)

    def test_p0_4_py_decide_marks_mem_budget_unused(self):
        # 形参保留但显式标注未消费，防止后人误以为还能调
        self.assertIn("(void)mem_budget;", self.text)

    def test_p0_4_export_docstring_states_fixed_budget(self):
        self.assertIn(
            "always enforces the fixed", self.text,
            "pybind 导出说明必须声明 mem_budget 不生效")

    def test_syntax_check_script_exists(self):
        # 提交前的语法门脚本随仓库保留，供后续重编译前复跑
        self.assertTrue((ROOT / "tools" / "cpp_syntax_check.cmd").exists())


if __name__ == "__main__":
    unittest.main()
