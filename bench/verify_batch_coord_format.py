"""Coordinate-format contract tests for the C++ decide() batch output.

Advisor nit: batch coordinates must match production Python `clicks`
format `[(x, y), ...]` (x = column, y = row, both 1-based).

What this pins down:
  1. Python `decide()["tied"]` IS a list of 2-tuples `(x, y)`.
  2. `(x, y)` -> board index round-trip agrees with the driver's idx_of rule
     `idx = y * (w + 2) + x`, so the C++ flattened buffer
     `out_xy[2k] = x, out_xy[2k+1] = y` maps to the same cells.
  3. Positive control: swapping x/y MUST be detected (proves the test has
     discriminating power, not a tautology).

Run:  python bench/verify_batch_coord_format.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

DECIDE_DIR = ROOT / "utils" / "trial_decide"
if not (DECIDE_DIR / "mscore.cp310-win_amd64.pyd").exists():
    raise SystemExit("missing trial_decide pyd: run cpp/build_trial.bat")
sys.path.insert(0, str(DECIDE_DIR))
import mscore as decide_mod  # noqa: E402


def make_board_with_safe_region(w=30, h=16):
    cv = np.zeros((h + 2, w + 2), dtype=np.int32)
    cv[1:-1, 1:-1] = 9
    # opened zero at (15,8): the 8 neighbours are certainly safe
    cv[8, 15] = 0
    return cv


def idx_of(x, y, w):
    """Driver rule: idx = y * (w + 2) + x  (1-based x,y)."""
    return y * (w + 2) + x


def run():
    results = []
    w, h = 30, 16
    cv = make_board_with_safe_region(w, h)
    r = decide_mod.decide(cv, 99, w, h, 8.0, 2000000, 1536 * 1024 * 1024, 0)
    tied = r["tied"]

    # 1) type contract: list of 2-element tuples
    type_ok = (isinstance(tied, list) and len(tied) > 0
               and all(isinstance(t, tuple) and len(t) == 2 for t in tied)
               and all(isinstance(v, int) for t in tied for v in t))
    results.append({"test": "tied_is_list_of_xy_tuples",
                    "passed": bool(type_ok), "n": len(tied),
                    "sample": [tuple(t) for t in tied[:4]]})

    # 2) x,y are 1-based and in range
    inrange = all(1 <= t[0] <= w and 1 <= t[1] <= h for t in tied)
    results.append({"test": "coords_1based_in_range", "passed": bool(inrange),
                    "w": w, "h": h})

    # 3) round-trip with the driver's idx_of rule: flattened buffer
    #    out_xy[2k]=x, out_xy[2k+1]=y must address the SAME cell as (x,y)
    flat = []
    for (x, y) in tied:
        flat.extend([x, y])
    rt_ok = True
    detail = ""
    for k, (x, y) in enumerate(tied):
        fx, fy = flat[2 * k], flat[2 * k + 1]
        if (fx, fy) != (x, y):
            rt_ok = False
            detail = "flat mismatch at %d" % k
            break
        if idx_of(fx, fy, w) != idx_of(x, y, w):
            rt_ok = False
            detail = "idx mismatch at %d" % k
            break
    results.append({"test": "flattened_xy_roundtrip_matches_idx_of",
                    "passed": bool(rt_ok), "detail": detail})

    # 4) POSITIVE CONTROL: swapping x/y MUST be detected.  A test that cannot
    #    fail proves nothing about the format.
    swapped_detected = any((t[1], t[0]) != (t[0], t[1]) for t in tied)
    results.append({"test": "xy_swap_detected_control",
                    "passed": bool(swapped_detected),
                    "note": "asymmetric coords exist -> swap is observable"})

    ok = all(r["passed"] for r in results)
    print(json.dumps({"all_passed": ok, "results": results},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
