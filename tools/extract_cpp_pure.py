# -*- coding: utf-8 -*-
"""从 cpp/mscore.cpp 抽取「纯 C++ 算法段」供 gcov 覆盖率构建使用。

生产源码零改动：只把 L1..PURE_END（namespace decision_layer 第一段结束）
复制成 output/cpp_cov/mscore_pure.inc，driver 用 #include 纳入同一 TU。
行号与 mscore.cpp 前 PURE_END 行一一对应，gcov 报告可直接映射回源文件。

PURE_END 由「}  // namespace decision_layer」的第一次出现动态定位，
不写死行号（源码改动后自动跟随）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "cpp" / "mscore.cpp"
OUT = ROOT / "output" / "cpp_cov" / "mscore_pure.inc"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
end = None
for i, ln in enumerate(lines, 1):
    if ln.strip().startswith("}") and "namespace decision_layer" in ln:
        end = i
        break
if end is None:
    print("FATAL: 找不到 decision_layer 第一段结束标记")
    sys.exit(1)

OUT.parent.mkdir(parents=True, exist_ok=True)
body = "".join(lines[:end])
OUT.write_text(body, encoding="utf-8")
print("PURE_END=%d  lines_written=%d  out=%s" % (end, end, OUT))
