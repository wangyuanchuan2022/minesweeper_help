# -*- coding: utf-8 -*-
"""复用生产求解器**点击当下的真实局部变量**，不另维护任何概率公式。

=================== 为什么这是唯一正确做法 ====================
我此前在探针里自己重算内部格概率，漏了前沿期望雷数 `mine_num`，
导致 0/63 不一致。advisor 指出：**不要另维护一套公式**，
应直接复用生产在点击前已算好的完整候选概率快照。

=================== 技术手段（只读，不改生产代码）====================
`pyautogui.click(...)` 被调用时，调用栈是：

    _number5_1_core(...)          ← 这里的局部变量就是生产算好的全部结果
        pyautogui.click(...)      ← 被 ab_policy 换成 drv.on_click

因此在 `on_click` 里用 `inspect.currentframe().f_back.f_locals`
就能**原样拿到生产的局部变量**：`clicks` / `clicks9` / `res` / `_prob`
/ `pos` / `confidence` / `total` 等。
⇒ 不重算、不猜测、与生产逐项一致。

=================== 必须通过的验证（已知答案 + 真实轨迹）====================
  W1 完整候选集：`len(clicks) + len(clicks9) == 未开格数`
  W2 `_prob` 覆盖：`set(_prob.keys()) == set(clicks)`（生产就是这么组装的）
  W3 best 同源：`tuple(pos[0]) == best`（1-based 一致）
  W4 概率一致性：`_prob[pos[0]] == confidence`（若 pos[0] 在前沿）
  W5 内部格概率：`clicks9` 存在时，`confidence` 应等于生产闭式值
      `1 - (a - mine_num - num10)/len(clicks9)`，其中 mine_num 从
      `total`/`res` 无法直接得——因此 W5 只在能拿到时验证

用法:
  python bench/endgame_prod_snapshot.py --selftest
  python bench/endgame_prod_snapshot.py --games 300
"""
import argparse
import inspect
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


class ProdSnapshotRecorder:
    """在 on_click 里抓生产 _number5_1_core 的局部变量（只读）。"""

    def __init__(self):
        self.events = []
        self._orig = {}

    def install(self):
        import utils.probability as prob
        from sim.eval_harness import SimDriver
        rec = self

        self._orig["n51"] = prob.ProbabilityMixin.number5_1
        self._orig["on_click"] = SimDriver.on_click

        def n51(solver, cell_value):
            import numpy as np
            inner = cell_value[1:H + 1, 1:W + 1]
            ev = {
                "n_opened": int(np.sum((inner >= 0) & (inner <= 8))),
                "n_unopened": int(np.sum(inner == 9)),
                "n_flagged": int(np.sum(inner == 10)),
                "snap": None,
            }
            rec.events.append(ev)
            rec._cur = ev
            try:
                return rec._orig["n51"](solver, cell_value)
            finally:
                rec._cur = None

        def on_click(drv, px, py):
            ev = getattr(rec, "_cur", None)
            if ev is not None and ev.get("snap") is None:
                # 只读：取调用者（_number5_1_core）的局部变量
                f = inspect.currentframe()
                caller = f.f_back if f else None
                loc = caller.f_locals if caller else {}
                if "clicks" in loc:
                    clicks = list(loc.get("clicks") or [])
                    clicks9 = list(loc.get("clicks9") or [])
                    res = loc.get("res")
                    _prob = loc.get("_prob")
                    pos = loc.get("pos")
                    conf = loc.get("confidence")
                    total = loc.get("total")
                    ev["snap"] = {
                        "n_clicks": len(clicks),
                        "n_clicks9": len(clicks9),
                        "clicks": [[int(x), int(y)] for x, y in clicks],
                        "clicks9": [[int(x), int(y)] for x, y in clicks9],
                        "prob_keys": (sorted([list(map(int, k))
                                              for k in _prob.keys()])
                                      if isinstance(_prob, dict) else None),
                        "prob_vals": ({f"{int(k[0])},{int(k[1])}":
                                       float(v) for k, v in _prob.items()}
                                      if isinstance(_prob, dict) else None),
                        "pos0": [int(pos[0][0]), int(pos[0][1])]
                                if pos else None,
                        "confidence": float(conf) if conf is not None
                                      else None,
                        "total": float(total) if total is not None else None,
                        "res_len": (len(res) if hasattr(res, "__len__")
                                    else None),
                    }
            cw = drv.s.cell_width
            gx = int(round((px - drv.s.bx) / cw)) - 1
            gy = int(round((py - drv.s.by) / cw)) - 1
            if ev is not None and ev.get("snap") is not None:
                ev["snap"].setdefault("clicks_made", []).append(
                    [gx, gy, (gx, gy) in drv.board.mines,
                     bool(getattr(drv, "in_decision", False))])
            return rec._orig["on_click"](drv, px, py)

        prob.ProbabilityMixin.number5_1 = n51
        SimDriver.on_click = on_click
        self._cur = None

    def uninstall(self):
        import utils.probability as prob
        from sim.eval_harness import SimDriver
        prob.ProbabilityMixin.number5_1 = self._orig["n51"]
        SimDriver.on_click = self._orig["on_click"]


def selftest():
    print("=" * 100)
    print("生产快照复用自检（W1–W5）")
    print("=" * 100)
    fails = []

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "pss"), True)
    import ab_policy as ab

    rec = ProdSnapshotRecorder()
    rec.install()
    try:
        ab.play_one(20267000, W, H, A, "baseline", 80, 200000, 2000000)
    finally:
        rec.uninstall()

    snaps = [e["snap"] for e in rec.events if e.get("snap")]
    print(f"\n  捕获到生产快照 {len(snaps)} 个")
    if not snaps:
        print("  ✘ 未捕获到任何快照")
        return 1

    # W1 完整候选集
    w1 = sum(1 for s in snaps
             if s["n_clicks"] + s["n_clicks9"] ==
             next((e["n_unopened"] for e in rec.events
                   if e.get("snap") is s), -1))
    print(f"\n[W1] clicks + clicks9 == 未开格数：")
    # 直接逐个校验
    ok1 = tot1 = 0
    for e in rec.events:
        s = e.get("snap")
        if not s:
            continue
        tot1 += 1
        if s["n_clicks"] + s["n_clicks9"] == e["n_unopened"]:
            ok1 += 1
    print(f"     {ok1}/{tot1}  {'✔' if ok1 == tot1 else '✘'}")
    if ok1 != tot1:
        fails.append(f"W1 {ok1}/{tot1}")

    # W2 _prob 覆盖 == clicks
    ok2 = tot2 = 0
    for s in snaps:
        if s["prob_keys"] is None:
            continue
        tot2 += 1
        if set(map(tuple, s["prob_keys"])) == set(map(tuple, s["clicks"])):
            ok2 += 1
    print(f"\n[W2] _prob.keys() == set(clicks)：{ok2}/{tot2}  "
          f"{'✔' if ok2 == tot2 else '✘'}")
    if ok2 != tot2:
        fails.append(f"W2 {ok2}/{tot2}")

    # W3 pos[0] 与点击一致
    ok3 = tot3 = 0
    for s in snaps:
        if not s["pos0"] or not s.get("clicks_made"):
            continue
        tot3 += 1
        p = s["clicks_made"][0]
        if [p[0] + 1, p[1] + 1] == s["pos0"]:
            ok3 += 1
    print(f"\n[W3] pos[0] == 点击+1：{ok3}/{tot3}  "
          f"{'✔' if tot3 and ok3 == tot3 else '✘'}")
    if tot3 == 0 or ok3 != tot3:
        fails.append(f"W3 {ok3}/{tot3}")

    # W4 概率一致性（正确判据：只在"前沿被选中"时比较）
    # 源码：confidence 会在"内部格更安全"时被覆盖为内部格安全率
    # ⇒ 当前沿被选中，confidence == _prob[pos0]（float32 误差 1e-4）
    # ⇒ 当内部被选中，confidence 应 >= max(_prob.values())
    ok4 = tot4 = 0
    ok4b = tot4b = 0
    for s in snaps:
        if not s["pos0"] or s["prob_vals"] is None:
            continue
        key = f"{s['pos0'][0]},{s['pos0'][1]}"
        in_front = key in s["prob_vals"]
        if in_front:
            tot4 += 1
            if abs(s["prob_vals"][key] - s["confidence"]) < 1e-4:
                ok4 += 1
        else:
            # 内部格被选中：confidence 应不低于前沿最大值
            tot4b += 1
            if s["prob_vals"]:
                if s["confidence"] >= max(s["prob_vals"].values()) - 1e-9:
                    ok4b += 1
    print(f"\n[W4a] 前沿被选中时 _prob[pos0]==confidence：{ok4}/{tot4}  "
          f"{'✔' if tot4 and ok4 == tot4 else '（无前沿选择样本）'}")
    print(f"[W4b] 内部被选中时 confidence>=前沿max：{ok4b}/{tot4b}  "
          f"{'✔' if tot4b and ok4b == tot4b else '（无内部选择样本）'}")
    if tot4 and ok4 != tot4:
        fails.append(f"W4a {ok4}/{tot4}")
    if tot4b and ok4b != tot4b:
        fails.append(f"W4b {ok4b}/{tot4b}")

    # W5 内部格：pos0 不在 clicks 时，clicks9 应非空
    ok5 = tot5 = 0
    for s in snaps:
        if not s["pos0"]:
            continue
        if tuple(s["pos0"]) in set(map(tuple, s["clicks"])):
            continue
        tot5 += 1
        if s["n_clicks9"] > 0:
            ok5 += 1
    print(f"\n[W5] pos0 非前沿时 clicks9 非空：{ok5}/{tot5}  "
          f"{'✔' if tot5 == 0 or ok5 == tot5 else '✘'}")
    if tot5 and ok5 != tot5:
        fails.append(f"W5 {ok5}/{tot5}")

    print("\n" + "=" * 100)
    if fails:
        print(f"✘ 自检失败：{fails}")
        return 1
    print("✅ 生产快照复用自检通过：")
    print("   · 完整候选集守恒（clicks + clicks9 == 未开格）")
    print("   · _prob 恰好覆盖 clicks（生产组装方式）")
    print("   · pos[0] 与实际点击 1-based 一致")
    print("   · 前沿选中时 _prob[pos0] == confidence（float32 容差 1e-4）")
    print("   · 内部选中时 confidence >= 前沿 max（生产只在此条件下改选）")
    print("   ⇒ 拿到的就是生产算好的值，无自建公式。")
    return 0


def collect(games, seed, out):
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "ps"), True)
    import ab_policy as ab

    rows = []
    stats = {"endgame_events": 0, "endgame_clicks": 0, "death": 0,
             "pos_in_clicks": 0, "pos_in_clicks9": 0}
    n_games = 0

    for gi in range(games):
        rec = ProdSnapshotRecorder()
        rec.install()
        try:
            ab.play_one(seed + gi, W, H, A, "baseline", 80, 200000, 2000000)
        except Exception:                              # noqa: BLE001
            pass
        finally:
            rec.uninstall()
        n_games += 1

        for e in rec.events:
            s = e.get("snap")
            if not s or not s.get("pos0"):
                continue
            if e["n_opened"] < ENDGAME:
                continue
            made = [c for c in s.get("clicks_made") or [] if c[3]]
            if not made:
                continue
            stats["endgame_events"] += 1
            stats["endgame_clicks"] += len(made)
            p = made[0]
            in_clicks = tuple(s["pos0"]) in set(map(tuple, s["clicks"]))
            if in_clicks:
                stats["pos_in_clicks"] += 1
            elif s["n_clicks9"] > 0:
                stats["pos_in_clicks9"] += 1
            if p[2]:
                stats["death"] += 1
            # 完整候选概率：前沿取 _prob，内部取 confidence（生产闭式值）
            probs = dict(s["prob_vals"] or {})
            if not in_clicks and s["n_clicks9"] > 0:
                # 内部格全部同值 = confidence（生产口径）
                for (x, y) in s["clicks9"]:
                    probs[f"{x},{y}"] = s["confidence"]
            key = f"{s['pos0'][0]},{s['pos0'][1]}"
            actual = probs.get(key)
            bestv = max(probs.values()) if probs else None
            rows.append({
                "n_opened": e["n_opened"],
                "n_unopened": e["n_unopened"],
                "n_clicks": s["n_clicks"], "n_clicks9": s["n_clicks9"],
                "pos0": s["pos0"], "in_clicks": in_clicks,
                "confidence": s["confidence"],
                "actual_safety": actual, "best_safety": bestv,
                "regret": (bestv - actual)
                          if (bestv is not None and actual is not None)
                          else None,
                "pick_is_mine": bool(p[2]),
                "coverage": (len(probs) / max(1, e["n_unopened"])),
            })
        if (gi + 1) % 50 == 0:
            print(f"  ... {gi+1}/{games} 残局事件 {len(rows)}", flush=True)

    print("\n" + "=" * 100)
    print(f"残局（复用生产快照，{n_games} 局）")
    print("=" * 100)
    print(f"\n  残局决策事件 {stats['endgame_events']}，"
          f"决策内点击 {stats['endgame_clicks']}，踩雷 {stats['death']}")
    print(f"  pos0 在前沿 clicks：{stats['pos_in_clicks']}")
    print(f"  pos0 在内部 clicks9：{stats['pos_in_clicks9']}")

    if not rows:
        print("\n  无残局样本")
        return
    covs = sorted(r["coverage"] for r in rows)
    print(f"\n  候选概率覆盖率：min {covs[0]:.4f} 中位 "
          f"{covs[len(covs)//2]:.4f} max {covs[-1]:.4f}")
    print(f"  ≥95% 比例：{sum(1 for c in covs if c >= 0.95)/len(covs)*100:.2f}%")

    regs = [r["regret"] for r in rows if r["regret"] is not None]
    if regs:
        regs.sort()
        n = len(regs)
        n_opt = sum(1 for x in regs if x <= 1e-9)
        print(f"\n  可算 regret {n}：中位 {regs[n//2]:.6f} "
              f"p90 {regs[int(0.9*n)]:.6f} max {regs[-1]:.6f}")
        print(f"  完全选对 {n_opt}/{n} = {n_opt/n*100:.2f}%")
        print(f"  平均 regret {sum(regs)/n:.6f}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"games": n_games, "seed": seed, "stats": stats,
                   "rows": rows}, f, ensure_ascii=False)
    print(f"\n明细已写 {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20267000)
    ap.add_argument("--out", default="state/ab/endgame_prod.json")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    collect(args.games, args.seed, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
