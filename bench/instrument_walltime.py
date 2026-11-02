# -*- coding: utf-8 -*-
"""墙钟插桩 v2：number5_1 各段真实占比（修正版：acc 初始化含全部 key）。"""
import os
import statistics
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data
from utils import native, probability as P

KEYS = ("total", "vision", "observe", "record", "refit", "tbsave", "pbs", "wr", "ps")


def zero():
    return {k: 0.0 for k in KEYS}


def run_once(cv, w, h, acc):
    s = make_full_solver(w=w, h=h, a=99)
    s.num = 1
    s.checked = {}
    s.pos_dict_list = []
    s.appended_pos = set()

    orig_scan = s.complete_scan

    def scan(c, no_10=True):
        t0 = time.perf_counter()
        try:
            return orig_scan(c)
        finally:
            acc["vision"] += time.perf_counter() - t0

    s.complete_scan = scan

    orig_ps = s.part_solve

    def ps(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_ps(*a, **kw)
        finally:
            acc["ps"] += time.perf_counter() - t0

    s.part_solve = ps

    orig_wr = s.win_rate

    def wr(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_wr(*a, **kw)
        finally:
            acc["wr"] += time.perf_counter() - t0

    s.win_rate = wr

    orig_pbs = s.process_bigger_situation

    def pbs(*a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_pbs(*a, **kw)
        finally:
            acc["pbs"] += time.perf_counter() - t0

    s.process_bigger_situation = pbs

    t0 = time.perf_counter()
    s.number5_1(cv.copy())
    acc["total"] += time.perf_counter() - t0


def main():
    data = load_test_data("test_33.json")
    cv = np.array(data["cell_value"], dtype=np.int32)
    h, w = cv.shape[0] - 2, cv.shape[1] - 2

    orig = {k: getattr(P, k) for k in ("_ps_observe", "_ps_record_ms", "_ps_refit_k", "_tb_save")}

    def wrap(name, key):
        real = orig[name]

        def inner(*a, **kw):
            t0 = time.perf_counter()
            try:
                return real(*a, **kw)
            finally:
                acc_ref[0][key] += time.perf_counter() - t0
        setattr(P, name, inner)

    acc_ref = [zero()]
    for n, k in (("_ps_observe", "observe"), ("_ps_record_ms", "record"),
                 ("_ps_refit_k", "refit"), ("_tb_save", "tbsave")):
        wrap(n, k)

    runs = []
    N = 7
    try:
        for _ in range(N):
            a = zero()
            acc_ref[0] = a
            run_once(cv, w, h, a)
            runs.append(dict(a))
    finally:
        for k, v in orig.items():
            setattr(P, k, v)

    def med(key):
        return statistics.median(r[key] for r in runs) * 1000

    tot = med("total")
    print(f"native={native.available}  棋盘 {w}x{h}  重复 {N} 次取中位数")
    print("=" * 62)
    print(f"number5_1 总墙钟              {tot:9.1f} ms  100.0%")
    # ⚠️ _ps_observe 内部会调用 _ps_record_ms / _ps_refit_k / _tb_save，
    # 四者相加会重复计时——只有 _ps_observe 是顶层，其余是其子项，仅作分解参考。
    rows = [
        ("视觉 complete_scan", med("vision")),
        ("耗时模型 _ps_observe(顶层)", med("observe")),
        ("part_solve", med("ps")),
        ("win_rate", med("wr")),
        ("process_bigger_situation", med("pbs")),
    ]
    print("  ── _ps_observe 内部构成（含在上面里面，不重复计入）──")
    for nm, key in (("_ps_record_ms", "record"), ("_ps_refit_k", "refit"), ("_tb_save", "tbsave")):
        print(f"     {nm:<31}{med(key):9.1f} ms")
    for name, v in rows:
        print(f"  {name:<34}{v:9.1f} ms  {v/tot*100:5.1f}%")
    accounted = sum(v for _, v in rows)
    print(f"  {'其余(分组/概率组装/决策)':<34}{tot-accounted:9.1f} ms  "
          f"{(tot-accounted)/tot*100:5.1f}%")


if __name__ == "__main__":
    main()
