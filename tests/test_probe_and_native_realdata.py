# -*- coding: utf-8 -*-
"""第二轮覆盖率补测：E2E 探针 + native 包装 + decide_trace。

全部用真实数据：
  - 真实 C++ decide 返回结构（取自 state/decide_trace.jsonl 的字段集合）
  - 真实 game_end 事件字段（取自 state/e2e_900 分析结论口径）
  - 真实 1.5GB 内存预算 / DSTAR=256 / 帮助×3 常量
探针写盘重定向到临时目录，绝不污染 state/e2e/ 的真实产物。
"""
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REAL_TRACE = ROOT / "state" / "decide_trace.jsonl"


def _load_probe(tmpdir):
    """在隔离环境重新加载 e2e_probe（模块级常量在 import 时求值）。

    返回 (module, restore)：restore() 在测试 cleanup 时还原环境变量。
    注意：不能在函数内立刻还原——模块级 _DIR/_PATH 已按当时的 env 定值，
    但测试期间仍须保持 env 一致，避免其它 reload 误指向真实 state/e2e。
    """
    for name in list(sys.modules):
        if name == "utils.e2e_probe" or name.endswith(".e2e_probe"):
            del sys.modules[name]
    saved = {k: os.environ.get(k) for k in
             ("MSW_E2E_DIR", "MSW_E2E_PROBE", "MSW_E2E_MEM_HZ", "MSW_E2E_SLOW_MS")}
    os.environ["MSW_E2E_DIR"] = str(tmpdir)
    os.environ["MSW_E2E_PROBE"] = "1"
    os.environ["MSW_E2E_MEM_HZ"] = "0"
    os.environ["MSW_E2E_SLOW_MS"] = "0"

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    import utils.e2e_probe as p
    importlib.reload(p)
    return p, restore


def _real_decide_rows(n=30):
    rows = []
    if not REAL_TRACE.exists():
        return rows
    with REAL_TRACE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
            if len(rows) >= n:
                break
    return rows


class TestE2EProbeFullLifecycle(unittest.TestCase):
    """探针全部公开 API：隔离目录 + 真实字段结构。

    临时目录放在工作区 output/ 内：沙箱对系统 Temp 无删除权限，
    tempfile.TemporaryDirectory 清理会 WinError 5（假红，非代码缺陷）。
    """

    def setUp(self):
        self.tmp = ROOT / "output" / ("_probe_tmp_%d" % os.getpid())
        self.tmp.mkdir(parents=True, exist_ok=True)
        for f in self.tmp.iterdir():
            if f.is_file():
                f.unlink()
        self.p, self._restore = _load_probe(self.tmp)
        self.addCleanup(self._cleanup_tmp)

    def _cleanup_tmp(self):
        self._restore()
        for f in self.tmp.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    def test_enabled_and_path(self):
        self.assertTrue(self.p.enabled())
        self.assertTrue(self.p.path().endswith("probe.jsonl"))

    def test_disabled_when_stopped(self):
        self.p._stopped[0] = True
        self.assertFalse(self.p.enabled())
        self.p._stopped[0] = False

    def test_game_begin_end_idempotent_real_events(self):
        p = self.p
        self.assertFalse(p.game_active())
        p.game_begin()
        self.assertTrue(p.game_active())
        p.game_begin()                 # 幂等：不重开
        s = p.game_end(won=True, total=2057, win=808)
        self.assertEqual(s["won"], True)
        self.assertEqual(s["total"], 2057)
        self.assertIn("wall_ms", s)
        self.assertIn("peak_mem_mb", s)
        self.assertFalse(p.game_active())
        self.assertEqual(p.game_end(won=False), {})   # 未在局中返回空
        p.game_begin(idx=7)
        s2 = p.game_end(won=False)
        self.assertEqual(s2["game"], 7)

    def test_span_normal_and_exception_paths(self):
        p = self.p
        with p.span("vision.grab_board", cells=480):
            pass
        try:
            with p.span("vision.match_template"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with p.span("ui.render_grid", cells=480) as on:
            self.assertTrue(on)

    def test_span_slow_filter(self):
        p = self.p
        p._SLOW_MS = 1e9   # 所有 span 都被过滤
        with p.span("ignored"):
            pass
        p._SLOW_MS = 0.0

    def test_decision_with_real_trace_fields(self):
        rows = _real_decide_rows(10)
        p = self.p
        if rows:
            p.decision(rows[0], 0.456, tag="first")
            p.decision(rows[0], 34.0, tag="loop")
        p.decision(None, 1.0)                       # decide 返回 None 分支
        p.decision({"prob": [0.1] * 5, "tied": [(1, 2)], "transitions": []},
                   2.0)                                # len 分支
        p.decision({"prob": 3}, 1.0)                # TypeError→0 分支

    def test_click_mark_error_real_payload(self):
        p = self.p
        p.click(12, 141, conf=0.8132, reason="certain_safe", source="decide")
        p.click(1, 1)                               # conf=None 分支
        p.mark("vision.low_confidence_abort", context="game_end", unknown=438)
        p.error("screen_grab_failed", "OSError: screen grab failed", mode="auto")

    def test_write_summary_and_events_written(self):
        p = self.p
        p.game_begin()
        p.mark("ui.heatmap_frame", n=1)
        p.game_end(won=False)
        s = p.write_summary(extra={"tag": "selftest"})
        self.assertIsNotNone(s)
        self.assertEqual(s["extra"], {"tag": "selftest"})
        self.assertIn("python", s)
        self.assertGreaterEqual(s["events"], 3)
        # 真实落盘可读
        lines = Path(p.path()).read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 3)
        types = {json.loads(x)["type"] for x in lines}
        self.assertIn("game_begin", types)
        self.assertIn("game_end", types)
        sp = self.tmp / "probe_summary.json"
        self.assertTrue(sp.exists())
        self.assertEqual(json.loads(sp.read_text(encoding="utf-8"))["games"], 1)

    def test_file_size_limit_stops_writing(self):
        p = self.p
        # 真实实现每 500 条（_seq % 500 == 0）才检查一次大小，且要求文件已存在。
        # 故先造一个超限文件、把 seq 推到 499，下一条写入即触发 500 周期检查。
        Path(p.path()).write_text("x" * 64, encoding="utf-8")
        p._MAX_BYTES = 1
        p._seq[0] = 500
        p._stopped[0] = False
        p.mark("x")
        self.assertTrue(p._stopped[0])
        self.assertFalse(p.enabled())
        txt = Path(p.path()).read_text(encoding="utf-8")
        self.assertIn("probe_stopped", txt)

    def test_mem_sampler_start_stop_idempotent(self):
        p = self.p
        p._MEM_HZ = 0.0
        p.start_mem_sampler()      # HZ=0 直接返回
        p._MEM_HZ = 500.0
        p.start_mem_sampler()
        self.assertIsNotNone(p._mem_thread)
        p.start_mem_sampler()      # 幂等
        p.stop_mem_sampler()
        self.assertTrue(p._mem_stop.is_set())

    def test_mem_now_returns_tuple(self):
        rss, commit = self.p._mem_now()
        self.assertTrue(rss is None or isinstance(rss, float))
        self.assertTrue(commit is None or isinstance(commit, float))

    def test_emit_disabled_no_write(self):
        p = self.p
        p._stopped[0] = True
        before = Path(p.path()).stat().st_size if Path(p.path()).exists() else 0
        p.decision({"reason": "optimal"}, 1.0)
        after = Path(p.path()).stat().st_size if Path(p.path()).exists() else 0
        self.assertEqual(before, after)
        p._stopped[0] = False


class TestNativeWrappers(unittest.TestCase):
    """utils.native 全部分支：真实 pyd 导出面 + 受控不可用 mock。"""

    def test_as_groups_with_none_mscore(self):
        import utils.native as n
        with mock.patch.object(n, "mscore", None):
            self.assertEqual(n.as_groups([[1, 2]]), [[1, 2]])

    def test_as_groups_with_contiguous_conversion(self):
        import numpy as np
        import utils.native as n
        fake = mock.MagicMock()
        with mock.patch.object(n, "mscore", fake):
            out = n.as_groups([np.array([[1, 2]], dtype=np.int64)])
            self.assertEqual(out[0].dtype, np.int32)
            self.assertTrue(out[0].flags["C_CONTIGUOUS"])

    def test_decide_no_mscore_returns_none(self):
        import utils.native as n
        with mock.patch.object(n, "mscore", None):
            self.assertIsNone(n.decide([[9]], 99, 30, 16))

    def test_decide_wr_threshold_not_forwarded(self):
        import utils.native as n
        fake = mock.MagicMock()
        fake.decide.return_value = {"x": 1, "y": 1, "reason": "certain_safe"}
        with mock.patch.object(n, "mscore", fake):
            n.decide([[9]], 99, 30, 16, wr_threshold=999, node_cap=1000)
        kw = fake.decide.call_args.kwargs
        self.assertNotIn("wr_threshold", kw)
        self.assertEqual(kw["node_cap"], 1000)
        self.assertEqual(kw["is_play"], True)

    def test_decide_old_pyd_signature_fallback(self):
        import utils.native as n
        fake = mock.MagicMock()
        calls = []

        def side(*a, **kw):
            calls.append(kw)
            if "is_play" in kw:
                raise TypeError("unexpected keyword 'is_play'")
            return {"reason": "baseline"}

        fake.decide.side_effect = side
        with mock.patch.object(n, "mscore", fake):
            r = n.decide([[9]], 99, 30, 16, is_play=False)
        self.assertEqual(r["reason"], "baseline")
        self.assertNotIn("is_play", calls[-1])

    def test_has_decide_and_has_transitions(self):
        import utils.native as n
        fake = mock.MagicMock(spec=["decide", "transitions_for"])
        with mock.patch.object(n, "mscore", fake):
            self.assertTrue(n.has_decide())
            self.assertTrue(n.has_transitions_for())
        with mock.patch.object(n, "mscore", None):
            self.assertFalse(n.has_decide())
            self.assertFalse(n.has_transitions_for())

    def test_transitions_for_success_and_failure(self):
        import utils.native as n
        fake = mock.MagicMock()
        fake.transitions_for.return_value = {"transitions": [], "reason": "ok"}
        with mock.patch.object(n, "mscore", fake):
            self.assertEqual(n.transitions_for([[9]], 99, 30, 16, 1, 1)["reason"], "ok")
            fake.transitions_for.side_effect = RuntimeError("boom")
            self.assertIsNone(n.transitions_for([[9]], 99, 30, 16, 1, 1))
        with mock.patch.object(n, "mscore", None):
            self.assertIsNone(n.transitions_for([[9]], 99, 30, 16, 1, 1))

    def test_diagnostic_wrappers(self):
        import utils.native as n
        fake = mock.MagicMock()
        fake.estimate_mem.return_value = {"allowed": True}
        fake.split_frontier.return_value = [[1, 2]]
        fake.decision_preflight.return_value = {"allowed": True}
        fake.packed_board_storage.return_value = {"cells": 480}
        with mock.patch.object(n, "mscore", fake):
            self.assertTrue(n.estimate_mem(10, 20)["allowed"])
            self.assertEqual(n.split_frontier([[9]]), [[1, 2]])
            self.assertTrue(n.decision_preflight([[9]], 10)["allowed"])
            self.assertEqual(n.packed_board_storage(480)["cells"], 480)
        with mock.patch.object(n, "mscore", None):
            self.assertIsNone(n.estimate_mem(10, 20))
            self.assertIsNone(n.split_frontier([[9]]))
            self.assertIsNone(n.decision_preflight([[9]], 10))
            self.assertIsNone(n.packed_board_storage(480))

    def test_tuned_env_switch(self):
        import utils.native as n
        with mock.patch.object(n, "available", True), \
             mock.patch.dict(os.environ, {"MSW_NATIVE_TUNE": "0"}):
            self.assertFalse(n.tuned())
        with mock.patch.object(n, "available", True), \
             mock.patch.dict(os.environ, {"MSW_NATIVE_TUNE": "1"}):
            self.assertTrue(n.tuned())
        with mock.patch.object(n, "available", False):
            self.assertFalse(n.tuned())


class TestDecideTraceRealData(unittest.TestCase):
    """utils.decide_trace：用真实 trace 行验证记录格式与关闭开关。"""

    def test_real_trace_schema_matches_module_fields(self):
        import utils.decide_trace as dt
        rows = _real_decide_rows(5)
        self.assertGreater(len(rows), 0, "真实 trace 缺失，跳过")
        keys = set(dt.__dict__.keys())
        # 真实行字段应能被模块识别（至少 reason/ms/session）
        for r in rows:
            self.assertIn("reason", r)
            self.assertIn("ms", r)
        self.assertTrue(any("session" in r for r in rows))

    def test_disabled_switch(self):
        # 模块级 _ENABLED 在 import 时定值；用 reload + 环境变量验证关闭语义
        with mock.patch.dict(os.environ, {"MSW_DECIDE_TRACE": "0"}):
            sys.modules.pop("utils.decide_trace", None)
            import utils.decide_trace as dt
            self.assertFalse(dt._ENABLED)
        sys.modules.pop("utils.decide_trace", None)
        with mock.patch.dict(os.environ, {"MSW_DECIDE_TRACE": "1"}):
            import utils.decide_trace as dt2
            self.assertTrue(dt2._ENABLED)


if __name__ == "__main__":
    unittest.main()
