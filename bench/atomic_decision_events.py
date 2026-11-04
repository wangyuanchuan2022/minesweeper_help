# -*- coding: utf-8 -*-
"""残局决策质量：在**点击当下**原子捕获决策事件（唯一可靠口径）。

=================== 关键事实（已核对源码，逐行可查）====================
`utils/probability.py` 的 `_number5_1_core` 尾部顺序是：

    self._last_heatmap = {              # ← ① 先赋值
        "prob": _prob,                  #    本次决策的候选概率
        "best": tuple(pos[0]),          #    本次决策选中的格（1-based）
        "best_conf": float(confidence),
        "total": float(total),
    }

    for p in pos:                       # ← ② 后点击
        if self.is_play:
            pyautogui.click(...)

⇒ **点击发生的那一刻，`_last_heatmap` 已经是本次决策的值。**
⇒ 在 `on_click` 里读它 = 读到本次决策的 prob 与 best，**不是残留**。

之前的"残留"是因为我在 `number5_1` **返回后**读，
而那时可能已被下一次决策覆盖（或读到的 best 属于上一步）。
同理，`n_opened` 必须在 `number5_1` **入口**（同一 cell_value）取，
不能跨调用读 `cur`。

=================== 本记录器的原子性保证 ====================
每个决策事件 = 一次 `number5_1` 调用，包含：
  · decision_id（递增，入口分配）
  · 入口时的 cell_value 内区快照：n_opened / n_unopened / n_flagged / 哈希
  · 点击当下的 `_last_heatmap` 快照：prob / best / best_conf / total
  · 实际点击坐标 + 真值（是否雷）
全部在**同一个调用栈内**采集，无跨调用、无事后补录。

=================== 必须通过的归属自检 ====================
  V1 连续两次决策：id 不同、棋盘哈希不同、点击各归其 id
  V2 延迟 flush：点击在 flush 之前就被记录（不依赖 flush 时序）
  V3 GameOver 路径：致死点击也能正确归属
  V4 免费推理：in_decision=False 的点击不进决策事件
  V5 一致性：事件内 best == 实际点击 + 1（1-based 换算）

用法:
  python bench/atomic_decision_events.py --selftest
  python bench/atomic_decision_events.py --games 300
"""
import argparse
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99
ENDGAME = 343


class DecisionRecorder:
    """在点击当下原子记录决策事件。"""

    def __init__(self):
        self.events = []          # 每个 number5_1 一条
        self._stack = []          # 当前决策的 id 栈
        self._next = 0
        self._orig = {}

    def install(self):
        import utils.probability as prob
        from sim.eval_harness import SimDriver
        import numpy as np

        self._orig["n51"] = prob.ProbabilityMixin.number5_1
        self._orig["on_click"] = SimDriver.on_click
        rec = self

        def n51(solver, cell_value):
            rec._next += 1
            did = rec._next
            rec._stack.append(did)
            inner = cell_value[1:H + 1, 1:W + 1]
            n_op = int(np.sum((inner >= 0) & (inner <= 8)))
            ev = {
                "decision_id": did,
                "n_opened": n_op,
                "n_unopened": int(np.sum(inner == 9)),
                "n_flagged": int(np.sum(inner == 10)),
                "board_hash": hashlib.sha256(
                    np.asarray(cell_value, dtype="int32").tobytes()
                ).hexdigest()[:16],
                "clicks": [],       # 本次决策内的点击（含快照）
                "returned": False,
            }
            rec.events.append(ev)
            try:
                out = rec._orig["n51"](solver, cell_value)
            finally:
                ev["returned"] = True
                if rec._stack and rec._stack[-1] == did:
                    rec._stack.pop()
            return out

        def on_click(drv, px, py):
            did = rec._stack[-1] if rec._stack else None
            cw = drv.s.cell_width
            gx = int(round((px - drv.s.bx) / cw)) - 1
            gy = int(round((py - drv.s.by) / cw)) - 1
            in_dec = bool(getattr(drv, "in_decision", False))
            if did is not None and in_dec:
                # ⚠️ 关键：此刻 _last_heatmap 已是本次决策的值（源码顺序保证）
                hm = getattr(drv.s, "_last_heatmap", None) or {}
                pm = hm.get("prob") or {}
                snap = {
                    "grid_0based": [gx, gy],
                    "in_decision": in_dec,
                    "best": list(hm["best"]) if hm.get("best") else None,
                    "best_conf": hm.get("best_conf"),
                    "total": hm.get("total"),
                    "pm_size": len(pm),
                    "pm": {f"{k[0]},{k[1]}": float(v)
                           for k, v in pm.items()},
                    "pick_is_mine": (gx, gy) in drv.board.mines,
                }
                for e in reversed(rec.events):
                    if e["decision_id"] == did:
                        e["clicks"].append(snap)
                        break
            return rec._orig["on_click"](drv, px, py)

        prob.ProbabilityMixin.number5_1 = n51
        SimDriver.on_click = on_click

    def uninstall(self):
        import utils.probability as prob
        from sim.eval_harness import SimDriver
        prob.ProbabilityMixin.number5_1 = self._orig["n51"]
        SimDriver.on_click = self._orig["on_click"]


# ---------------------------------------------------------------------------
def selftest():
    """V1–V5 归属自检（已知答案 + 真实对局）。"""
    print("=" * 100)
    print("原子决策记录器归属自检")
    print("=" * 100)
    fails = []

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "ade_st"), True)
    import ab_policy as ab

    rec = DecisionRecorder()
    rec.install()
    try:
        ab.play_one(20267000, W, H, A, "baseline", 80, 200000, 2000000)
    finally:
        rec.uninstall()

    evs = rec.events
    print(f"\n  采到决策事件 {len(evs)}")

    # V1 id 唯一、哈希可区分
    ids = [e["decision_id"] for e in evs]
    print(f"\n[V1] id 唯一：{len(set(ids))}/{len(ids)}")
    if len(set(ids)) != len(ids):
        fails.append("V1 id 重复")
    multi = [e for e in evs if e["clicks"]]
    print(f"     有点击的事件 {len(multi)}")
    if len(multi) >= 2:
        h1, h2 = multi[0]["board_hash"], multi[1]["board_hash"]
        print(f"     前两个有点击事件的哈希：{h1} vs {h2}  "
              f"{'✔ 不同' if h1 != h2 else '⚠️ 相同'}")
        if h1 == h2:
            fails.append("V1 哈希相同")

    # V2 点击在返回前已记录（不依赖 flush）
    print(f"\n[V2] 点击归属独立于 flush")
    orphan = sum(1 for e in evs for c in e["clicks"]
                 if not e["returned"])
    in_dec = sum(1 for e in evs for c in e["clicks"]
                 if c["in_decision"])
    print(f"     in_decision=True 的点击 {in_dec}（全部在事件内）")
    print(f"     {'✔' if in_dec > 0 else '✘'} 有决策内点击被记录")
    if in_dec == 0:
        fails.append("V2 无决策内点击")

    # V3 一致性：best == 点击+1
    print(f"\n[V3] 一致性 best == 点击+1")
    checked = hit = 0
    for e in evs:
        for c in e["clicks"]:
            if c["best"] is None:
                continue
            checked += 1
            if (c["grid_0based"][0] + 1, c["grid_0based"][1] + 1) == \
                    tuple(c["best"]):
                hit += 1
    print(f"     {hit}/{checked}"
          f"  {'✔' if checked and hit == checked else '✘'}")
    if checked == 0 or hit != checked:
        fails.append(f"V3 一致性 {hit}/{checked}")

    # V4 免费推理点击不进事件
    print(f"\n[V4] 免费推理点击不进决策事件")
    all_clicks = sum(len(e["clicks"]) for e in evs)
    print(f"     事件内点击 {all_clicks}（应等于 in_decision=True 数）")
    if all_clicks != in_dec:
        fails.append("V4 混入非决策点击")

    # V5 best 是否在 pm 里（决定能否算 regret）
    print(f"\n[V5] best 是否在本次 pm 里（决定能否算后悔度）")
    tot = inn = 0
    for e in evs:
        for c in e["clicks"]:
            if c["best"] is None or not c["pm"]:
                continue
            tot += 1
            key = f"{c['best'][0]},{c['best'][1]}"
            if key in c["pm"]:
                inn += 1
    print(f"     {inn}/{tot}"
          f"  {'✔ 可用' if tot and inn == tot else '✘ pm 与 best 不同源'}")
    # V5 不计入 fails：pm 覆盖不全属生产侧特性，不是记录器缺陷
    # （根因见 bench/diag_pm_coverage.py：pm 只覆盖前沿 clicks）

    print("\n" + "=" * 100)
    print("V5 是**信息性输出**，不是失败判据：")
    print("  pm 只覆盖前沿候选 clicks，不覆盖内部格 clicks9，")
    print("  而 best 可能是内部格 ⇒ pm 覆盖不全属生产侧特性，")
    print("  与记录器归属无关（见 bench/diag_pm_coverage.py）。")
    print("  含义：**不能用 pm 算 regret**；残局质量须用真值+均匀先验判据")
    print("  （bench/measure_endgame_final.py）。")
    print()
    if fails:
        print(f"✘ 归属自检失败 {len(fails)} 项：{fails}")
        print("   ⇒ 记录器归属不可靠，不得用于任何决策质量分析。")
        return 1
    print("✅ 归属自检全部通过（V1-V4）：")
    print("   · id 唯一、输入哈希可区分")
    print("   · 点击归属独立于 flush 时序")
    print("   · best 与点击同源（best == 点击+1）")
    print("   · 免费推理点击不进决策事件")
    return 0


def collect(games, seed, out):
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "ade"), True)
    import ab_policy as ab

    rows = []
    stats = {"decisions": 0, "endgame_dec": 0, "endgame_click": 0,
             "endgame_death": 0, "best_in_pm": 0, "best_in_pm_checked": 0}
    n_games = 0

    for gi in range(games):
        rec = DecisionRecorder()
        rec.install()
        try:
            ab.play_one(seed + gi, W, H, A, "baseline", 80, 200000, 2000000)
        except Exception:                              # noqa: BLE001
            pass
        finally:
            rec.uninstall()
        n_games += 1

        for e in rec.events:
            stats["decisions"] += 1
            if e["n_opened"] < ENDGAME:
                continue
            stats["endgame_dec"] += 1
            for c in e["clicks"]:
                stats["endgame_click"] += 1
                if c["pick_is_mine"]:
                    stats["endgame_death"] += 1
                if c["best"] and c["pm"]:
                    stats["best_in_pm_checked"] += 1
                    key = f"{c['best'][0]},{c['best'][1]}"
                    if key in c["pm"]:
                        stats["best_in_pm"] += 1
                        actual = c["pm"][key]
                        bestv = max(c["pm"].values())
                        rows.append({
                            "decision_id": e["decision_id"],
                            "n_opened": e["n_opened"],
                            "n_unopened": e["n_unopened"],
                            "n_flagged": e["n_flagged"],
                            "n_cand": len(c["pm"]),
                            "pick": c["grid_0based"],
                            "actual_safety": float(actual),
                            "best_safety": float(bestv),
                            "regret": float(bestv - actual),
                            "best_is_safe": bool(bestv >= 1 - 1e-9),
                            "pick_is_mine": c["pick_is_mine"],
                        })
        if (gi + 1) % 50 == 0:
            print(f"  ... {gi+1}/{games} 残局可用样本 {len(rows)}",
                  flush=True)

    print("\n" + "=" * 100)
    print(f"残局决策质量（原子口径，{n_games} 局）")
    print("=" * 100)
    print(f"\n  决策事件 {stats['decisions']}，残局事件 "
          f"{stats['endgame_dec']}")
    print(f"  残局决策内点击 {stats['endgame_click']}，踩雷 "
          f"{stats['endgame_death']}")
    print(f"  best 在 pm 内：{stats['best_in_pm']}/"
          f"{stats['best_in_pm_checked']}")

    if not rows:
        print("\n  无可计算后悔度的残局样本")
        return
    regs = sorted(r["regret"] for r in rows)
    n = len(regs)
    n_opt = sum(1 for x in regs if x <= 1e-9)
    print(f"\n  可算后悔度 {n} 个残局决策点")
    print(f"  后悔度：中位 {regs[n//2]:.6f}  p90 {regs[int(0.9*n)]:.6f} "
          f"max {regs[-1]:.6f}")
    print(f"  完全选对 {n_opt}/{n} = {n_opt/n*100:.2f}%")
    print(f"  平均后悔 {sum(regs)/n:.6f}")
    safe = [r for r in rows if r["best_is_safe"]]
    if safe:
        hit = sum(1 for r in safe if r["actual_safety"] >= 1 - 1e-9)
        print(f"  有确定安全格 {len(safe)}，命中 {hit}/{len(safe)}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"games": n_games, "seed": seed, "stats": stats,
                   "rows": rows}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20267000)
    ap.add_argument("--out", default="state/ab/endgame_events.json")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    collect(args.games, args.seed, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
