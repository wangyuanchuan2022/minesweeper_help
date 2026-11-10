# -*- coding: utf-8 -*-
"""解析 gcov JSON（整份 cpp/mscore.cpp 插桩 pyd 的真实运行计数），
产出「哪些函数实测零调用」的权威清单。

为什么用 gcov JSON 而不是 .gcov 文本：
  · .gcov 文本行前缀只给「该行执行次数」，函数边界要自己猜（缩进启发式会错）；
  · gcov -j 的 JSON 直接给出每个函数的 demangled_name / start_line / end_line /
    execution_count / blocks / blocks_executed，是编译器插桩的真值，不需要启发式。
  · 权威判据：execution_count == 0 ⇒ 本次负载下该函数**一次都没被调用**。

口径（必须随读数一起声明）：
  - 覆盖率分母 = JSON 中属于 cpp/mscore.cpp 的可执行行；
  - 「零调用函数」= execution_count == 0 的函数，**仅对本次负载成立**
    （负载 = 400 真实中局盘面 × auto/help + 2396 次悬停转移 + 300 局真实雷位整局重放）；
  - 零调用 ≠ 死代码：可能只是本次负载没触发它的输入（如异常路径、超大棋盘兜底）。
    报告里必须区分「结构性不可达」（宏开关 / 无任何调用点）与「样本未覆盖」。

输出：
  state/audit/cpp_line_coverage.json     行覆盖 + 函数清单
  state/audit/cpp_zero_hit_functions.md  人读报告
"""
import gzip
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_line_cov"
JSONGZ = OUT / "mscore_cov.pyd-mscore.gcov.json.gz"
SRC = ROOT / "cpp" / "mscore.cpp"
REPORT_JSON = ROOT / "state" / "audit" / "cpp_line_coverage.json"
REPORT_MD = ROOT / "state" / "audit" / "cpp_zero_hit_functions.md"


def load_mscore_json():
    if not JSONGZ.exists():
        print("FATAL: 缺少", JSONGZ, "先跑 tools/cpp_line_cov_driver.py 再 gcov -j")
        sys.exit(1)
    d = json.load(gzip.open(JSONGZ, "rt", encoding="utf-8"))
    for f in d["files"]:
        if f["file"].replace("\\", "/").endswith("cpp/mscore.cpp"):
            return f
    print("FATAL: gcov JSON 里找不到 cpp/mscore.cpp")
    sys.exit(1)


# 宏区块（行号区间）——用于给零调用函数打「结构性」标签
# 行号区间仅作标签用途（2026-11-10 后 AB_NATIVE_MERGED 桥接块已整块删除，
# 保留空表项会让 macro_of() 给已不存在的行打错标签，故按当前源码重设）。
# 说明：这里只用于把零调用函数归类到「Python 不可达 / trial 块内 / 生产可达」，
# 不影响覆盖率分母与分子。
MACRO_BLOCKS = [
    ("MSW_TRIAL_DECIDE", 1567, 2968),
]


def macro_of(line):
    for name, lo, hi in MACRO_BLOCKS:
        if lo <= line <= hi:
            return name
    return ""


def main():
    f = load_mscore_json()
    src = SRC.read_text(encoding="utf-8", errors="replace").splitlines()

    # ---- 行覆盖 ----
    lines = f["lines"]
    denom = [ln for ln in lines if ln["line_number"] > 0]
    hit = [ln for ln in denom if ln["count"] > 0]
    zero = [ln for ln in denom if ln["count"] == 0]

    # ---- 函数覆盖（编译期真值）----
    fns = []
    for fn in f["functions"]:
        sl = fn["start_line"]
        fns.append({
            "name": fn["demangled_name"],
            "mangled": fn["name"],
            "start_line": sl,
            "end_line": fn["end_line"],
            "execution_count": fn["execution_count"],
            "blocks": fn["blocks"],
            "blocks_executed": fn["blocks_executed"],
            "macro": macro_of(sl),
        })
    fns.sort(key=lambda r: r["start_line"])

    never = [r for r in fns if r["execution_count"] == 0]
    partial_blocks = [r for r in fns if r["execution_count"] > 0
                      and r["blocks_executed"] < r["blocks"]]

    # ---- 按「是否 Python 可达」分类零调用函数 ----
    py_unreachable = [r for r in never if r["macro"] in ("AB_NATIVE_MERGED", "#if 0 legacy")]
    trial_only = [r for r in never if r["macro"] == "MSW_TRIAL_DECIDE"]
    production = [r for r in never if r["macro"] == ""]

    report = {
        "instrument": "MinGW g++ 16.1.0 -O0 --coverage -shared（整份 cpp/mscore.cpp 编成可 import 的 pyd）",
        "workload": ("400 真实中局盘面 × (decide auto + decide help + split_frontier) + "
                     "2396 次 transitions_for 悬停转移 + 300 局真实雷位整局重放（7598 次 decide）"),
        "scope": "cpp/mscore.cpp 全文件（含 pybind 绑定层；与 cpp_cov_build.py 纯算法段口径分列，不相加）",
        "line_coverage": {
            "denominator_lines": len(denom),
            "hit_lines": len(hit),
            "pct": round(100.0 * len(hit) / len(denom), 2) if denom else 0.0,
            "zero_hit_lines": len(zero),
        },
        "function_coverage": {
            "total": len(fns),
            "called": len(fns) - len(never),
            "never_called": len(never),
            "never_called_python_unreachable": len(py_unreachable),
            "never_called_trial_decide": len(trial_only),
            "never_called_production_reachable": len(production),
        },
        "never_called_functions": never,
        "partial_block_functions": [
            {"name": r["name"], "start_line": r["start_line"],
             "blocks": r["blocks"], "blocks_executed": r["blocks_executed"]}
            for r in sorted(partial_blocks, key=lambda r: r["start_line"])],
        "caveat": ("零调用 = 本次负载未触发，不等于死代码；已按宏区块标注 "
                   "AB_NATIVE_MERGED / MSW_TRIAL_DECIDE / 生产可达 三类。"),
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 人读报告 ----
    L = []
    L.append("# C++ 零调用函数清单（实测取证）\n")
    L.append("## 口径\n")
    L.append("- 仪器：MinGW g++ 16.1.0 `-O0 --coverage -shared`，把**整份** "
             "`cpp/mscore.cpp` 编成可 import 的 pyd（宏 `MSW_TRIAL_DECIDE` 与生产一致）")
    L.append("- 负载：" + report["workload"])
    L.append("- 判据：`execution_count == 0` ⇒ 本次负载下该函数**一次都没被调用**"
             "（编译器插桩真值，非静态推断）")
    L.append("- **零调用 ≠ 死代码**：可能只是本次负载没触发它的输入。下文分三类标注。\n")
    L.append("## 总览\n")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    lc = report["line_coverage"]
    fc = report["function_coverage"]
    L.append(f"| 行覆盖 | {lc['hit_lines']}/{lc['denominator_lines']} = **{lc['pct']}%** "
             f"（零命中 {lc['zero_hit_lines']} 行） |")
    L.append(f"| 函数总数 | {fc['total']} |")
    L.append(f"| 被调用 | {fc['called']} |")
    L.append(f"| **零调用** | **{fc['never_called']}** |")
    L.append(f"| └ 其中 Python 不可达（AB_NATIVE_MERGED / #if 0） | "
             f"{fc['never_called_python_unreachable']} |")
    L.append(f"| └ 其中 MSW_TRIAL_DECIDE 块内 | {fc['never_called_trial_decide']} |")
    L.append(f"| └ 其中生产可达 | {fc['never_called_production_reachable']} |")
    L.append("")

    def dump(title, rows, note=""):
        L.append(f"## {title}\n")
        if note:
            L.append(note + "\n")
        if not rows:
            L.append("（无）\n")
            return
        L.append("| 函数 | 行区间 | 行数 | 宏块 |")
        L.append("|---|---|---|---|")
        for r in rows:
            body = MAX_END - r["start_line"] if False else r["end_line"] - r["start_line"] + 1
            L.append(f"| `{r['name']}` | L{r['start_line']}..{r['end_line']} | {body} | "
                     f"{r['macro'] or '生产'} |")
        L.append("")

    dump("A. Python 完全不可达（宏关闭时根本不编译）", py_unreachable,
         "这些函数在**生产 pyd 里不存在**，只在 `AB_NATIVE_MERGED` 合并构建或 "
         "`#if 0` 历史块里出现。删不删不影响生产二进制，但会影响合并构建。")
    dump("B. MSW_TRIAL_DECIDE 块内但本次零调用", trial_only,
         "生产 pyd 会编译这些函数，但本次负载没有触发。需逐个判断是「输入未覆盖」"
         "还是「只被已删导出调用」。")
    dump("C. 生产可达但本次零调用", production,
         "**最需要关注的一类**：生产 pyd 会编译，且不在任何宏保护内，但实测零调用。")

    REPORT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")

    print("C++ WHOLE-FILE: %d/%d = %.2f%% lines; functions total=%d called=%d NEVER=%d"
          % (lc["hit_lines"], lc["denominator_lines"], lc["pct"],
             fc["total"], fc["called"], fc["never_called"]))
    print("  unreachable(Python)=%d  trial_decide_zero=%d  production_zero=%d"
          % (len(py_unreachable), len(trial_only), len(production)))
    print("\n-- A. Python 不可达 --")
    for r in py_unreachable:
        print("  %-52s L%d..%d  [%s]" % (r["name"][:52], r["start_line"], r["end_line"], r["macro"]))
    print("\n-- B. MSW_TRIAL_DECIDE 内零调用 --")
    for r in trial_only:
        print("  %-52s L%d..%d" % (r["name"][:52], r["start_line"], r["end_line"]))
    print("\n-- C. 生产可达零调用 --")
    for r in production:
        print("  %-52s L%d..%d" % (r["name"][:52], r["start_line"], r["end_line"]))
    print("\nreport:", REPORT_JSON)
    print("report:", REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
