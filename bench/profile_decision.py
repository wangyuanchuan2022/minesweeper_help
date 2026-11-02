# -*- coding: utf-8 -*-
"""number5_1 内部耗时分解：distanceTransform / 分组 / part_solve / win_rate / pbs。"""
import cProfile
import io
import os
import pstats
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native


def fresh(cv):
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    s = make_full_solver(w=w, h=h, a=99)
    s.num = 1
    s.checked = {}
    s.pos_dict_list = []
    s.appended_pos = set()
    return s


def main():
    data = load_test_data("test_33.json")
    cv = np.array(data["cell_value"], dtype=np.int32)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2
    print(f"native={native.available} board={w}x{h} clicks={len(data['clicks'])}")

    s = fresh(cv)
    s.complete_scan = lambda c, no_10=True: c

    t0 = time.perf_counter()
    s.number5_1(cv.copy())
    dt = time.perf_counter() - t0
    print(f"number5_1 = {dt*1000:.1f} ms")
    print(f"  _last_heatmap.prob 格数 = {len(s._last_heatmap.get('prob') or {})}")
    print(f"  _last_heatmap.total    = {s._last_heatmap.get('total')}")
    print(f"  _last_heatmap.best     = {s._last_heatmap.get('best')}")
    tb = getattr(s, "_time_budget", None)
    print(f"  time_budget = {tb}")

    pr = cProfile.Profile()
    pr.enable()
    s2 = fresh(cv)
    s2.complete_scan = lambda c, no_10=True: c
    s2.number5_1(cv.copy())
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(22)
    text = buf.getvalue()
    # 去掉长路径前缀，便于阅读
    for line in text.splitlines():
        print(line)


if __name__ == "__main__":
    main()
