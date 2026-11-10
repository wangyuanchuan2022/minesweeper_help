# -*- coding: utf-8 -*-
"""定点诊断：默认生产参数下 LAYER 2 到底是"没进"还是"进了但失败"，以及
单次决策的 commit 峰值来自哪里。

为什么必须做（2026-11-09 实测）：
  probe_nopywr_live.py 在默认参数下 10 局读到 node_cap_fallback=5、win_rate=0，
  但 max_call 只有 52ms —— 52ms 不可能是 8s 时间闸或 60 万节点耗尽，只能是
  **快速失败**（世界物化超 HARD_CAP / 无世界 / 无候选）。同时 peak_commit
  读到 ~800MB，与"修复后 50~58MB"的旧读数矛盾。这两条不解释就下判断=仪器未校。

本脚本逐次决策记录：
  · 决策前后 commit（Private Bytes）与本次增量
  · reason / cost_log2 / world_upper / estimated_bytes
  · 累计各 reason 计数、commit 全局峰值与来源决策

用法（py310，必须独立进程；C++ 侧 COST_CAP 等是 magic static）：
    <PY310_PYTHON> bench/diag_layer2_commit.py --games 10
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed-base", type=int, default=0,
                    help="从 _corner2000.json 的 layouts 里选局的起点")
    args = ap.parse_args()

    import numpy as np
    import psutil
    from utils import native

    print("COST_CAP_LOG2 env = %r（缺省 = 19.4 编译内置）"
          % os.environ.get("MSW_COST_CAP_LOG2"))
    print("LOOKAHEAD_BUDGET_MS env = %r（缺省 = 8000）"
          % os.environ.get("MSW_LOOKAHEAD_BUDGET_MS"))
    print("LOOKAHEAD_NODE_CAP env = %r（缺省 = 600000）"
          % os.environ.get("MSW_LOOKAHEAD_NODE_CAP"))

    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    layouts = d["layouts"]

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

        def frame(self):
            return self.cv

    proc = psutil.Process()
    commit = lambda: proc.memory_info().private / (1024 * 1024)

    reasons = {}
    worst = []  # (commit_after, reason, world_upper, cost_log2, game, step)
    peak = 0.0
    peak_base = 0.0
    t_call_max = 0.0
    n_calls = 0
    t0 = time.perf_counter()

    for i in range(args.seed_base, args.seed_base + args.games):
        g = Sim(layouts[i])
        game_calls = 0
        while not g.dead and not g.won and g.steps < 3000:
            c0 = commit()
            t1 = time.perf_counter()
            r = native.mscore.decide(g.frame(), a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            c1 = commit()
            n_calls += 1
            game_calls += 1
            t_call_max = max(t_call_max, dt)
            reason = str(r["reason"])
            reasons[reason] = reasons.get(reason, 0) + 1
            if c1 > peak:
                peak = c1
                peak_base = c0
                worst.append((round(c1, 1), reason,
                              r.get("world_upper"), round(float(r.get("cost_log2") or 0), 2),
                              i, g.steps, round(dt, 1)))
            elif reason == "node_cap_fallback":
                worst.append((round(c1, 1), reason,
                              r.get("world_upper"), round(float(r.get("cost_log2") or 0), 2),
                              i, g.steps, round(dt, 1)))
            # 记录所有 fallback 的耗时与代价，用于判断是否"快速失败"
            if reason == "node_cap_fallback":
                print("  [fallback] game=%d step=%d dt=%.1fms world_upper=%s "
                      "cost_log2=%s commit %.0f->%.0fMB"
                      % (i, g.steps, dt, r.get("world_upper"),
                         round(float(r.get("cost_log2") or 0), 2), c0, c1), flush=True)
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

    el = time.perf_counter() - t0
    print("=" * 74)
    print("决策总次数 %d（%d 局，%.1fs，%.1fms/次，max %.0fms）"
          % (n_calls, args.games, el, el * 1000 / max(1, n_calls), t_call_max))
    print("reason: %s" % json.dumps(reasons, sort_keys=True))
    print("commit 峰值 %.0fMB（该次决策前 %.0fMB，净增 %.0fMB）"
          % (peak, peak_base, peak - peak_base))
    print("commit 峰值来源 top: %s" % json.dumps(sorted(worst, reverse=True)[:5]))
    print("进程 boot 后基线 commit = %.0fMB" % peak_base)


if __name__ == "__main__":
    main()
