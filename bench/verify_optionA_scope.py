"""Whitebox unit tests for OptionAScope._choice interception gate.

WHY (advisor): a p's mathematical shape CANNOT identify a tie-break.
Real production has THREE different np.random.choice calls:

  1. win_rate tie-break  (probability.py:833-837)
       win_rate = np.around(...,5); where_max = np.where(==max,1,0)
       p = where_max / where_max.sum()          -> uniform over positives
  2. first-click / interior random (probability.py:553-555, :1214-1217)
       np_op_num = np.where(op_num == max(op_num), 1, 0)
       p = np_op_num / np_op_num.sum()          -> SAME shape as (1)!
  3. pbs weighted (probability.py:1215-1217)
       p = op_num_max_mask / mask.sum()          -> also uniform over positives

Therefore the gate MUST be the explicit branch marker set by win_rate /
process_bigger_situation hooks, not p's math.

These tests use the REAL shapes from those three code paths.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import bench.ab_optionA_10games as m


def _real_p(kind, n=8):
    """Reproduce the exact p construction from the cited production lines."""
    if kind == "win_rate":
        # :833-837  where_max from np.around(win_rate,5)==max
        where_max = np.zeros(n, dtype=int)
        where_max[[0, 3]] = 1          # 2 tied maxima
        return where_max / where_max.sum()
    if kind == "first_click":
        # :553-555 / :1214-1217  np.where(op_num == max(op_num),1,0)
        op5 = np.array([5, 5, 3, 5, 2, 5, 1, 5][:n], dtype=float)
        mask = np.where(op5 == max(op5), 1, 0)
        return mask / mask.sum()
    if kind == "pbs_weighted":
        # :1215-1217  same np.where(==max)/sum shape
        op5 = np.array([2, 9, 9, 1, 9, 3, 9, 4][:n], dtype=float)
        mask = np.where(op5 == max(op5), 1, 0)
        return mask / mask.sum()
    raise ValueError(kind)


def run():
    results = []

    # ---- Test A: win_rate tie-break MUST be intercepted ----
    s = m.OptionAScope()
    s.active = True
    s.state["branch"] = "win_rate"
    s.state["sorted_clicks"] = [(1, 1), (2, 2), (3, 3), (4, 4),
                                (5, 5), (6, 6), (7, 7), (8, 8)]
    s.state["pbs_pool"] = []
    p = _real_p("win_rate", 8)
    idx = s._choice(np.arange(8), p=p)
    intercepted = (s.choice_calls == 1 and len(s.decisions) == 1 and
                   s.decisions[0]["branch"] == "win_rate")
    results.append({"test": "win_rate_tiebreak_intercepted",
                    "passed": intercepted, "idx": int(idx),
                    "chosen_xy": s.decisions[0]["chosen_xy"]})

    # ---- Test B: first-click uniform (SAME p shape) MUST pass through ----
    s2 = m.OptionAScope()
    s2.active = True
    s2.state["branch"] = "none"          # no win_rate/pbs hook fired
    s2.state["sorted_clicks"] = []
    s2.state["pbs_pool"] = []
    p2 = _real_p("first_click", 8)
    np.random.seed(42)
    r2 = s2._choice(np.arange(8), p=p2)
    passed_through = (s2.choice_calls == 0 and len(s2.decisions) == 0)
    # compare with pristine numpy choice under same seed
    np.random.seed(42)
    ref2 = np.random.choice(np.arange(8), p=p2)
    # STRICT same-shape control (advisor): feed the EXACT SAME p as win_rate
    # but with branch='none'.  Must still pass through untouched.  This is
    # the decisive test that a p's math alone cannot identify a tie-break.
    s2b = m.OptionAScope()
    s2b.active = True
    s2b.state["branch"] = "none"
    s2b.state["sorted_clicks"] = []
    s2b.state["pbs_pool"] = []
    p_same_as_wr = np.array(p, dtype=float)   # identical values to win_rate p
    np.random.seed(7)
    r_same = s2b._choice(np.arange(8), p=p_same_as_wr)
    np.random.seed(7)
    ref_same = np.random.choice(np.arange(8), p=p_same_as_wr)
    same_shape_passthrough = (s2b.choice_calls == 0 and
                              len(s2b.decisions) == 0 and
                              int(r_same) == int(ref_same))
    results.append({"test": "first_click_uniform_NOT_intercepted",
                    "passed": passed_through and int(r2) == int(ref2),
                    "got": int(r2), "orig": int(ref2)})
    results.append({"test": "SAME_p_as_win_rate_but_branch_none_NOT_intercepted",
                    "passed": same_shape_passthrough,
                    "p_identical_to_win_rate": True,
                    "got": int(r_same), "orig": int(ref_same)})

    # ---- Test C: PBS is EXPLICITLY OUT OF OPTION A scope (negative control) ----
    # Production pbs poses are a dynamic res-threshold subset of clicks;
    # capturing/reconstructing them would rewrite pbs selection logic.  User
    # approved Option A narrowing: only win_rate deterministic; PBS remains
    # original production random.  Even with the real p shape, branch='pbs'
    # MUST pass through unchanged and record no deterministic decision.
    s3 = m.OptionAScope()
    s3.active = True
    s3.state["branch"] = "pbs"
    s3.state["sorted_clicks"] = []
    s3.state["pbs_pool"] = []       # intentionally not captured
    p3 = _real_p("pbs_weighted", 8)
    np.random.seed(42)
    got3 = s3._choice(np.arange(8), p=p3)
    np.random.seed(42)
    ref3 = np.random.choice(np.arange(8), p=p3)
    passthrough3 = (s3.choice_calls == 0 and len(s3.decisions) == 0 and
                    int(got3) == int(ref3))
    results.append({"test": "pbs_branch_NOT_in_optionA_determinization",
                    "passed": passthrough3,
                    "chosen_same_as_original": int(got3) == int(ref3),
                    "choice_calls": s3.choice_calls,
                    "decisions": len(s3.decisions),
                    "scope_note": "PBS poses capture/rewrite intentionally out of scope"})

    # ---- Test D: outside number5_1 scope, everything passes through ----
    s4 = m.OptionAScope()
    s4.active = False
    s4.state["branch"] = "win_rate"
    p4 = _real_p("win_rate", 8)
    s4._choice(np.arange(8), p=p4)
    results.append({"test": "outside_scope_NOT_intercepted",
                    "passed": s4.choice_calls == 0 and len(s4.decisions) == 0})

    # ---- Test E: restore identity by id (advisor nit) ----
    import sys as _sys
    id_before = id(_sys.modules["numpy.random"].choice)
    s5 = m.OptionAScope()
    s5.install()
    id_patched = id(_sys.modules["numpy.random"].choice)
    s5.restore()
    id_after = id(_sys.modules["numpy.random"].choice)
    results.append({"test": "restore_restores_choice_id",
                    "passed": id_before == id_after and id_patched != id_before,
                    "id_before": id_before, "id_patched": id_patched,
                    "id_after": id_after})

    all_ok = all(r["passed"] for r in results)
    print(json.dumps({"all_passed": all_ok, "results": results},
                     ensure_ascii=False, indent=1))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
