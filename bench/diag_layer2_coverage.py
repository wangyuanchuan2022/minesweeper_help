# -*- coding: utf-8 -*-
"""定位 LAYER 2 真实可覆盖区间：修正估算器后，哪些局面«本可精确前瞻»却被拒？

==================== 必须回答的问题 ====================
修正准入闸后（估算器改为 max_s C(N,s) 的 sound 上界），300 局里 LAYER 2 只成功
2 次、失败 1529 次。两个互斥解释：

  H1「估算器仍偏高」：真实物化数 << 上界，上界误杀本可算动的局面。
     判据：找 cost_log2 落在 (19.4, 某值] 的决策，实测它们若强行进 LAYER 2
     能否在 8s 内成功。若能成功且显著多 ⇒ 上界过松，需改精确计数。
  H2「COST_CAP 太小」：这些局面真实规模就是几十万~几百万世界，8s 内算不动。
     判据：强制放开 COST_CAP=40 后，成功（win_rate）的比例与单次耗时分布。

本脚本直接测 H1/H2（不靠推断）：
  A 臂：默认（修正后的准入闸）——reason 分布
  B 臂：MSW_COST_CAP_LOG2=25（≈3.3e7 世界）——看能不能算出更多 win_rate
  C 臂：MSW_COST_CAP_LOG2=40 + 8s 闸——看真实耗时上限与成功率

⚠️ C++ 的 COST_CAP 是 magic static ⇒ 三臂必须**各自独立子进程**。

用法：
    <PY310_PYTHON> bench/diag_layer2_coverage.py --games 60
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32


def arm_run(games, out_path):
    """子进程：跑一臂，统计 reason / win_rate 耗时。"""
    import time
    import numpy as np
    import psutil
    from utils import native

    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    layouts = d["layouts"][:games]

    class Sim:
        def __init__(self, mines_xy1):
            self.mine = [[False] * W2 for _ in range(H + 2)]
            for (mx, my) in mines_xy1:
                self.mine[my][mx] = True
            self.cv = np.zeros((H + 2, W2), dtype=np.int32)
            for y in range(1, H + 1):
                for x in range(1, W + 1):
                    self.cv[y][x] = 9
            self.opened = 0
            self.dead = False
            self.steps = 0
            self.won = False
            self._flood(1, 1)

        def number(self, x, y):
            c = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 1 <= nx <= W and 1 <= ny <= H and self.mine[ny][nx]:
                        c += 1
            return c

        def _flood(self, sx, sy):
            stack = [(sx, sy)]
            while stack:
                x, y = stack.pop()
                if self.cv[y][x] != 9 or self.mine[y][x]:
                    continue
                n = self.number(x, y)
                self.cv[y][x] = n
                self.opened += 1
                if n == 0:
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            stack.append((x + dx, y + dy))

        def click(self, x, y):
            self.steps += 1
            if self.mine[y][x]:
                self.dead = True
                return
            self._flood(x, y)
            self.won = self.opened >= W * H - A

    proc = psutil.Process()
    reasons = {}
    wr_ms = []
    npc_ms = []
    peak = 0.0
    wins = 0
    t0 = time.perf_counter()
    for i in range(games):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = native.mscore.decide(g.cv, a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            reason = str(r["reason"])
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason == "win_rate":
                wr_ms.append(dt)
            elif reason == "node_cap_fallback":
                npc_ms.append(dt)
            peak = max(peak, proc.memory_info().private / (1024 * 1024))
            if reason == "certain_safe":
                batch = [(int(c[0]), int(c[1])) for c in (r["tied"] or [])][:64]
                clicked = False
                for (bx, by) in batch:
                    if g.cv[by][bx] == 9:
                        g.click(bx, by)
                        clicked = True
                        if g.dead or g.won:
                            break
                if not clicked:
                    break
                continue
            x, y = int(r["x"]), int(r["y"])
            if x >= 1 and y >= 1:
                g.click(x, y)
            else:
                fb = None
                for yy in range(1, H + 1):
                    for xx in range(1, W + 1):
                        if g.cv[yy][xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                g.click(fb[0], fb[1])
        wins += 1 if g.won else 0
    el = time.perf_counter() - t0

    def q(v, p):
        if not v:
            return None
        s = sorted(v)
        return round(s[min(len(s) - 1, int(len(s) * p))], 1)

    res = {
        "cap": os.environ.get("MSW_COST_CAP_LOG2", "default"),
        "games": games, "wins": wins, "wall_s": round(el, 2),
        "reasons": reasons, "peak_commit_mb": round(peak, 0),
        "wr_n": len(wr_ms), "wr_ms_med": q(wr_ms, 0.5), "wr_ms_max": q(wr_ms, 0.99),
        "npc_n": len(npc_ms), "npc_ms_med": q(npc_ms, 0.5),
        "npc_ms_max": q(npc_ms, 0.99),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--arm", default=None)
    args = ap.parse_args()
    if args.arm:
        arm_run(args.games, args.arm)
        return

    tmp = os.path.join(ROOT, "output")
    os.makedirs(tmp, exist_ok=True)
    arms = [("A 默认（修正准入闸）", {}),
            ("B cap=25", {"MSW_COST_CAP_LOG2": "25"}),
            ("C cap=40", {"MSW_COST_CAP_LOG2": "40", "MSW_LOOKAHEAD_BUDGET_MS": "8000"})]
    results = []
    for idx, (label, extra) in enumerate(arms):
        out = os.path.join(tmp, "_layer2cov_arm%d.json" % idx)
        env = dict(os.environ)
        env.pop("MSW_COST_CAP_LOG2", None)
        env.pop("MSW_LOOKAHEAD_BUDGET_MS", None)
        env.update(extra)
        env["PYTHONIOENCODING"] = "utf-8"
        print("=" * 74)
        print("臂 %s  env=%s" % (label, extra or "(默认)"))
        rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                              "--arm", out, "--games", str(args.games)],
                             env=env, cwd=ROOT)
        if rc != 0:
            print("[WARN] 臂 %s rc=%d" % (label, rc))
        try:
            with open(out, encoding="utf-8") as fh:
                results.append({"label": label, **json.load(fh)})
        except (OSError, ValueError) as e:
            print("[WARN] 读取臂结果失败:", e)

    print("=" * 74)
    print("汇总：")
    for r in results:
        print("  %-22s win_rate=%-3d (med %sms) fallback=%-3d (med %sms) "
              "peak=%.0fMB" % (r["label"], r["wr_n"], r["wr_ms_med"],
                               r["npc_n"], r["npc_ms_med"], r["peak_commit_mb"]))
        print("      reasons: %s" % json.dumps(r["reasons"], sort_keys=True,
                                               ensure_ascii=False))
    print()
    worst = max((r["wr_ms_max"] or 0) for r in results)
    print("判定：win_rate 单次最坏耗时 %.0fms；凡 fallback 中位数远小于该值 ⇒ "
          "属「快速失败」而非真实算不完。" % worst)


if __name__ == "__main__":
    main()
