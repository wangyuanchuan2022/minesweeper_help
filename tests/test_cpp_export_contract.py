# -*- coding: utf-8 -*-
"""C++ 导出面契约回归（2026-11-10 精简后）。

背景：用户要求「C++ 里只保留 decide 两层决策所需的，其他不必要代码都删除」。
本次把导出面从 10 个精简到 3 个，并删除 421 行死代码（MD5 类、py_estimate_mem、
py_decision_preflight、py_part_solve_single 等）。

本测试锁住：
  1) 导出面恰好是 decide / transitions_for / split_frontier 三个
  2) 被删导出确实不在（防止回退）
  3) decide 内部复用的函数本体仍在（不能误删）
  4) 精简未改变决策语义（由 tools/verify_cpp_refactor.py 对拍验证，此处只锁形状）
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "cpp" / "mscore.cpp"

EXPECTED_EXPORTS = {"decide", "transitions_for", "split_frontier"}
REMOVED_EXPORTS = {"estimate_mem", "decision_preflight", "packed_board_storage",
                   "md5", "part_solve", "part_solve_single", "pbs_compute"}
# decide 内部复用、必须保留的函数本体（只是不再导出）
MUST_KEEP_FUNCS = {"py_part_solve", "py_pbs_compute", "py_split_frontier",
                   "py_decide", "py_transitions_for"}


class TestCppExportContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SRC.read_text(encoding="utf-8")

    def _module_block(self):
        """截取生产 PYBIND11_MODULE 块。

        不能用括号配平：m.def 的字符串参数里含 '{'，且块内有
        #ifdef/#else/#endif 预处理行，配平会越过块尾。
        改用「PYBIND11_MODULE 起 → 后随第一个 '#endif  // AB_NATIVE_MERGED'
        （模块入口的收尾标记）」截断。
        """
        lines = self.text.splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("PYBIND11_MODULE"))
        for i in range(start, len(lines)):
            if lines[i].startswith("#endif") and "AB_NATIVE_MERGED" in lines[i]:
                return "\n".join(lines[start:i])
        return "\n".join(lines[start:])

    def test_export_surface_is_minimal(self):
        import re
        block = self._module_block()
        # 去掉注释行再取 m.def 名字
        real = [l for l in block.splitlines() if not l.strip().startswith("//")]
        names = set(re.findall(r'm\.def\(\s*"(\w+)"', "\n".join(real)))
        self.assertEqual(names, EXPECTED_EXPORTS,
                         "导出面必须是 %r，实测 %r" % (EXPECTED_EXPORTS, names))

    def test_removed_exports_absent(self):
        import re
        block = self._module_block()
        real = "\n".join(l for l in block.splitlines()
                         if not l.strip().startswith("//"))
        names = set(re.findall(r'm\.def\(\s*"(\w+)"', real))
        for gone in REMOVED_EXPORTS:
            self.assertNotIn(gone, names, "%s 应已删除" % gone)

    def test_internal_reused_functions_still_exist(self):
        for fn in MUST_KEEP_FUNCS:
            self.assertIn(fn, self.text,
                          "%s 被 decide 链复用，不得删除" % fn)

    def test_md5_class_removed(self):
        """MD5 类定义与静态成员必须已删除（注释里的说明不算）。"""
        real = "\n".join(
            l for l in self.text.splitlines()
            if not l.lstrip().startswith("//"))
        self.assertNotIn("class MD5 {", real)
        self.assertNotIn("MD5::K_", real)
        self.assertNotIn("MD5::S_", real)

    def test_py_estimate_mem_and_preflight_removed(self):
        """被删函数不得以「定义或调用」形式存在。

        注意：源码里保留了「已删除」说明注释（含旧名字），
        因此必须排除注释行再判（L1-14 门线被注释误触发的教训）。
        """
        real = "\n".join(
            l for l in self.text.splitlines()
            if not l.lstrip().startswith("//"))
        self.assertNotIn("py_estimate_mem", real)
        self.assertNotIn("py_decision_preflight", real)
        self.assertNotIn("py_part_solve_single", real)

    def test_decide_still_has_two_layer_structure(self):
        # 论文 Algorithm 3 两层：OPTIMAL 判据 + pbs/PSEQ 出口
        self.assertIn("count_situations_exact", self.text)
        self.assertIn("deterministic_tie_break", self.text)
        self.assertIn("py_pbs_compute", self.text)


if __name__ == "__main__":
    unittest.main()
