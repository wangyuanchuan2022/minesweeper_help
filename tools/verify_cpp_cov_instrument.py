# -*- coding: utf-8 -*-
"""仪器验证：MinGW -O0 --coverage 插桩 pyd 与 MSVC -O2 生产 pyd 的决策必须逐字段一致。

为什么必须做这一步：
  覆盖率画像只有在「插桩构建 ≡ 生产构建」时才代表生产路径。若两者决策不同，
  那么 gcov 统计的调用序列就不是生产真正走的那条路，任何「零调用」结论都失效。
  （测量纪律：先验证仪器，再测量。）

做法：同一批真实盘面，分别用两个 pyd 目录里的 mscore 跑 decide（auto/help），
      把返回 dict 归一化后逐字段比对。两个模块同名，必须分**两个子进程**跑。

用法：
  python tools/verify_cpp_cov_instrument.py
输出：
  state/audit/cpp_cov_instrument_equivalence.json
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COV_DIR = ROOT / "output" / "cpp_line_cov"
STATS = COV_DIR / "_workload_stats.json"
REPORT = ROOT / "state" / "audit" / "cpp_cov_instrument_equivalence.json"

NORM_KEYS = ("x", "y", "reason", "path", "world_upper", "estimated_bytes",
             "situations", "dstar")


def norm(r):
    if r is None:
        return {"none": True}
    out = {}
    for k in NORM_KEYS:
        v = r.get(k)
        out[k] = None if v is None else v
    out["confidence"] = round(float(r.get("confidence") or 0.0), 10)
    out["limitation"] = round(float(r.get("limitation") or 0.0), 8)
    prob = r.get("prob")
    if isinstance(prob, dict):
        out["prob"] = sorted([int(k[0]), int(k[1]), round(float(v), 10)]
                             for k, v in prob.items())
    else:
        out["prob"] = []
    out["tied"] = sorted([int(c[0]), int(c[1])] for c in (r.get("tied") or []))
    out["weighted"] = sorted([int(c[0]), int(c[1])] for c in (r.get("weighted") or []))
    tr = r.get("transitions")
    if isinstance(tr, dict):
        out["transitions"] = sorted([str(k), round(float((v or {}).get("p") or 0.0), 10)]
                                    for k, v in tr.items())
    else:
        out["transitions"] = None
    return out


RUNNER = r'''
import json, sys, os
mod_dir = sys.argv[1]
n = int(sys.argv[2])
sys.path.insert(0, mod_dir)
import numpy as np
import mscore
cases = json.load(open(sys.argv[3], encoding="utf-8"))["cases"][:n]
out = []
for c in cases:
    cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
    a = int(c["a"])
    out.append({"auto": mscore.decide(cv, a=a, w=30, h=16, is_play=True),
                "help": mscore.decide(cv, a=a, w=30, h=16, is_play=False)})
# 归一化（在子进程里做，避免 py::dict 跨进程不可序列化）
def norm(r):
    if r is None:
        return {"none": True}
    o = {}
    for k in ("x","y","reason","path","world_upper","estimated_bytes","situations","dstar"):
        o[k] = r.get(k)
    o["confidence"] = round(float(r.get("confidence") or 0.0), 10)
    o["limitation"] = round(float(r.get("limitation") or 0.0), 8)
    p = r.get("prob")
    o["prob"] = sorted([int(k[0]), int(k[1]), round(float(v),10)] for k,v in p.items()) if isinstance(p, dict) else []
    o["tied"] = sorted([int(c[0]),int(c[1])] for c in (r.get("tied") or []))
    o["weighted"] = sorted([int(c[0]),int(c[1])] for c in (r.get("weighted") or []))
    tr = r.get("transitions")
    o["transitions"] = sorted([str(k), round(float((v or {}).get("p") or 0.0),10)] for k,v in tr.items()) if isinstance(tr, dict) else None
    return o
print(json.dumps([{"auto": norm(x["auto"]), "help": norm(x["help"])} for x in out]))
'''


def run(mod_dir, n, cases_path):
    r = subprocess.run([sys.executable, "-c", RUNNER, str(mod_dir), str(n), str(cases_path)],
                       capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        print("RUN FAILED", mod_dir)
        print(r.stdout[-2000:])
        print(r.stderr[-4000:])
        sys.exit(1)
    return json.loads(r.stdout)


def main():
    cases_path = ROOT / "bench/data/midgame_cases.json"
    n = 400
    stats = {}
    if STATS.exists():
        stats = json.loads(STATS.read_text(encoding="utf-8"))

    print("== run A: 生产 MSVC pyd (utils/mscore.cp310-win_amd64.pyd) ==")
    prod = run(ROOT / "utils", n, cases_path)
    print("   cases:", len(prod))
    print("== run B: 插桩 MinGW --coverage pyd ==")
    cov = run(COV_DIR, n, cases_path)
    print("   cases:", len(cov))

    diffs = []
    for i, (a, b) in enumerate(zip(prod, cov)):
        for mode in ("auto", "help"):
            if a[mode] != b[mode]:
                keys = sorted(set(a[mode]) | set(b[mode]))
                bad = [k for k in keys if a[mode].get(k) != b[mode].get(k)]
                diffs.append({"case": i, "mode": mode, "fields": bad,
                              "prod": {k: str(a[mode].get(k))[:80] for k in bad},
                              "cov": {k: str(b[mode].get(k))[:80] for k in bad}})

    verdict = "PASS" if not diffs else "FAIL"
    report = {
        "purpose": "证明插桩 pyd（MinGW -O0 --coverage）与生产 pyd（MSVC -O2）决策逐字段一致",
        "cases": n,
        "pairs": n * 2,
        "fields_compared": list(NORM_KEYS) + ["confidence", "limitation", "prob", "tied",
                                              "weighted", "transitions"],
        "mismatch_count": len(diffs),
        "verdict": verdict,
        "first_diffs": diffs[:5],
        "workload_stats_used_for_coverage": stats,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nPAIRS:", n * 2, " MISMATCHES:", len(diffs), " VERDICT:", verdict)
    for d in diffs[:3]:
        print(" -", d["case"], d["mode"], d["fields"], d["prod"], d["cov"])
    print("report:", REPORT)
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
