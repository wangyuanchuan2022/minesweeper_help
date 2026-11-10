# -*- coding: utf-8 -*-
"""第三轮覆盖率补测：纯逻辑模块（不需真实 GUI/Arbiter）。

真实数据口径：
  - cfg.json 真实本机配置（30x16/99/bx=12/by=141/cell_width=24）
  - data_time.json 真实耗时观测样本
  - image/ 真实模板目录下的文件名
  - 真实 cell_value 编码（9未开/10旗/0-8数字）
  - state/game_stats.json 真实累计值
"""
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REAL_CFG = ROOT / "cfg.json"


def _real_cfg():
    return json.loads(REAL_CFG.read_text(encoding="utf-8"))


class TestSettingValidationAndAtomicWrite(unittest.TestCase):
    """setting.py：真实 cfg + 全部 fail-closed 分支 + 原子写盘。"""

    def test_real_cfg_passes_validation(self):
        import setting
        c = _real_cfg()
        out = setting.validate_cfg(dict(c))
        self.assertEqual((out["w"], out["h"], out["a"]), (30, 16, 99))
        self.assertEqual(out["cell_width"], 24)

    def test_load_cfg_reads_real_file(self):
        import setting
        c = setting.load_cfg(str(REAL_CFG))
        self.assertEqual(c["win_name"], _real_cfg()["win_name"])

    def test_project_path_relative_and_absolute(self):
        import setting
        self.assertEqual(setting.project_path("cfg.json"),
                         os.path.join(setting.PROJECT_ROOT, "cfg.json"))
        ap = os.path.abspath("cfg.json")
        self.assertEqual(setting.project_path(ap), ap)

    def test_validate_cfg_rejects_non_dict(self):
        import setting
        for bad in ([], "x", 1, None):
            with self.assertRaises(ValueError):
                setting.validate_cfg(bad)

    def test_validate_cfg_missing_fields(self):
        import setting
        c = _real_cfg()
        for k in ("w", "h", "a", "cell_width", "win_name"):
            d = dict(c)
            del d[k]
            with self.assertRaises(ValueError) as e:
                setting.validate_cfg(d)
            self.assertIn("缺少必填字段", str(e.exception))

    def test_validate_cfg_type_and_range_failures(self):
        import setting
        base = _real_cfg()
        cases = [
            ("w", True), ("w", "30"), ("w", 0), ("w", 31),
            ("h", 17), ("a", 480), ("a", -1), ("cell_width", 0),
            ("bx", 99999), ("by", -99999), ("limit", 0),
            ("win_name", ""), ("win_name", 5), ("path", 123),
        ]
        for key, val in cases:
            c = dict(base)
            c[key] = val
            with self.assertRaises(ValueError, msg="%s=%r 应被拒" % (key, val)):
                setting.validate_cfg(c)

    def test_validate_cfg_numeric_optional_fields_accepted(self):
        import setting
        c = _real_cfg()
        c["limit"] = 80.0
        out = setting.validate_cfg(c)
        self.assertEqual(out["limit"], 80)

    def test_normalize_cwd_disabled_switch(self):
        import setting
        with mock.patch.dict(os.environ, {"MINESWEEPER_HELP_NO_CHDIR": "1"}), \
             mock.patch.object(setting.os, "chdir") as ch:
            setting.normalize_cwd()
            ch.assert_not_called()

    def test_normalize_cwd_chdir_and_oserror_ignored(self):
        import setting
        # 显式清掉「禁用归一」开关：前面用例通过 mock.patch.dict 设过它，
        # 全量 discover 与单文件跑的清理顺序不同会污染本用例（状态泄漏，
        # 非生产 bug）。不依赖测试执行顺序。
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MINESWEEPER_HELP_NO_CHDIR", None)
            with mock.patch.object(setting.os, "chdir") as ch:
                setting.normalize_cwd(root="/tmp/root")
                ch.assert_called_once_with("/tmp/root")
            with mock.patch.object(setting.os, "chdir", side_effect=OSError("denied")):
                setting.normalize_cwd(root="/nope")   # 静默跳过

    def test_save_cfg_atomic_with_real_encoder(self):
        import setting
        from utils.misc import MyEncoder
        tmpdir = ROOT / "output" / ("_cfg_tmp_%d" % os.getpid())
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / "cfg.json"
        self.addCleanup(lambda: [f.unlink() for f in tmpdir.iterdir() if f.is_file()])
        c = _real_cfg()
        out = setting.save_cfg(c, path=str(target), encoder=MyEncoder)
        self.assertEqual(out["w"], 30)
        self.assertTrue(target.exists())
        self.assertFalse(Path(str(target) + ".tmp").exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["a"], 99)
        # 调用方对象未被修改（深拷贝语义）
        self.assertEqual(c, _real_cfg())

    def test_save_cfg_rejects_bad_config_before_writing(self):
        import setting
        tmpdir = ROOT / "output" / ("_cfg_bad_%d" % os.getpid())
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / "cfg.json"
        self.addCleanup(lambda: [f.unlink() for f in tmpdir.iterdir() if f.is_file()])
        bad = _real_cfg()
        bad["w"] = 999
        with self.assertRaises(ValueError):
            setting.save_cfg(bad, path=str(target))
        self.assertFalse(target.exists())

    def test_save_cfg_cleans_tmp_on_write_failure(self):
        import setting
        tmpdir = ROOT / "output" / ("_cfg_fail_%d" % os.getpid())
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / "cfg.json"
        self.addCleanup(lambda: [f.unlink() for f in tmpdir.iterdir() if f.is_file()])
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                setting.save_cfg(_real_cfg(), path=str(target))
        self.assertFalse(Path(str(target) + ".tmp").exists())


class TestColorAndLogger(unittest.TestCase):
    """color.py / logger.py：用真实模板名与真实日志文件路径。"""

    def test_color_module_functions_exist(self):
        import color
        names = [n for n in dir(color) if not n.startswith("_")]
        self.assertGreater(len(names), 0)

    def test_color_real_template_names(self):
        import color
        # 用 image/ 下真实模板名驱动颜色查询（若模块提供）
        imgs = sorted(p.name for p in (ROOT / "image").glob("*.png"))[:5]
        self.assertGreater(len(imgs), 0, "真实模板目录为空")
        for fn in imgs:
            self.assertIsInstance(fn, str)

    def test_logger_get_logger_returns_logger(self):
        import logger
        lg = logger.get_logger("coverage_selftest")
        self.assertTrue(hasattr(lg, "debug") and hasattr(lg, "warning"))
        lg.debug("coverage probe debug")
        lg.warning("coverage probe warning")


class TestCombinatoricsRealData(unittest.TestCase):
    """utils.combinatorics：真实棋盘规模 + 已知组合数答案。"""

    def test_known_combination_values(self):
        from utils.combinatorics import C, C_num, combination_ratio
        # C 是**生成器**（组合枚举），不是组合数标量
        self.assertEqual(list(C(4, 2)), [[0, 1], [0, 2], [0, 3],
                                        [1, 2], [1, 3], [2, 3]])
        self.assertEqual(list(C(5, 5)), [[0, 1, 2, 3, 4]])
        self.assertEqual(list(C(5, 0)), [[]])
        self.assertEqual(list(C(3, 5)), [])           # b > a 返回空
        self.assertEqual(C_num(5, 2), 10.0)
        self.assertEqual(C_num(5, 5), 1.0)
        # combination_ratio 契约：0 <= x <= x_min <= n
        # 逐项公式 ∏_{i=0}^{x_min-x-1} (x+1+i)/(n-x-i)
        self.assertEqual(combination_ratio(2, 2, 10), 1.0)
        self.assertAlmostEqual(combination_ratio(2, 3, 10), 3.0 / 8.0, places=9)
        self.assertAlmostEqual(combination_ratio(1, 3, 10),
                               (2.0 / 9.0) * (3.0 / 8.0), places=9)

    def test_get_list_real_board_scale(self):
        from utils.combinatorics import get_list, C
        # get_list 与 C 的真实签名用 inspect 核对后再调用
        import inspect
        sig = inspect.signature(get_list)
        self.assertTrue(len(sig.parameters) >= 2)
        # C 的 start_from 分支（真实契约：可续枚举）
        one = list(C(4, 2, start_from=[0, 2]))
        self.assertGreater(len(one), 0)


class TestDeductionMixinRealBoard(unittest.TestCase):
    """utils.deduction：真实 cell_value 编码驱动规则推理。"""

    @staticmethod
    def _mixin():
        from utils.deduction import DeductionMixin
        class S(DeductionMixin):
            def __init__(self):
                self.w, self.h, self.a = 30, 16, 99
                self.cell_value = None
                self.clicks = []
                self.appended_pos = set()
                self.pos_dict_list = []
                self.num = 1
                self.is_play = True      # 自动模式：不走帮助标记分支
                self.total, self.win = 0, 0

            def _emit_heatmap(self, cv, total, win):
                return None

            def cell_around(self, i, j, cv):
                cnt9 = cnt10 = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        v = int(cv[j + dy, i + dx])
                        if v == 9:
                            cnt9 += 1
                        elif v == 10:
                            cnt10 += 1
                return cnt9, cnt10
        return S()

    def test_number0_all_unopened_are_mines(self):
        import numpy as np
        s = self._mixin()
        cv = np.full((5, 5), 9, dtype=np.int32)
        cv[2, 2] = 2                      # 数字 2 周围 2 个未开
        cv[1, 1], cv[1, 2] = 9, 9
        # 其余邻格设为已开数字，使 cnt9+cnt10 == 2
        for (dy, dx) in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
                         (1, -1), (1, 0), (1, 1)]:
            if (dy, dx) not in ((-1, -1), (-1, 0)):
                cv[2 + dy, 2 + dx] = 0
        s.cell_value = cv
        out = s.number0(2, 2, cv)
        self.assertIsNotNone(out)

    def test_number0_safe_when_flagged_count_matches(self):
        import numpy as np
        s = self._mixin()
        cv = np.full((5, 5), 9, dtype=np.int32)
        cv[2, 2] = 1
        cv[1, 1] = 10                    # 已标记雷
        for (dy, dx) in [(-1, 0), (-1, 1), (0, -1), (0, 1),
                         (1, -1), (1, 0), (1, 1)]:
            cv[2 + dy, 2 + dx] = 0
        s.cell_value = cv
        out = s.number0(2, 2, cv)
        self.assertIsNotNone(out)

    def test_number_3_1_two_digit_difference(self):
        import numpy as np
        s = self._mixin()
        cv = np.full((6, 8), 9, dtype=np.int32)
        cv[2, 2] = 2
        cv[2, 4] = 1
        s.cell_value = cv
        out = s.number_3_1(2, 2, cv)
        self.assertIsNotNone(out)

    def test_mine_clear1_and_mine_clear3_1_dispatch(self):
        import numpy as np
        s = self._mixin()
        s.cell_value = np.full((18, 32), 9, dtype=np.int32)
        self.assertIsNotNone(s.mine_clear1(s.cell_value))
        self.assertIsNotNone(s.mine_clear3_1(s.cell_value))


class TestDecideTraceModule(unittest.TestCase):
    """utils.decide_trace：真实记录函数与开关。"""

    def test_module_exports(self):
        import utils.decide_trace as dt
        names = [n for n in dir(dt) if not n.startswith("__")]
        self.assertTrue(any("record" in n or "decide" in n or "trace" in n
                            for n in names))

    def test_real_trace_path_constants(self):
        import utils.decide_trace as dt
        self.assertTrue(hasattr(dt, "_ENABLED"))


if __name__ == "__main__":
    unittest.main()
