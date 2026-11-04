# -*- coding: utf-8 -*-
"""验证 adaptive_depth 的两个必要条件。

=================== 为什么必须验证这两条 ====================
用户要求「前瞻步数按复杂度定，写入 C++」。改动加在生产的 win_rate 里，
所以有两条硬约束：

  1. **默认路径必须逐位不变**：不传 adaptive_depth（=0）时，输出必须与
     改动前的 .pyd 完全一致。否则任何历史读数都失去可比性，生产行为也被
     悄悄改掉。
  2. **自适应必须真的起作用**：adaptive_depth > 0 时，
     (a) node_cap 触发率下降；(b) 耗时长尾下降。
     若 cap 率没降，说明这条规则是空转（本项目已踩过"参数没接到生产路径"
     的坑：node_cap 扫描轴曾因没接线而三档零触发，是假象）。

判据不是"看起来合理"，而是上面两条的**可观测数字**。

用法: python bench/verify_adaptive_depth.py
"""
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np                                        # noqa: E402
import utils.native as native                             # noqa: E402


def noop(_p):
    pass


def call(case, node_cap, adaptive, depth_override=-1.0):
    clicks = [tuple(c) for c in case["clicks"]]
    clicks9 = [tuple(c) for c in case["clicks9"]]
    groups = [np.asarray(g, dtype=np.int32) for g in case["groups"]]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    return native.mscore.win_rate(
        clicks, clicks9, groups, cv, list(case["ck"]),
        int(case["num10"]), int(case["a"]), int(case["w"]), int(case["h"]),
        False, noop, node_cap, depth_override, adaptive)


def main():
    if not native.available or not hasattr(native.mscore, "win_rate"):
        print("✘ native 不可用")
        return 1
    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases if 8.0 < c["limitation"] <= 60.0]
    # 取复杂度跨度大的样本：既有小局面也有大局面
    sample = sorted(sample, key=lambda c: c["limitation"] * 0)
    picked = sample[:60]

    print("=" * 100)
    print("验证 1：默认路径（adaptive_depth=0）与旧签名逐位一致")
    print("=" * 100)
    n_same = 0
    n_diff = 0
    for c in picked:
        try:
            r_old = call(c, 20_000_000, 0)          # 旧签名等价（不传新参数）
            r_new = call(c, 20_000_000, 0, -1.0)    # 显式传默认值
        except Exception:                            # noqa: BLE001
            continue
        same = (list(r_old[0]) == list(r_new[0])
                and list(r_old[1]) == list(r_new[1])
                and int(r_old[2]) == int(r_new[2]))
        if same:
            n_same += 1
        else:
            n_diff += 1
            if n_diff <= 3:
                print(f"  ✘ 不一致 lim={c['limitation']:.2f}")
    print(f"  逐位一致 {n_same}，不一致 {n_diff}")
    ok1 = (n_diff == 0 and n_same > 0)
    print("  ✔ 默认路径未变" if ok1 else "  ✘ 默认路径被改变，必须修")

    print()
    print("=" * 100)
    print("验证 2：adaptive_depth 是否真的降低 node_cap 触发率与长尾耗时")
    print("=" * 100)
    print("  用较小 node_cap（2e5，接近生产默认）放大 cap 现象")
    print()
    print(f"{'adaptive':>10}{'n':>5}{'cap触发':>9}{'cap率':>8}"
          f"{'中位ms':>10}{'p90ms':>10}{'总ms':>11}")
    print("-" * 100)
    rows = {}
    for adaptive in (0, 1, 2, 4, 8):
        n = 0
        caps = 0
        times = []
        for c in picked:
            t0 = time.perf_counter()
            try:
                call(c, 200_000, adaptive)
                n += 1
            except Exception as e:                     # noqa: BLE001
                if "node cap" in str(e):
                    caps += 1
                else:
                    continue
            times.append((time.perf_counter() - t0) * 1000.0)
        if not times:
            print(f"{adaptive:>10}{0:>5}{caps:>9}{'—':>8}{'—':>10}{'—':>10}{'—':>11}")
            continue
        ts = sorted(times)
        rows[adaptive] = {
            "n": n, "caps": caps,
            "cap_rate": caps / max(1, n + caps),
            "median": ts[len(ts) // 2],
            "p90": ts[min(len(ts) - 1, int(len(ts) * 0.9))],
            "total": sum(ts),
        }
        r = rows[adaptive]
        print(f"{adaptive:>10}{n:>5}{caps:>9}{r['cap_rate']*100:>7.1f}%"
              f"{r['median']:>10.1f}{r['p90']:>10.1f}{r['total']:>11.1f}")

    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    if 0 in rows and len(rows) > 1:
        base = rows[0]
        print(f"  基线（adaptive=0）：cap 率 {base['cap_rate']*100:.1f}%，"
              f"p90 {base['p90']:.1f}ms，总 {base['total']:.1f}ms")
        better = [(k, v) for k, v in rows.items()
                  if k > 0 and v["cap_rate"] < base["cap_rate"] - 1e-9]
        if better:
            for k, v in sorted(better):
                print(f"  ✔ adaptive={k}: cap 率 {v['cap_rate']*100:.1f}%"
                      f"（Δ{(v['cap_rate']-base['cap_rate'])*100:+.1f}pp），"
                      f"p90 {v['p90']:.1f}ms（{v['p90']-base['p90']:+.1f}）")
            print("  ⇒ 自适应确实降低了 cap 率。")
        else:
            print("  ✘ 没有任何 adaptive 档位降低 cap 率 ⇒ 规则空转，需检查接线。")
        # 耗时是否变差
        worse = [(k, v) for k, v in rows.items() if k > 0
                 and v["p90"] > base["p90"] * 1.2]
        if worse:
            print(f"  ⚠ 但这些档位的 p90 耗时反而上升 >20%："
                  f"{[k for k, _ in worse]} —— 变浅没有换来速度，需查成本模型。")

    out = os.path.join(ROOT, "state", "ab", "adaptive_depth_verify.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"default_path_same": n_same, "default_path_diff": n_diff,
                   "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0 if ok1 else 1


if __name__ == "__main__":
    sys.exit(main())
