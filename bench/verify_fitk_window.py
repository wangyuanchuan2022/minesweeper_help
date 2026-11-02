# -*- coding: utf-8 -*-
"""量化 _ps_refit_k 的窗口回归：注释说只用最近 200 条，代码实际用全量。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from utils import probability as P


def main():
    rows = P._ps_read_samples()
    state = {"ps_native": True, "ps_base_ms": 3.0, "ps_k": 1.1, "wr": {"t": 10.5}}

    t0 = time.perf_counter()
    P._ps_refit_k(state, rows)
    dt_all = (time.perf_counter() - t0) * 1000
    k_all = state["ps_k"]

    t0 = time.perf_counter()
    P._ps_refit_k(state, rows[-200:])
    dt_200 = (time.perf_counter() - t0) * 1000
    k_200 = state["ps_k"]

    print("样本总数                 =", len(rows))
    print("data_time.json 大小      = %.1f KB" % (os.path.getsize(P._PS_SAMPLES_PATH) / 1024))
    print("_ps_refit_k(全量)        = %8.1f ms  -> ps_k=%.5f" % (dt_all, k_all))
    print("_ps_refit_k(最近200条)   = %8.1f ms  -> ps_k=%.5f" % (dt_200, k_200))
    print("按注释意图截窗可省       = %8.1f ms/次调用" % (dt_all - dt_200))
    print()
    print("调用频率：_ps_observe 每次 part_solve 后调用一次（每局数十次）")
    print("         _number5_1_core 首次进入时调用一次")
    print("每局额外开销估算         = %.1f ms x 调用次数" % dt_all)


if __name__ == "__main__":
    main()
