# -*- coding: utf-8 -*-
"""抓取 LAYER 2 快速失败的**真实盘面**，供逐臂复现（不猜、不构造合成局面）。

背景（2026-11-09 实测）：
  默认生产参数下 decide 在 game9 step138 返回 reason=node_cap_fallback，
  但耗时只有 45ms，world_upper=576、cost_log2=9.17 —— 这个规模不该失败。
  node_cap 提到 500 万后仍然 45ms 失败 ⇒ 异常不是节点闸/时间闸。
  cpp/mscore.cpp:3508 的 `catch (const std::exception&)` 丢弃了 e.what()，
  属于本仓评审已标为 P1 的「静默降级」。因此本脚本先把**失败盘面存盘**，
  再在独立子进程里逐臂复现（C++ 常量是 magic static，必须分进程）。

产物：output/_layer2_fail_boards.json
用法：
    <PY310_PYTHON> bench/capture_layer2_fail.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32


def main():
    import numpy as np
    from utils import native

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

    out = []
    for i in range(10):
        g = Sim(layouts[i])
        rec = {"game": i, "layout": layouts[i], "frames": []}
        while not g.dead and not g.won and g.steps < 3000:
            r = native.mscore.decide(g.frame(), a=A, w=W, h=H)
            reason = str(r["reason"])
            if reason == "node_cap_fallback":
                rec["frames"].append({
                    "step": g.steps,
                    "frame": [[int(v) for v in row] for row in g.cv],
                    "world_upper": r.get("world_upper"),
                    "cost_log2": float(r.get("cost_log2") or 0),
                    "estimated_bytes": r.get("estimated_bytes"),
                    "limitation": r.get("limitation"),
                })
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
        if rec["frames"]:
            out.append(rec)

    path = os.path.join(ROOT, "output", "_layer2_fail_boards.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("捕获 %d 局、%d 个失败盘面 → %s"
          % (len(out), sum(len(r["frames"]) for r in out), path))
    for r in out:
        print("  game %d: %d 个失败点，world_upper=%s"
              % (r["game"], len(r["frames"]),
                 [f["world_upper"] for f in r["frames"]]))


if __name__ == "__main__":
    main()
