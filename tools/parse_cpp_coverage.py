# -*- coding: utf-8 -*-
"""解析 mscore_pure.inc.gcov，产出 C++ 纯算法段的真实行覆盖率。

口径（必须随读数一起声明）：
  - 分母 = mscore.cpp 纯算法段（L1..PURE_END）里 gcov 标记为「可执行」的行
    （行首是数字/#####/===== 而非 '-'）。
  - 分子 = 执行次数 >= 1 的行。
  - 不含 pybind 绑定层（L1767 之后）、不含 C++ 分支覆盖、不含系统头。
  - 与 Python 侧 tools/coverage_probe.py 的语句行口径分列，不相加成一个数。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_cov"
GCOV = OUT / "mscore_pure.inc.gcov"
REPORT = ROOT / "state" / "audit" / "cpp_coverage.json"

LINE = re.compile(r"^\s*([^:]*):\s*(\d+):(.*)$")


def main():
    if not GCOV.exists():
        print("FATAL: 缺少", GCOV, " 先跑 tools/cpp_coverage.cmd")
        sys.exit(1)
    denom = hit = 0
    missed = []
    for raw in GCOV.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        mark, lineno, code = m.group(1).strip(), int(m.group(2)), m.group(3)
        if mark == "-":
            continue                      # 非执行行（注释/空行/声明）
        denom += 1
        # 数字 = 执行次数；##### / ===== = 未执行
        if re.fullmatch(r"\d+", mark) and int(mark) >= 1:
            hit += 1
        else:
            missed.append(lineno)
    pct = round(100.0 * hit / denom, 2) if denom else 0.0
    out = {
        "instrument": "MinGW g++ --coverage + gcov（tools/cpp_coverage.cmd）",
        "scope": "cpp/mscore.cpp 纯算法段 L1..PURE_END（decision_layer 第一段结束前）",
        "excluded": [
            "pybind11 绑定层（L1767 之后：py_decide / decide_impl / PYBIND11_MODULE）",
            "abnative_bridge 桥接函数（仅 AB_NATIVE_MERGED 下存在）",
            "系统头 / 标准库 / pybind11 头",
            "C++ 分支覆盖（本次仅语句行覆盖）",
        ],
        "denominator_lines": denom,
        "hit_lines": hit,
        "overall_pct": pct,
        "missed_lines_count": len(missed),
        "missed_lines_first40": missed[:40],
        "caveat": "与 Python 侧 tools/coverage_probe.py 口径分列，不相加成一个总覆盖率",
    }
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("CPP PURE-SEGMENT LINE COVERAGE: %d/%d = %.2f%%" % (hit, denom, pct))
    print("missed lines:", len(missed), "first40:", missed[:40])
    print("report:", REPORT)


if __name__ == "__main__":
    main()
