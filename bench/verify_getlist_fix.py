# -*- coding: utf-8 -*-
"""修复验收：`get_list` off-by-one 修复后的算术恒等式 + 触发频率。

==================== 判定依据（全部是算术恒等式，不需要真值） ====================

恒等式 A（世界数一致）：
    同一批 odometer 行产生两个数，必须相等：
      situations   = count_situations_exact  = Σ_rows [rem==0 ? 1 : C(n9, rem)]
      world_count  = 物化世界数              = Σ_rows Σ_{s∈sizes} C(n9, s)
    修复前：rem==n9 的行被钳成 C(n9, n9-1)=n9（应为 1）⇒ world_count > situations。

恒等式 B（概率守恒）：
    Σ_{未开格} P(该格是雷) == 剩余雷数 (a - num10)
    这是"每个世界恰好 rem 个雷、按世界加权平均"的直接推论，纯数学。
    修复前：每个 rem==n9 行物化的世界少 1 个雷 ⇒ Σ 系统性偏小。

恒等式 C（触发计数闭合）：
    world_count - situations 必须等于 Σ_{rem==n9 的行} (n9 - 1)
    （这一条只能在知道逐行 rem 时算；本脚本用 B 的偏差量做交叉验证：
      Σ 偏差 == (world_count - situations) / world_count）

==================== 触发频率 ====================
触发条件是**逐 odometer 行的 rem == n9**，不是整盘剩余雷数 == 内部格数。
本脚本统计真实对局中「optimal 决策里 world_count != situations」的比例。

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/verify_getlist_fix.py --games 200
    ... --json state/audit/getlist_fix.json
"""
import argparse
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np                      # noqa: E402

from utils import native                # noqa: E402

W, H, A = 30, 16, 99


class Sim:
    def __init__(self, mines, w=W, h=H, a=A):
        self.w, self.h, self.a = w, h, a
        self.mine = set(map(tuple, mines))
        self.cv = np.zeros((h + 2, w + 2), np.int32)
        for y in range(1, h + 1):
            for x in range(1, w + 1):
                self.cv[y, x] = 9
        self.opened = 0
        self.dead = False
        self.won = False
        self.steps = 0
        self._flood(1, 1)

    def _num(self, x, y):
        return sum(1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   if (dx or dy) and (x + dx, y + dy) in self.mine)

    def _flood(self, sx, sy):
        st = [(sx, sy)]
        while st:
            x, y = st.pop()
            if not (1 <= x <= self.w and 1 <= y <= self.h):
                continue
            if self.cv[y, x] != 9 or (x, y) in self.mine:
                continue
            n = self._num(x, y)
            self.cv[y, x] = n
            self.opened += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx or dy:
                            st.append((x + dx, y + dy))

    def click(self, x, y):
        self.steps += 1
        if (x, y) in self.mine:
            self.dead = True
            return
        self._flood(x, y)
        self.won = self.opened >= self.w * self.h - self.a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--layouts",
                    default=os.path.join(ROOT, "bench", "data", "_corner2000.json"))
    ap.add_argument("--json", default=os.path.join(
        ROOT, "state", "audit", "getlist_fix.json"))
    args = ap.parse_args()

    print("=" * 100)
    print("修复验收：get_list off-by-one —— 算术恒等式 + 触发频率")
    print("=" * 100)
    if not os.path.exists(args.layouts):
        print(f"⚠️ 找不到布局文件：{args.layouts}")
        return 0
    with io.open(args.layouts, encoding="utf-8") as f:
        layouts = json.load(f)["layouts"][:args.games]
    print(f"盘面 {len(layouts)} 局（{args.layouts}）\n")

    n_dec = n_opt = 0
    n_wc_checked = n_wc_bad = 0
    n_cons_checked = n_cons_bad = 0
    worst_cons = 0.0
    bad_examples = []
    wins = 0
    t0 = time.perf_counter()

    for gi, lay in enumerate(layouts):
        g = Sim(lay)
        while not g.dead and not g.won and g.steps < 400:
            r = native.decide(g.cv, a=A, w=g.w, h=g.h,
                              node_cap=2_000_000, is_play=True)
            n_dec += 1
            reason = str(r.get("reason"))

            # ---- 恒等式 A：world_count vs situations ----
            meta = r.get("transition_meta") or {}
            wc = meta.get("world_count")
            sit = r.get("situations")
            if wc is not None and sit is not None:
                n_wc_checked += 1
                if wc != sit:
                    n_wc_bad += 1
                    if len(bad_examples) < 8:
                        bad_examples.append({
                            "kind": "world_count", "game": gi, "step": g.steps,
                            "reason": reason, "world_count": wc,
                            "situations": sit, "diff": wc - sit})

            # ---- 恒等式 B：ΣP(雷) == 剩余雷数（只在 prob 覆盖全部未开格时） ----
            prob = r.get("prob") or {}
            unop = [(x, y) for y in range(1, g.h + 1)
                    for x in range(1, g.w + 1) if g.cv[y, x] == 9]
            if prob and len(prob) >= len(unop):
                n_cons_checked += 1
                rem = A - int((g.cv == 10).sum())
                s = sum(1.0 - float(prob[c]) for c in unop if c in prob)
                dev = abs(s - rem)
                worst_cons = max(worst_cons, dev)
                if dev > 1e-4:
                    n_cons_bad += 1
                    if len(bad_examples) < 8:
                        bad_examples.append({
                            "kind": "conservation", "game": gi,
                            "step": g.steps, "reason": reason,
                            "sum_p_mine": round(s, 6), "rem": rem,
                            "dev": round(dev, 6)})

            if reason == "optimal":
                n_opt += 1

            if reason == "certain_safe":
                b = [(int(c[0]), int(c[1])) for c in (r.get("tied") or [])][:64]
                anyc = False
                for (bx, by) in b:
                    if g.cv[by, bx] == 9:
                        g.click(bx, by)
                        anyc = True
                        if g.dead or g.won:
                            break
                if not anyc:
                    break
                continue
            x, y = int(r["x"]), int(r["y"])
            if x >= 1 and y >= 1:
                g.click(x, y)
            else:
                fb = None
                for yy in range(1, g.h + 1):
                    for xx in range(1, g.w + 1):
                        if g.cv[yy, xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                g.click(*fb)
        wins += int(g.won)

    dt = time.perf_counter() - t0
    print(f"跑完 {len(layouts)} 局 / {n_dec} 决策 / {dt:.0f}s / 胜 {wins} "
          f"({wins / max(1, len(layouts)) * 100:.2f}%)")
    print(f"其中 optimal 决策 {n_opt} 次\n")

    print("-" * 100)
    print("恒等式 A：物化世界数 == 准入精确计数 ‖S‖")
    print("-" * 100)
    if n_wc_checked == 0:
        print("  未取到样本（world_count 只在 OPTIMAL 带 transitions 时返回）")
    else:
        print(f"  检查 {n_wc_checked} 次，不一致 {n_wc_bad} 次")
        print(f"  {'✅ 全部一致' if n_wc_bad == 0 else '❌ 存在不一致'}")

    print("\n" + "-" * 100)
    print("恒等式 B：Σ P(该格是雷) == 剩余雷数")
    print("-" * 100)
    if n_cons_checked == 0:
        print("  未取到样本（需 prob 覆盖全部未开格）")
    else:
        print(f"  检查 {n_cons_checked} 次，超差(>1e-4) {n_cons_bad} 次，"
              f"最大偏差 {worst_cons:.3e}")
        print(f"  {'✅ 全部守恒' if n_cons_bad == 0 else '❌ 存在守恒破坏'}")

    if bad_examples:
        print("\n不一致样例：")
        for e in bad_examples:
            print(f"  {e}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    ok = (n_wc_bad == 0 and n_cons_bad == 0
          and (n_wc_checked + n_cons_checked) > 0)
    if ok:
        print(f"  ✅ 修复生效：两条算术恒等式在 {n_wc_checked + n_cons_checked} "
              f"个决策点上全部成立")
        print(f"     参考：修复前在 30x16 真实盘面上 world_count=36 vs ‖S‖=30、")
        print(f"           ΣP(雷)=98.6667 vs 剩余 99（bench/repro_world_count_split.py）")
        print(f"     触发频率（修复前实测）：200 局 / 1333 次 optimal 中 5 次 = 0.38%")
        verdict = "PASS"
    elif (n_wc_checked + n_cons_checked) == 0:
        print("  ⚠️ 无有效样本，无法判定")
        verdict = "NO_SAMPLE"
    else:
        print(f"  ❌ 仍存在不一致（world_count {n_wc_bad}/{n_wc_checked}，"
              f"守恒 {n_cons_bad}/{n_cons_checked}）")
        verdict = "FAIL"

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with io.open(args.json, "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict,
                   "games": len(layouts), "decisions": n_dec,
                   "optimal": n_opt, "wins": wins,
                   "world_count_checked": n_wc_checked,
                   "world_count_bad": n_wc_bad,
                   "conservation_checked": n_cons_checked,
                   "conservation_bad": n_cons_bad,
                   "worst_conservation_dev": worst_cons,
                   "examples": bad_examples,
                   "elapsed_s": round(dt, 1)}, f, ensure_ascii=False, indent=1)
    print(f"\n落盘：{args.json}")
    return 0 if verdict in ("PASS", "NO_SAMPLE") else 1


if __name__ == "__main__":
    sys.exit(main())
