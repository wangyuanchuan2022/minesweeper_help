# -*- coding: utf-8 -*-
"""对拍：重构后的 decide 输出必须与删改前基线逐字段完全一致。

用法：
  python tools/verify_cpp_refactor.py state/audit/cpp_baseline_decisions.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np
import utils.native as native

# tools/ 无 __init__.py，按路径加载 make_cpp_baseline 复用同一序列化口径
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "msw_baseline_mod", str(ROOT / "tools" / "make_cpp_baseline.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
snapshot = _mod.snapshot


def main():
    base_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "state/audit/cpp_baseline_decisions.json"
    base = json.loads(base_path.read_text(encoding="utf-8"))["cases"]
    cases = json.loads(
        (ROOT / "bench/data/midgame_cases.json").read_text(encoding="utf-8"))["cases"]
    print("baseline cases:", len(base), " current cases:", len(cases))
    assert len(base) == len(cases)

    diffs = []
    for i, (b, c) in enumerate(zip(base, cases)):
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        a = c["a"]
        cur_auto = snapshot(native.decide(cv, a, 30, 16, is_play=True))
        cur_help = snapshot(native.decide(cv, a, 30, 16, is_play=False))
        if cur_auto != b["auto"]:
            diffs.append(("auto", i, b["auto"], cur_auto))
        if cur_help != b["help"]:
            diffs.append(("help", i, b["help"], cur_help))

    print("MISMATCHES:", len(diffs))
    for mode, idx, old, new in diffs[:5]:
        print("\n--- %s case %d ---" % (mode, idx))
        for k in set(old) | set(new):
            if old.get(k) != new.get(k):
                o, n = old.get(k), new.get(k)
                print("  %-14s %s != %s" % (k, str(o)[:100], str(n)[:100]))
    verdict = "PASS" if not diffs else "FAIL"
    print("\nREFACTOR_EQUIVALENCE:", verdict)
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
