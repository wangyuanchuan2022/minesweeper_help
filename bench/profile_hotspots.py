# -*- coding: utf-8 -*-
"""性能剖析：基于真实对局数据（tests/data/*.json）测量核心算法耗时分布。

用法: python bench/profile_hotspots.py [scenario]
scenario: all | part_solve | part_solve_single | number5_1
"""
import cProfile
import io
import json
import os
import pstats
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # cfg.json / image/ 依赖工作目录

from tests.helpers import make_full_solver, load_test_data


def bench_part_solve(solver, data, cell_value, repeats=1):
    t0 = time.perf_counter()
    for _ in range(repeats):
        res_l, num_solve, canopen = solver.part_solve(
            data["clicks"], cell_value.copy(), data["num10"],
            data["num9"], data["cs"], _try=False)
    t1 = time.perf_counter()
    return t1 - t0, len(res_l)


def bench_part_solve_single(solver, data, cell_value, repeats=1):
    t0 = time.perf_counter()
    for _ in range(repeats):
        res_list, total, canopen = solver.part_solve_single(
            data["clicks"], cell_value.copy(), data["num10"],
            data["num9"], data["cs"], _try=False)
    t1 = time.perf_counter()
    return t1 - t0, len(res_list)


def bench_number5_1(solver, data, repeats=1):
    """number5_1 端到端（真实主决策入口，含分组/part_solve/胜率或大局面处理）。"""
    t0 = time.perf_counter()
    for _ in range(repeats):
        cv = np.array(data["cell_value"], dtype=np.int32)
        solver.num = 1
        solver.checked = {}
        solver.pos_dict_list = []
        solver.appended_pos = set()
        out = solver.number5_1(cv)
    t1 = time.perf_counter()
    return t1 - t0, None


def profile_call(fn, top=25):
    pr = cProfile.Profile()
    pr.enable()
    fn()
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(top)
    return s.getvalue()


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "all"
    name = sys.argv[2] if len(sys.argv) > 2 else "test_33.json"

    data = load_test_data(name)
    cell_value = np.array(data["cell_value"], dtype=np.int32)
    print(f"数据: {name}  clicks={len(data['clicks'])} cs={len(data['cs'])} "
          f"num9={data['num9']} num10={data['num10']}")

    solver = make_full_solver(w=30, h=16, a=99)
    solver.is_play = False

    if scenario in ("all", "part_solve"):
        dt, n = bench_part_solve(solver, data, cell_value)
        print(f"[part_solve]        {dt*1000:10.1f} ms  解数={n}")
        if scenario == "all":
            print(profile_call(lambda: bench_part_solve(solver, data, cell_value)))

    if scenario in ("all", "part_solve_single"):
        dt, n = bench_part_solve_single(solver, data, cell_value)
        print(f"[part_solve_single] {dt*1000:10.1f} ms  解数={n}")
        if scenario == "all":
            print(profile_call(lambda: bench_part_solve_single(solver, data, cell_value)))

    if scenario in ("all", "number5_1"):
        dt, _ = bench_number5_1(solver, data)
        print(f"[number5_1 端到端]  {dt*1000:10.1f} ms")
        print(profile_call(lambda: bench_number5_1(solver, data)))


if __name__ == "__main__":
    main()
