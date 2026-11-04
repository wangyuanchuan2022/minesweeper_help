# -*- coding: utf-8 -*-
"""用**合成小棋盘**验证 adaptive_depth 接线是否真的生效。

=================== 为什么必须用合成用例 ====================
从真实对局采的 260 个中局决策点实测：
  · cost = n_cand × total 的中位只有 4,032，最大 3.57e6
  · 但 **total > 400 的用例占满所有高 cost 点**，而 total 越大世界枚举越慢
  · 实测筛选：total ≤ 400 且 cost > 1e5 的用例 **0 个**
⇒ 想"又快又能触发预算"的真实用例**不存在**。
   硬要挑高 cost 的真实用例，每个要跑几分钟到十几分钟（total=37152 时挂起 >10 分钟）。

这正是 Advisor 指出的结构性困难：**要修的高复杂度局面，恰恰是不可枚举的**。

=================== 合成用例的设计 ====================
手工构造一个 6x6 棋盘 + 少量雷，控制：
  · clicks（前沿候选数）→ 决定 n_cand
  · clicks9（内部未开格）→ 决定 total 的量级
  · groups/ck → 决定枚举规模
让 total 保持在几十~几百（秒级），同时 n_cand 足够大 ⇒ cost 超预算。

判据（这是本脚本唯一要回答的问题）：
  同一个合成用例，adaptive_depth=0 与 >0 的**输出是否不同**。
  · 不同 ⇒ 接线生效（规则真的作用到了 depth_limit）
  · 相同 ⇒ 规则空转（本项目已踩过"参数没接到生产路径"的假象）

用法: python bench/verify_adaptive_depth_synthetic.py
"""
import json
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


def make_case(w, h, a, num10, opened_cells, clicks, clicks9, groups, ck):
    """构造 cell_value：(h+2, w+2)，外框 0，opened 填 1（非 0 数字），
    未开填 9。注意 opened 用 1 而不是 0，避免与外框混淆。"""
    cv = np.zeros((h + 2, w + 2), dtype=np.int32)
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            cv[y, x] = 9
    for (x, y) in opened_cells:
        cv[y, x] = 1
    return {
        "w": w, "h": h, "a": a, "num10": num10,
        "clicks": clicks, "clicks9": clicks9,
        "groups": groups, "ck": ck,
        "cell_value": cv.tolist(),
        "total": None,
    }


def call(case, node_cap, adaptive, depth_override=-1.0, old=False):
    g = [np.asarray(x, dtype=np.int32) for x in case["groups"]]
    cv = np.asarray(case["cell_value"], dtype=np.int32)
    args = ([tuple(x) for x in case["clicks"]],
            [tuple(x) for x in case["clicks9"]],
            g, cv, list(case["ck"]),
            int(case["num10"]), int(case["a"]),
            int(case["w"]), int(case["h"]), False, noop, node_cap)
    if old:
        return native.mscore.win_rate(*args)
    return native.mscore.win_rate(*args, depth_override, adaptive)


def build_cases():
    """构造合成用例：**多组**结构让 n_cand 变大，同时 total 保持小。

    ==================== 上一版为什么失败 ====================
    首版用「1 组 + 大量 clicks9」想拉高 cost，结果：
      · U=20 时 C(20,rem) 让 total 爆炸 ⇒ node cap
      · U=30 时 MemoryError（世界枚举把内存吃光）
    ⇒ 教训：**total 由 clicks9 的组合数决定，不能靠加 clicks9 来加 cost**。

    ==================== 正确构造 ====================
    用 G 个独立组，每组 2 个解 ⇒ total = 2^G（几十~几百），
    而 n_cand = 2G（每组 2 个候选格）⇒ cost = 2G × 2^G。
      G=6  → total=64,  n_cand=12, cost=768
      G=8  → total=256, n_cand=16, cost=4,096
      G=10 → total=1024,n_cand=20, cost=20,480
      G=12 → total=4096,n_cand=24, cost=98,304
      G=13 → total=8192,n_cand=26, cost=212,992  ← 超 1e5 预算
      G=14 → total=16384,n_cand=28,cost=458,752  ← 超 3e5
    """
    cases = []
    w, h = 30, 16
    for G in (6, 8, 9, 10, 11, 12):
        # 每组占 2 个前沿格（同一行相邻两列），共 2G 个候选
        clicks = []
        groups = []
        ck = []
        opened = []
        for gi in range(G):
            x0 = 1 + (gi % 14) * 2
            y0 = 1 + (gi // 14) * 2
            if x0 + 1 > w or y0 > h:
                break
            clicks.append((x0, y0))
            clicks.append((x0 + 1, y0))
            # 该组 2 个解：解1 = 前一格有雷；解2 = 后一格有雷
            groups.append([[1, 0], [0, 1]])
            ck.append(2)
            # 上方一行标记为已开（数字 1），让局面在语义上像真实边界
            if y0 - 1 >= 1:
                opened.append((x0, y0 - 1))
                opened.append((x0 + 1, y0 - 1))
        num10 = 0
        clicks9 = []
        # ⚠️ a 必须等于"前沿组解里的雷总数"（每组 2 解各含 1 雷 ⇒ 每组 1 雷）。
        # 首版写死 a=99 ⇒ rem = 99 - G 无处安放（clicks9 为空）⇒ 0 个世界
        # ⇒ win_rate 抛 ZeroDivisionError。这是构造错误，不是引擎错误。
        a = G
        cv = make_case(w, h, a, num10, opened, clicks, clicks9, groups, ck)
        cases.append(("G%d" % G, cv))
    return cases


def main():
    if not native.available:
        print("✘ native 不可用")
        return 1

    print("=" * 96)
    print("合成用例验证 adaptive_depth 接线")
    print("=" * 96)
    print()

    cases = build_cases()
    rows = []
    for name, case in cases:
        try:
            r0 = call(case, 2_000_000, 0)
        except Exception as e:                             # noqa: BLE001
            print(f"  {name}: 构造失败 {type(e).__name__}: {e}")
            continue
        total = int(r0[2])
        n_cand = len(case["clicks"]) + len(case["clicks9"])
        cost = n_cand * total
        base = list(r0[0])
        line = {"name": name, "total": total, "n_cand": n_cand, "cost": cost,
                "variants": {}}
        print(f"  {name}: total={total} n_cand={n_cand} cost={cost:,}")
        for ad in (0, 1, 3, 5):
            t0 = time.perf_counter()
            try:
                r = call(case, 2_000_000, ad)
            except Exception as e:                         # noqa: BLE001
                print(f"    ad={ad}: {type(e).__name__}")
                continue
            dt = (time.perf_counter() - t0) * 1000.0
            same = (list(r[0]) == base)
            line["variants"][ad] = {"same": same, "ms": dt}
            mark = "相同" if same else "★不同"
            print(f"    ad={ad}: {mark}  {dt:8.1f}ms")
        rows.append(line)

    print()
    print("=" * 96)
    print("判定")
    print("=" * 96)
    fired = [r for r in rows
             if any(not v["same"] for k, v in r["variants"].items() if k > 0)]
    if fired:
        print(f"  ✔ {len(fired)}/{len(rows)} 个用例在 adaptive>0 时输出发生变化"
              f" ⇒ 接线生效，规则真的作用到了 depth_limit。")
        for r in fired:
            ks = [k for k, v in r["variants"].items() if k > 0 and not v["same"]]
            print(f"     {r['name']}: cost={r['cost']:,} 触发的档位 {ks}")
    else:
        print("  ✘ 所有用例在所有 adaptive 档位下输出都相同 ⇒ 规则空转。")
        print("     可能原因：预算仍高于所有用例的 cost，或 adaptive 参数未接到计算段。")
        print(f"     本批用例 cost 范围：{[r['cost'] for r in rows]}")

    out = os.path.join(ROOT, "state", "ab", "adaptive_synthetic.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")
    return 0 if fired else 1


if __name__ == "__main__":
    sys.exit(main())
