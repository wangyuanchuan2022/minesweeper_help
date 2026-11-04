# -*- coding: utf-8 -*-
"""打印 state/ab/midgame_lookahead.json 的表格（只读）。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "state", "ab", "midgame_lookahead.json"),
          encoding="utf-8") as f:
    d = json.load(f)
rows = d["rows"]
ARMS = ("prod", "safety", "open5x5", "ply2", "ply3")
print("covered %d  max_worlds=%s" % (len(rows), d.get("max_worlds")))
head = "%-9s %3s %3s %9s %5s %7s | " % ("lim", "F", "U", "worlds", "tie", "V*")
head += " ".join("%8s" % a for a in ARMS)
print(head)
for r in rows:
    cells = []
    for a in ARMS:
        e = r.get(a) or {}
        v = e.get("regret") if e.get("present") else "N/A"
        cells.append("%8s" % v)
    print("%-9s %3d %3d %9d %5d %7.4f | %s"
          % (r["limitation"], r["F"], r["U"], r["worlds"], r["n_tie"],
             r["v_opt"], " ".join(cells)))
