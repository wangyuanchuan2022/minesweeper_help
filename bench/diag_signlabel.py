# -*- coding: utf-8 -*-
"""诊断：sign_test 的 pos/neg 标签是否与"更好/更差"语义一致。

背景：offline_multistep.py 输出「优 11 / 劣 51」但 Δ=-0.024032（准则更好），
      两者矛盾。本脚本确认这是标签语义问题还是数值问题。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
sys.path.insert(0, "bench")

import numpy as np  # noqa: E402
from offline_multistep import (  # noqa: E402
    load_v3_with_cases, build_multistep, s0_pick, score_minmax, sign_test)

rows, cases = load_v3_with_cases(150)
rows, feats = build_multistep(rows, cases, 50000000)

d = []
for r, f in zip(rows, feats):
    q = np.asarray(r["q"], dtype=np.float64)
    if len(q) < 3:
        continue
    b = float(q.max() - q[s0_pick(f)])
    c = float(q.max() - q[int(np.argmax(score_minmax(f, "PLY3")))])
    d.append(c - b)
d = np.asarray(d)

print("diffs (criterion - S0):")
print("  mean = %+.6f" % d.mean())
print("  d > 0  (criterion WORSE):  %d" % int((d > 1e-12).sum()))
print("  d < 0  (criterion BETTER): %d" % int((d < -1e-12).sum()))
print("  d == 0 (same):             %d" % int((np.abs(d) <= 1e-12).sum()))

p, pos, neg = sign_test(d)
print()
print("sign_test -> p=%.4f  pos=%d  neg=%d" % (p, pos, neg))
print("  pos is defined as count(d > 1e-12) => that is count of WORSE points")
print("  neg is defined as count(d < -1e-12) => that is count of BETTER points")
print()
print("=> the print statement says 'better pos / worse neg', but:")
print("   pos=%d actually means WORSE, neg=%d actually means BETTER" % (pos, neg))
print("   So the correct report is: better %d / worse %d" % (neg, pos))
print()
consistent = (neg > pos) and (d.mean() < 0)
print("=> numeric consistency check (more better points AND negative mean): %s"
      % ("CONSISTENT" if consistent else "INCONSISTENT"))
