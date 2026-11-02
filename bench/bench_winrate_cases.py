# -*- coding: utf-8 -*-
"""win_rate 基准与对拍（同输入 → 同输出）。

用途：
  ① 记录**改前**金标准（每个用例的输出 + 耗时）
  ② 改后逐位对拍（要求 |Δ| < 1e-12，即 IEEE754 双精度逐位相同）
  ③ 报耗时对比

⚠️ 安全：只在**已采集的固定用例**上跑，规模由采集时的生产阈值决定
（n_cand ≤ 200、total ≤ 32）。不会像之前那样在活棋局里强制展开。

用法:
  python bench/bench_winrate_cases.py --save bench/data/wr_before.json
  python bench/bench_winrate_cases.py --compare bench/data/wr_before.json
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np

import utils.native as nat


def run_case(case):
    """跑一个用例，返回 (res, clicks, total, clicks2p, ms)。"""
    t0 = time.perf_counter()
    out = nat.mscore.win_rate(
        case["clicks"], case["clicks9"], [np.asarray(g, np.int32) for g in case["groups"]],
        np.asarray(case["cell_value"], np.int32), case["ck"], case["num10"],
        case["a"], case["w"], case["h"], case["is_play"], lambda v: None)
    ms = (time.perf_counter() - t0) * 1000.0
    res, clicks, total, c2p = out
    return ([float(x) for x in res],
            [list(map(int, c)) for c in clicks], int(total),
            {f"{k[0]},{k[1]}": float(v) for k, v in c2p.items()}, ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(ROOT, "bench", "data", "wr_cases.json"))
    ap.add_argument("--save", default=None, help="把本次结果存为金标准")
    ap.add_argument("--compare", default=None, help="与金标准文件对拍")
    ap.add_argument("--repeat", type=int, default=3, help="每用例重复次数（取中位数耗时）")
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]
    print("=" * 78)
    print(f"win_rate 基准：{len(cases)} 个用例（native={nat.available}）")
    print("=" * 78)

    gold = None
    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            gold = json.load(f)["results"]

    results = []
    t_all = []
    for i, c in enumerate(cases):
        times = []
        out = None
        for _ in range(args.repeat):
            out = run_case(c)
            times.append(out[4])
        res, clicks, total, c2p, _ = out
        med = statistics.median(times)
        t_all.append(med)
        results.append({"idx": i, "res": res, "clicks": clicks,
                        "total": total, "clicks2p": c2p, "ms_median": med,
                        "case": {k: c[k] for k in ("F", "U", "total", "n_cand",
                                                   "limitation")}})
        print(f"  [{i:2d}] F={c['F']:2d} U={c['U']:2d} total={c['total']:4d} "
              f"n_cand={c['n_cand']:3d}  {med:8.2f} ms  total_w={total}")

    print("-" * 78)
    ms_all = sorted(t_all)
    if ms_all:
        print(f"耗时：中位数 {statistics.median(ms_all):.2f} ms  "
              f"最大 {ms_all[-1]:.2f} ms  合计 {sum(ms_all):.2f} ms")
    print(f"合计 {len(t_all)} 用例")

    if gold is not None:
        print()
        print("=" * 78)
        print("对拍（要求逐位相同，|Δ| < 1e-12）")
        print("=" * 78)
        bad = 0
        max_d = 0.0
        gms = []
        for r in results:
            g = gold[r["idx"]]
            gms.append(g["ms_median"])
            # 1) res 数组
            if len(g["res"]) != len(r["res"]):
                print(f"  [{r['idx']:2d}] ❌ res 长度不同 {len(g['res'])} vs {len(r['res'])}")
                bad += 1
                continue
            d_res = max((abs(a - b) for a, b in zip(g["res"], r["res"])), default=0.0)
            # 2) total
            d_tot = abs(g["total"] - r["total"])
            # 3) clicks 序列
            same_clicks = g["clicks"] == r["clicks"]
            # 4) clicks2p 字典
            keys_same = set(g["clicks2p"]) == set(r["clicks2p"])
            d_p = (max((abs(g["clicks2p"][k] - r["clicks2p"][k])
                        for k in g["clicks2p"]), default=0.0) if keys_same else float("inf"))
            dt = d_res + d_tot + d_p
            max_d = max(max_d, d_res if d_res == d_res else 0.0)
            ok = (d_res < 1e-12 and d_tot == 0 and same_clicks and keys_same and d_p < 1e-12)
            if not ok:
                bad += 1
            print(f"  [{r['idx']:2d}] {'✅' if ok else '❌'} Δres={d_res:.3e} "
                  f"Δtotal={d_tot} clicks同={same_clicks} "
                  f"Δclicks2p={d_p:.3e}   "
                  f"耗时 {g['ms_median']:.2f} → {r['ms_median']:.2f} ms")
        print("-" * 78)
        print(f"对拍结果：{len(results) - bad}/{len(results)} 一致"
              f"{'  ✅ 全部逐位相同' if bad == 0 else f'  ⚠️ {bad} 个不一致'}")
        if gms and ms_all:
            bg, af = statistics.median(gms), statistics.median(ms_all)
            print(f"耗时中位数 {bg:.2f} → {af:.2f} ms  "
                  f"（{'提速' if af < bg else '变慢'} {bg/af if af else 0:.2f}x）")
        if bad:
            print()
            print("⚠️ 存在不一致 —— 优化改变了数值语义，必须修到逐位相同后再谈提阈值")

    if args.save:
        os.makedirs(os.path.dirname(args.save), exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({"results": results}, f, ensure_ascii=False)
        print(f"已保存金标准 → {args.save}")


if __name__ == "__main__":
    main()
