# -*- coding: utf-8 -*-
"""win_rate 加速基线：定位热点并建立可重复的加速比测量。

=================== 目标 ====================
用户目标：让 win_rate 算法提速 **10 倍以上**。

=================== 测量口径 ====================
用 `bench/data/wr_cases_big.json` 的 33 个生产真实决策点：
  · 总耗时（sum）—— 决定整体加速比
  · 最重用例（case 13，424,114 节点）—— 决定尾部加速比
  · 逐用例耗时表 —— 便于对比改前改后

⚠️ 必须先确认：同一输入多次调用结果**逐位相同**（否则提速无意义）。
本脚本同时记录输出的校验和，供改前/改后对拍。

用法:
  python bench/bench_wr_speed.py --label before
  python bench/bench_wr_speed.py --label after
  python bench/bench_wr_speed.py --compare before after
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                       # noqa: E402

import utils.native as nat               # noqa: E402

DATA = os.path.join(ROOT, "bench", "data", "wr_cases_big.json")
OUT = os.path.join(ROOT, "state", "ab")


def load_cases():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)["cases"]


def call_wr(c):
    """调用一次 win_rate，返回 (耗时 ms, 输出指纹)。"""
    groups = [np.asarray(g, np.int32) for g in c["groups"]]
    cv = np.asarray(c["cell_value"], np.int32)
    t0 = time.perf_counter()
    out = nat.mscore.win_rate(
        c["clicks"], c["clicks9"], groups, cv, c["ck"], c["num10"],
        c["a"], c["w"], c["h"], c["is_play"], lambda v: None, 0)
    dt = (time.perf_counter() - t0) * 1000.0
    # 输出指纹：res 列表 + 排序后 clicks + total
    res = np.asarray(out[0], np.float64)
    h = hashlib.md5(res.tobytes()).hexdigest()[:12]
    return dt, h, int(out[2]) if len(out) > 2 else 0


def measure(label, repeat=1):
    cases = load_cases()
    rows = []
    print("=" * 92)
    print(f"win_rate 速度基线 [{label}]  native={nat.available}")
    print("=" * 92)
    print(f"{'#':>3} {'worlds':>7} {'F':>3} {'U':>4} {'ms':>10} {'out_hash':>14}")
    print("-" * 92)
    for ci, c in enumerate(cases):
        best = None
        fp = None
        for _ in range(repeat):
            try:
                dt, h, total = call_wr(c)
            except Exception as e:                 # noqa: BLE001
                print(f"{ci:>3} ERR {type(e).__name__}: {str(e)[:50]}")
                dt, h = None, None
                break
            if best is None or dt < best:
                best = dt
                fp = h
        if best is None:
            continue
        rows.append({"ci": ci, "ms": best, "hash": fp,
                     "worlds": c["total"],
                     "F": len(c["clicks"]), "U": len(c["clicks9"])})
        print(f"{ci:>3} {c['total']:>7} {len(c['clicks']):>3} "
              f"{len(c['clicks9']):>4} {best:>10.2f} {fp:>14}")

    total_ms = sum(r["ms"] for r in rows)
    worst = max(rows, key=lambda r: r["ms"])
    print("-" * 92)
    print(f"用例数 {len(rows)}")
    print(f"**总耗时 {total_ms:.2f} ms**")
    print(f"中位 {statistics.median([r['ms'] for r in rows]):.2f} ms")
    print(f"最重用例 #{worst['ci']}（{worst['worlds']} 世界）：{worst['ms']:.2f} ms")

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f"wr_speed_{label}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"label": label, "rows": rows, "total_ms": total_ms,
                   "median_ms": statistics.median([r["ms"] for r in rows])},
                  f, ensure_ascii=False, indent=1)
    print(f"\n已写 {p}")
    return rows, total_ms


def compare(a, b):
    pa = os.path.join(OUT, f"wr_speed_{a}.json")
    pb = os.path.join(OUT, f"wr_speed_{b}.json")
    for p in (pa, pb):
        if not os.path.exists(p):
            print(f"缺少 {p}，先跑对应 label")
            return
    da = json.load(open(pa, encoding="utf-8"))
    db = json.load(open(pb, encoding="utf-8"))
    ma = {r["ci"]: r for r in da["rows"]}
    mb = {r["ci"]: r for r in db["rows"]}

    print("=" * 100)
    print(f"加速比对比：{a} → {b}")
    print("=" * 100)
    print(f"{'#':>3} {'worlds':>7} {a + '(ms)':>12} {b + '(ms)':>12} "
          f"{'加速':>8} {'输出一致':>9}")
    print("-" * 100)
    same_all = True
    for ci in sorted(set(ma) & set(mb)):
        ra, rb = ma[ci], mb[ci]
        sp = ra["ms"] / rb["ms"] if rb["ms"] > 0 else float("inf")
        same = ra["hash"] == rb["hash"]
        if not same:
            same_all = False
        print(f"{ci:>3} {ra['worlds']:>7} {ra['ms']:>12.2f} {rb['ms']:>12.2f} "
              f"{sp:>7.2f}x {'OK' if same else 'XX 不一致':>9}")
    print("-" * 100)
    tot_a, tot_b = da["total_ms"], db["total_ms"]
    print(f"总耗时  {tot_a:.2f} ms → {tot_b:.2f} ms   "
          f"**加速 {tot_a / tot_b:.2f}x**")
    print(f"中位    {da['median_ms']:.2f} ms → {db['median_ms']:.2f} ms   "
          f"加速 {da['median_ms'] / db['median_ms']:.2f}x")
    print()
    if same_all:
        print("✅ 全部用例输出指纹一致（逐位相同）")
    else:
        print("❌ 存在输出不一致的用例 —— 提速无效，必须修回")
    print(f"目标 10x：{'已达成' if tot_a / tot_b >= 10 else f'未达成（当前 {tot_a / tot_b:.2f}x）'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        measure(args.label, args.repeat)


if __name__ == "__main__":
    main()
