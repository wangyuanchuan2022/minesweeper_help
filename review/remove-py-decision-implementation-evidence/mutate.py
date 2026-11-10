# -*- coding: utf-8 -*-
"""方案 A 注入必红负向自测：验证 F1-F5 的断言真的能抓住回归。

用法（在仓库根）：
    D:\anaconda3\envs\py310\python.exe review\remove-py-decision-implementation-evidence\mutate.py V1
    D:\anaconda3\envs\py310\python.exe review\remove-py-decision-implementation-evidence\mutate.py V2

每个变异体做「打补丁 → 跑测试 → 记录是否红 → 还原 → 再跑确认绿」，
结果打印成一行 JSON，供报告引用。**脚本永远还原**（try/finally）。
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
PROB = os.path.join(ROOT, "utils", "probability.py")
SOLVER = os.path.join(ROOT, "utils", "solver.py")

# V1：把 helper 改成「失败时原样 return、不 emit warning、不重扫、不计数」
V1_OLD = """        msg = DECIDE_UNAVAILABLE_MSG.format(where=where or "unknown")
        _DECIDE_FAIL_SEQ[0] += 1"""
V1_NEW = """        msg = DECIDE_UNAVAILABLE_MSG.format(where=where or "unknown")
        if True:  # MUTANT V1: 静默失败——不计数、不重扫、不警告
            return cell_value
        _DECIDE_FAIL_SEQ[0] += 1"""

# V2：把两条失败出口的 return 都改回 pass（悬空控制流 → 函数落空返回 None，
# 即「静默失败」的历史形态：无 ERROR 计数、无安全重扫、无返回值）。
# 注：只把外层 except 的 return 改 pass 是**行为等价**变异体（下行会命中
# has_decide=False 那条 helper），鉴别力为零，故 V2 必须同时去掉尾随 return。
V2_OLD = """                return self._decide_unavailable(cell_value, exc=e, where="decide 异常")"""
V2_NEW = """                pass  # MUTANT V2a: 吞掉异常后继续下行"""
V2B_OLD = """        return self._decide_unavailable(cell_value, where="has_decide=False")"""
V2B_NEW = """        pass  # MUTANT V2b: 尾部出口也去掉 → 函数落空返回 None（静默失败）"""

# V3：删掉 help() 内的 mine_clear1/mine_clear3_1（帮助模式回归）
V3_OLD = """            try:
                for _ in range(2):
                    cell_value = self.complete_scan(cell_value)
                    cell_value = self.mine_clear1(cell_value)
                    cell_value = self.mine_clear3_1(cell_value)
                cell_value = self.mine_clear1(cell_value)
            except ValueError:"""
V3_NEW = """            try:
                for _ in range(2):
                    cell_value = self.complete_scan(cell_value)
                # MUTANT V3: 帮助模式 Py 推理被删
            except ValueError:"""

MUTANTS = {
    "V1": (PROB, [(V1_OLD, V1_NEW)],
           ["tests.test_cpp_decide_wiring"],
           "helper 静默失败 → F1/F3/F4/helper 单测必须红"),
    "V2": (PROB, [(V2_OLD, V2_NEW), (V2B_OLD, V2B_NEW)],
           ["tests.test_cpp_decide_wiring"],
           "两条失败出口都不 return → 函数落空返回 None → F1 必须红"),
    "V3": (SOLVER, [(V3_OLD, V3_NEW)],
           ["tests.test_cpp_decide_wiring", "tests.test_solver_core"],
           "帮助模式 Py 推理被删 → help() 契约测试必须红"),
}


def run_tests(mods):
    cmd = [PY, "-m", "unittest", "-v"] + mods
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def grep_help_mine_clear():
    """Step 3 的 grep 门：help() 内应仍有 3 处 mine_clear 调用。"""
    txt = open(SOLVER, encoding="utf-8").read()
    return txt.count("self.mine_clear1(cell_value)") + txt.count(
        "self.mine_clear3_1(cell_value)")


def main(who):
    path, mutations, mods, expectation = MUTANTS[who]
    backup = path + ".mutant_bak"
    src = open(path, encoding="utf-8").read()
    for old, _new in mutations:
        assert old in src, f"变异点未找到：{who} / {old[:40]!r}"
    shutil.copy2(path, backup)
    result = {"mutant": who, "file": os.path.relpath(path, ROOT),
              "expectation": expectation}
    try:
        for old, new in mutations:
            src = src.replace(old, new, 1)
        open(path, "w", encoding="utf-8", newline="").write(src)
        rc, out = run_tests(mods)
        result["mutated_returncode"] = rc
        result["mutated_is_red"] = rc != 0
        # 抓第一条 FAIL/ERROR 用作证据
        for line in out.splitlines():
            if line.startswith(("FAIL:", "ERROR:")):
                result["first_failure"] = line.strip()
                break
        if not result.get("first_failure"):
            # 变异体产出的 None/A 崩栈也留证
            for line in out.splitlines():
                if "Error" in line or "error" in line:
                    result["first_failure"] = line.strip()[:200]
                    break
        if who == "V3":
            result["help_mine_clear_calls_after_mutation"] = grep_help_mine_clear()
    finally:
        shutil.copy2(backup, path)
        os.remove(backup)
    rc2, out2 = run_tests(mods)
    result["restored_returncode"] = rc2
    result["restored_is_green"] = rc2 == 0
    if who == "V3":
        result["help_mine_clear_calls_after_restore"] = grep_help_mine_clear()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if (result["mutated_is_red"] and result["restored_is_green"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
