"""Same-board paired comparison after the weighted tie-break fix.

Reads the shared layouts (generated once from Python Boards) and re-runs the
C++ driver on them, then computes the paired statistics vs the stored Python
results.  Usage: python bench/paired_weighted_stats.py
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils" / "trial_wholegame"))
import mscore as t  # noqa: E402

d = json.load(open(ROOT / "bench/data/_paired_layouts.json", encoding="utf-8"))
t0 = time.perf_counter()
out = t.run_ab_mines(d["layouts"], 30, 16, 99, "production", 200000, 3)
el = time.perf_counter() - t0

cppw = sum(1 for won, st, dead in out if won)
b = sum(1 for (won, _, _), r in zip(out, d["py"]) if r["won"] and not won)
c = sum(1 for (won, _, _), r in zip(out, d["py"]) if won and not r["won"])
n = b + c
p_mcn = min(1.0, (2 * math.comb(n, min(b, c)) / 2 ** n)) if n else 1.0
k, N = cppw, 50
pk = k / N
z = 1.959963984540054
dd = 1 + z * z / N
cc = pk + z * z / (2 * N)
hh = z * math.sqrt(pk * (1 - pk) / N + z * z / (4 * N * N))
lo, hi = 100 * (cc - hh) / dd, 100 * (cc + hh) / dd
print("WEIGHTED 50 same-board: wins %d rate %.1f%% CI [%.1f, %.1f]"
      % (k, 100 * pk, lo, hi))
print("PY 14/50=28.0%% | paired only_PY %d only_CPP %d McNemar p=%.4f"
      % (b, c, p_mcn))
print("deaths %d per_game_s %.3f"
      % (sum(1 for w, s, dd2 in out if dd2), el / 50))
io.open(ROOT / "bench/data/paired_weighted_50.json", "w", encoding="utf-8").write(
    json.dumps({
        "scope": "SAME-BOARD paired after open_num5x5 weighted tie-break",
        "py_wins": 14, "cpp_wins": cppw, "cpp_rate": pk,
        "cpp_wilson95": [(cc - hh) / dd, (cc + hh) / dd],
        "paired": {"only_py": b, "only_cpp": c, "mcnemar_p": round(p_mcn, 4)},
        "per_game_s": round(el / 50, 3),
    }, ensure_ascii=False, indent=1))
print("saved")
