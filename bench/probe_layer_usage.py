# -*- coding: utf-8 -*-
"""按论文实现取证：同口径采样 reason 分布，回答「实际用到哪一层」。

==================== 为什么要这个 ====================
用户指令：「按照论文实现。看看17万局中实际用到了什么就保留什么」。

⚠️ 事实先说清：178,690 局的口径数据（state/stress20k/runs_20261106.jsonl）
**没有 reason 字段**——实测 179,652 行里含 reason 的 0 行。该语料只有
seed/won/died/steps/ms/rss_mb/pyd_sha8/worker_pid。
⇒ 「17 万局里用了哪一层」**无法从那份语料直接读出**，只能：
   ① 用它算总胜率基线（39.84% / 178,690 局，可直接引用）；
   ② 用**同一协议**（纯 C++ decide 循环、角落首点、30x16/99 雷、
      certain_safe 批量、无效坐标兜底首格）重采样，采集 reason 分布。

本脚本做 ②：协议与 bench/_corner2000.py / stress_20k_production.py 逐条对齐，
并对比「LAYER 2 已移除」的新构建与 17 万局基线。

==================== 论文层对照 ====================
论文（AAAI-17 WS-17-15 §6）三层：
  1. OPTIMAL：|S| 小则精确穷举最优（本项目 world_upper<=DSTAR=400）
  2. 前瞻层（win_rate）：按复杂度自适应深度搜索
  3. pbs + 破平局（PSEQ 计数过滤）
本脚本产出每层实际决策占比，供「保留什么」的裁决。

用法（py310；两臂需独立进程，C++ 常量是 magic static）：
    <PY310_PYTHON> bench/probe_layer_usage.py --games 400
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


LAYER_MAP = {
    # 论文层 1：精确穷举最优
    "optimal": "L1 OPTIMAL(精确popt)",
    # 论文层 2：前瞻（当前已移除）
    "win_rate": "L2 前瞻(已移除)",
    "node_cap_fallback": "L2 前瞻失败降级",
    # 论文层 3：pbs + 破平局
    "limitation": "L3 pbs+PSEQ",
    "clicks9_fallback": "L3 内部格回退",
    "memory_gate": "L3 内存闸降级",
    "certain_safe": "确定性安全批点",
    "no_frontier": "无前沿(内部首格)",
    "no_closed_cell": "无未开格",
    "empty_group": "空组(异常)",
    "part_solve_error": "part_solve失败",
    "pbs_no_feasible_worlds": "pbs无解",
}


def run(games, out_path):
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
    layer = {}
    ms_all = []
    peak = 0.0
    wins = 0
    t0 = time.perf_counter()
    for i in range(games):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = native.mscore.decide(g.cv, a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            ms_all.append(dt)
            reason = str(r["reason"]).split(":")[0]
            reasons[reason] = reasons.get(reason, 0) + 1
            lg = LAYER_MAP.get(reason, "其它:" + reason)
            layer[lg] = layer.get(lg, 0) + 1
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

    ms_sorted = sorted(ms_all)
    n_ms = len(ms_sorted)
    res = {
        "games": games, "wins": wins,
        "win_rate_pct": round(100 * wins / games, 2),
        "ci95": [round(100 * v, 2) for v in wilson(wins, games)],
        "decisions": len(ms_all),
        "wall_s": round(el, 2),
        "reasons": reasons,
        "layer_usage": layer,
        "layer_pct": {k: round(100 * v / max(1, len(ms_all)), 2)
                      for k, v in layer.items()},
        "ms_median": round(ms_sorted[n_ms // 2], 2) if ms_sorted else None,
        "ms_p99": round(ms_sorted[min(n_ms - 1, int(n_ms * 0.99))], 2) if ms_sorted else None,
        "ms_max": round(ms_sorted[-1], 1) if ms_sorted else None,
        "peak_commit_mb": round(peak, 0),
        "pyd_sha8": None,
    }
    import hashlib
    res["pyd_sha8"] = hashlib.sha256(
        open(native.mscore.__file__, "rb").read()).hexdigest()[:8].upper()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--out", default=None)
    ap.add_argument("--arm", default=None)
    args = ap.parse_args()
    if args.arm:
        run(args.games, args.arm)
        return
    out = args.out or os.path.join(ROOT, "output", "_layer_usage.json")
    run(args.games, out)


if __name__ == "__main__":
    main()
