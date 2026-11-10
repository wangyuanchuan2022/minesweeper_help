# -*- coding: utf-8 -*-
"""标定 LAYER 2 的 node_cap：修正准入闸后，真实需要的节点预算是多少？

==================== 已确认的链条（2026-11-09）====================
① 准入闸方向性错误（已修）：旧估算用 C(N, rem_max) 当上界，C(N,r) 单峰
   ⇒ rem_max 靠近 N 时低估达指数级 ⇒ 放行算不动的局面 ⇒ 45ms 白烧。
   修复后：cost_log2 与真实规模一致（失败盘面 9.17 → 35.38），不再放行。
② 修好后暴露下一层：进入 LAYER 2 的都是真实可算规模（cost_log2 ≤ 19.4），
   但 ExactEngine 撞 `node cap exceeded: visited=600001 cap=600000`。
   ⇒ **准入对了，节点预算太小**。

==================== 本脚本要回答 ====================
Q node_cap 取多少，才能让「准入放行」局面的 win_rate 真正算完，
  且单次决策墙钟仍 ≤ 时间预算（默认 8s）？

方法：固定准入闸（默认 19.4），扫 node_cap ∈ {60万, 150万, 300万, 500万}
（ExactEngine 侧上限 500 万，见 :3426），每臂独立子进程（magic static）。
每臂记录：win_rate 成功次数、fallback 次数、win_rate 单次耗时中位/最大值、
整体胜率（同盘面对比，纯 C++ decide 循环口径）。

⚠️ 臂间必须同 seed 同 layouts（_corner2000.json 前 N 局），否则胜率不可比。

用法：
    <PY310_PYTHON> bench/calibrate_layer2_nodecap.py --games 40
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
    wr_ms, npc_ms = [], []
    peak = 0.0
    wins = 0
    all_ms = []
    t0 = time.perf_counter()
    for i in range(games):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = native.mscore.decide(g.cv, a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            all_ms.append(dt)
            reason = str(r["reason"])
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
            if key == "win_rate":
                wr_ms.append(dt)
            elif key == "node_cap_fallback":
                npc_ms.append(dt)
            peak = max(peak, proc.memory_info().private / (1024 * 1024))
            if key == "certain_safe":
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

    res = {"node_cap": os.environ.get("MSW_LOOKAHEAD_NODE_CAP", "default"),
           "games": games, "wins": wins,
           "win_rate_pct": round(100 * wins / games, 2),
           "wall_s": round(el, 2), "reasons": reasons,
           "peak_commit_mb": round(peak, 0),
           "wr_n": len(wr_ms), "wr_med": q(wr_ms, 0.5), "wr_p99": q(wr_ms, 0.99),
           "npc_n": len(npc_ms), "npc_med": q(npc_ms, 0.5),
           "all_ms_med": q(all_ms, 0.5), "all_ms_p99": q(all_ms, 0.99),
           "all_ms_max": round(max(all_ms), 1) if all_ms else None}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--arm", default=None)
    args = ap.parse_args()
    if args.arm:
        arm_run(args.games, args.arm)
        return

    tmp = os.path.join(ROOT, "output")
    os.makedirs(tmp, exist_ok=True)
    caps = ["600000", "1500000", "3000000", "5000000"]
    results = []
    for idx, cap in enumerate(caps):
        out = os.path.join(tmp, "_nodecap_arm%d.json" % idx)
        env = dict(os.environ)
        env.pop("MSW_LOOKAHEAD_NODE_CAP", None)
        env["MSW_LOOKAHEAD_NODE_CAP"] = cap
        env["PYTHONIOENCODING"] = "utf-8"
        print("=" * 74)
        print("臂 node_cap=%s" % cap, flush=True)
        rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                              "--arm", out, "--games", str(args.games)],
                             env=env, cwd=ROOT)
        if rc != 0:
            print("[WARN] 臂 %s rc=%d" % (cap, rc))
        try:
            with open(out, encoding="utf-8") as fh:
                results.append(json.load(fh))
        except (OSError, ValueError) as e:
            print("[WARN] 读取失败:", e)

    print("=" * 74)
    print("%-10s %-8s %-7s %-7s %-9s %-9s %-9s" %
          ("node_cap", "win_rate", "胜率%", "fallback", "wr_ms_med",
           "all_p99", "peakMB"))
    for r in results:
        print("%-10s %-8d %-7.2f %-7d %-9s %-9s %-9s"
              % (r["node_cap"], r["wr_n"], r["win_rate_pct"],
                 r["npc_n"], r["wr_med"], r["all_ms_p99"],
                 r["peak_commit_mb"]))
    print()
    print("各臂 reason:", json.dumps([r["reasons"] for r in results],
                                       ensure_ascii=False))


if __name__ == "__main__":
    main()
