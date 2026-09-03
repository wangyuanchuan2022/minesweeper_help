# -*- coding: utf-8 -*-
"""真实文件读写测试：_ps_record_ms / _tb_save / _tb_load（路径注入临时目录）。

与 TestTimeBudget/TestWrFeedback 的 mock 隔离不同，本类刻意不做 mock——
把路径常量 patch 到临时目录，验证真实的"读-改-写+原子替换"与加载容错逻辑。
"""
import json
import os
import shutil
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRealFileIO(unittest.TestCase):
    """持久化层真实 IO：样本文件与模型状态文件的写入/追加/容错。"""

    def setUp(self):
        # 用工作区内临时目录：系统 Temp 在 DSH 沙箱下 cleanup 会被拒绝
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._tmpdir = os.path.join(root, ".tools", "tmp", uuid.uuid4().hex)
        os.makedirs(self._tmpdir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self._tmpdir, True)
        self.samples = os.path.join(self._tmpdir, "data_time.json")
        self.state = os.path.join(self._tmpdir, "state", "time_budget.json")
        p1 = mock.patch("utils.probability._PS_SAMPLES_PATH", self.samples)
        p2 = mock.patch("utils.probability._TB_STATE_PATH", self.state)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_record_creates_valid_json_and_appends(self):
        from utils.probability import _ps_record_ms
        rows = _ps_record_ms(22, 11.4)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)
        rows = _ps_record_ms(33, 2.3)
        self.assertEqual(len(rows), 2)
        with open(self.samples, encoding="utf-8") as f:   # 文件始终是合法 JSON 数组
            data = json.load(f)
        self.assertEqual(data[-1]["size"], 33)
        self.assertEqual(data[-1]["ms"], 2.3)
        self.assertIsInstance(data[-1]["native"], bool)

    def test_record_filters_invalid_observations(self):
        from utils.probability import _ps_record_ms
        self.assertIsNone(_ps_record_ms(33, 0.001))      # 亚毫秒构造值
        self.assertIsNone(_ps_record_ms(33, float("nan")))
        self.assertFalse(os.path.exists(self.samples))   # 全被过滤 → 不落盘

    def test_tb_save_load_roundtrip(self):
        from utils.probability import _tb_save, _tb_load
        st = {"ps_k": 1.11, "ps_base_ms": 5.8, "ps_native": True,
              "wr": {"t": 9.0, "limitation": 8.5, "ms": 1500.0}}
        _tb_save(st)
        self.assertEqual(_tb_load(), st)

    def test_tb_load_missing_or_corrupt_returns_empty(self):
        from utils.probability import _tb_load
        self.assertEqual(_tb_load(), {})                 # 文件不存在
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{broken")                           # 损坏内容
        self.assertEqual(_tb_load(), {})

    def test_ps_read_samples_missing_returns_empty(self):
        from utils.probability import _ps_read_samples
        self.assertEqual(_ps_read_samples(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
