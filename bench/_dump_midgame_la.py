# -*- coding: utf-8 -*-
"""打印 state/ab/midgame_lookahead.json 的逐点明细（只读）。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "state", "ab", "midgame_lookahead.json"),
          encoding="utf-8") as f:
    d = json.load(f)
rows = d["rows"]
print(f"covered {len(rows)} (max_worlds={d.get('max_worlds')})")
for r in rows:
    cells = []
    for a in ("prod", "safety", "open5x5", "ply2", "ply3"):
        e = r.get(a) or {}
        cells.append(f"{a}={e.get('regret') if e.get('present') else 'N/A'}")
    print("lim=%7s F=%3d U=%3d w=%8d ncand=%3d tie=%3d V*=%.4f | %s"
          % (r["limitation"], r["F"], r["U"], r["worlds"], r["n_cand"],
             r["n_tie"], r["v_opt"], " ".join(cells)))
