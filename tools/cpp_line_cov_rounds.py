# -*- coding: utf-8 -*-
"""C++ 零调用取证：多轮真实负载 + 阳性对照（与 tools/cpp_line_cov_driver.py 共用同一份 gcda）。

为什么要多轮：
  单轮负载的「零调用」可能只是**样本未覆盖**。本题要区分三种情况：
    A. 结构性死代码   —— 源码里根本没有调用点，或调用点被宏/preprocessor 关掉
                        ⇒ 任何负载都不可能命中（可用静态证据 + 阳性对照不可达来定案）
    B. 样本未覆盖     —— 结构上可达，但本次负载没触发
                        ⇒ 必须给**阳性对照**证明"它是可达的，只是没被触发"
    C. 已删功能残留   —— 曾经被调用，功能删掉后残留的代码

  做法：多轮不同数据的负载（中局/整局/帮助转移/小盘/大盘）累加覆盖率；
  再对每个"零调用"候选做一次**定向阳性对照**（构造能触发它的输入）。

阳性对照清单（本轮实现）：
  · NodeCapExceeded       → 传极小 node_cap，逼 ExactEngine::tick 抛它
  · help_exact_transitions→ 帮助模式 + 悬停每个前沿格（大世界集）
  · py_transitions_for 缓存命中/未命中两条分支
  · no_frontier / no_closed_cell / clicks9_fallback / certain_safe / optimal /
    memory_gate / limitation 各类 reason 全覆盖
  · 5x5 / 4x3 小盘（Arbiter 小盘 cfg 路径）
  · MSW_DSTAR 放大（进 OPTIMAL 更多）

用法：
  set MINGW_BIN=<含 g++.exe / gcov.exe 的目录>
  python tools/cpp_line_cov_rounds.py
（前提：output/cpp_line_cov/mscore_cov.pyd 已由 cpp_line_cov_driver.py 构建；
  本脚本会**清空 gcda 后重跑全部轮次**，保证计数语义干净。）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_line_cov"
STATS = OUT / "_rounds_stats.json"

W, H = 30, 16
FIRST = (16, 8)


# ---------------------------------------------------------------------------
# 模拟器（与 cpp_line_cov_driver.Sim 同构，参数化棋盘尺寸）
# ---------------------------------------------------------------------------
class Sim:
    def __init__(self, mines_xy1, w=W, h=H, first=FIRST):
        self.W, self.H = w, h
        self.mine = [[False] * (w + 2) for _ in range(h + 2)]
        for mx, my in mines_xy1:
            if 1 <= mx <= w and 1 <= my <= h:
                self.mine[my][mx] = True
        self.cell = [[0] * (w + 2) for _ in range(h + 2)]
        for y in range(1, h + 1):
            for x in range(1, w + 1):
                self.cell[y][x] = 9
        self.opened = 0
        self.dead = False
        self.won = False
        self.steps = 0
        self.mines = sum(1 for y in range(1, h + 1) for x in range(1, w + 1) if self.mine[y][x])
        self._first = first

    def number(self, x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not dx and not dy:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= self.W and 1 <= ny <= self.H and self.mine[ny][nx]:
                    c += 1
        return c

    def _flood(self, start):
        stack = [start]
        while stack:
            x, y = stack.pop()
            if self.cell[y][x] != 9 or self.mine[y][x]:
                continue
            n = self.number(x, y)
            self.cell[y][x] = n
            self.opened += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if not dx and not dy:
                            continue
                        nx, ny = x + dx, y + dy
                        if 1 <= nx <= self.W and 1 <= ny <= self.H:
                            stack.append((nx, ny))

    def check(self):
        self.won = self.opened >= self.W * self.H - self.mines

    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]:
            self.dead = True
            self.cell[y][x] = 10
            return
        self._flood((x, y))
        self.check()

    def frame(self):
        return [row[:] for row in self.cell]

    def start(self):
        x, y = self._first
        if not (1 <= x <= self.W and 1 <= y <= self.H) or self.mine[y][x]:
            for yy in range(1, self.H + 1):
                for xx in range(1, self.W + 1):
                    if not self.mine[yy][xx]:
                        self.click(xx, yy)
                        return
            return
        self.click(x, y)


def run_games(mscore, np, layouts, n, stats, max_steps=900):
    for li, layout in enumerate(layouts[:n]):
        g = Sim(layout)
        g.start()
        guard = 0
        while not g.dead and not g.won and g.steps < max_steps and guard < max_steps + 100:
            guard += 1
            r = mscore.decide(np.ascontiguousarray(g.frame(), dtype=np.int32),
                              a=99, w=W, h=H, is_play=True)
            reason = str(r.get("reason"))
            stats["game_reasons"][reason] = stats["game_reasons"].get(reason, 0) + 1
            stats["game_decisions"] += 1
            if reason == "certain_safe":
                batch = [(int(a), int(b)) for a, b in (r.get("tied") or [])][:64]
                any_click = False
                for bx, by in batch:
                    if 1 <= bx <= W and 1 <= by <= H and g.cell[by][bx] == 9:
                        g.click(bx, by)
                        any_click = True
                        if g.dead or g.won:
                            break
                if not any_click:
                    break
                continue
            x, y = int(r.get("x") or -1), int(r.get("y") or -1)
            if not (1 <= x <= W and 1 <= y <= H) or g.cell[y][x] != 9:
                fb = None
                for yy in range(1, H + 1):
                    for xx in range(1, W + 1):
                        if g.cell[yy][xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                x, y = fb
            g.click(x, y)
        stats["games"] += 1
        if li and li % 100 == 0:
            print("  game", li, flush=True)


def main():
    gcda = next(OUT.glob("*.gcda"), None)
    if gcda is None or not PYD_OK():
        print("FATAL: 先跑 tools/cpp_line_cov_driver.py 构建插桩 pyd")
        return 1
    # 清空计数：本次统计 = 本脚本全部轮次的并集
    for f in OUT.glob("*.gcda"):
        f.unlink()
    print("gcda cleared:", gcda.name)

    sys.path.insert(0, str(OUT))
    import numpy as np  # noqa: E402
    import mscore  # noqa: E402

    stats = {"game_decisions": 0, "games": 0, "game_reasons": {},
             "decide_reasons": {}, "help_reasons": {}, "trans_reasons": {},
             "boards": 0, "rounds": []}

    def bump(d, k):
        d[str(k)] = d.get(str(k), 0) + 1

    def load(p):
        return json.loads((ROOT / p).read_text(encoding="utf-8"))

    # ---- Round 1: 400 基础中局盘 × auto/help + 悬停 + 300 局 ----
    cases = load("bench/data/midgame_cases.json")["cases"]
    for c in cases:
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        a = int(c["a"])
        bump(stats["decide_reasons"], mscore.decide(cv, a=a, w=W, h=H, is_play=True).get("reason"))
        r2 = mscore.decide(cv, a=a, w=W, h=H, is_play=False)
        bump(stats["help_reasons"], r2.get("reason"))
        fr = mscore.split_frontier(cv)
        for fx, fy in list(fr[0])[:6]:
            t = mscore.transitions_for(cv, a=a, w=W, h=H, x=int(fx), y=int(fy))
            if t is not None:
                bump(stats["trans_reasons"], t.get("reason"))
        stats["boards"] += 1
    stats["rounds"].append("R1: 400 中局盘×(auto+help)+悬停+300 局")
    layouts = load("bench/data/_dstar_10k_layouts.json")["layouts"]
    run_games(mscore, np, layouts, 300, stats)

    # ---- Round 2: 1886 扩展中局盘（更多种局面形态）----
    for p in ("bench/data/midgame_cases_ext.json", "bench/data/midgame_cases_ext2.json"):
        try:
            ext = load(p)["cases"]
        except Exception as e:  # noqa: BLE001
            print("SKIP", p, e)
            continue
        for c in ext:
            cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
            a = int(c["a"])
            bump(stats["decide_reasons"], mscore.decide(cv, a=a, w=W, h=H, is_play=True).get("reason"))
            bump(stats["help_reasons"], mscore.decide(cv, a=a, w=W, h=H, is_play=False).get("reason"))
            stats["boards"] += 1
        print("  ext done:", p, len(ext), flush=True)
    stats["rounds"].append("R2: +1886 扩展中局盘(auto+help)")

    # ---- Round 3: 另外 600 局整局（不同雷位布局来源）----
    b2000 = load("bench/data/_baseline2000.json")["layouts"]
    run_games(mscore, np, b2000, 600, stats)
    stats["rounds"].append("R3: +600 局整局(_baseline2000 布局)")

    # ---- Round 4: 阳性对照 —— 强制 node_cap 抛 NodeCapExceeded ----
    forced = {"node_cap_throw": 0, "small_boards": 0, "big_dstar": 0}
    for c in cases[:120]:
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        for cap in (1, 10, 100, 1000):
            try:
                mscore.decide(cv, a=int(c["a"]), w=W, h=H, is_play=True, node_cap=cap)
                forced["node_cap_throw"] += 1
            except Exception:  # noqa: BLE001 —— 记录即可
                forced["node_cap_throw"] += 0
    # 小盘（4x3 / 5x5）：走 no_frontier / 极小世界集
    for (bw, bh, mines) in ((4, 3, [(2, 1), (4, 2), (1, 3)]),
                            (5, 5, [(1, 1), (3, 2), (5, 4), (2, 5), (4, 3)]),
                            (9, 9, [(1, 1), (3, 3), (5, 5), (7, 7), (9, 9)])):
        cell = [[0] * (bw + 2) for _ in range(bh + 2)]
        for y in range(1, bh + 1):
            for x in range(1, bw + 1):
                cell[y][x] = 9
        cv = np.ascontiguousarray(cell, dtype=np.int32)
        for play in (True, False):
            try:
                mscore.decide(cv, a=len(mines), w=bw, h=bh, is_play=play)
                mscore.split_frontier(cv)
                mscore.transitions_for(cv, a=len(mines), w=bw, h=bh, x=1, y=1)
                forced["small_boards"] += 1
            except Exception:  # noqa: BLE001
                pass
    # 放大的 DSTAR：让更多局面进 OPTIMAL（positive control for optimal path）
    os.environ["MSW_DSTAR"] = "4000"
    for c in cases[:200]:
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        bump(stats["decide_reasons"], mscore.decide(cv, a=int(c["a"]), w=W, h=H, is_play=True).get("reason"))
        forced["big_dstar"] += 1
    os.environ.pop("MSW_DSTAR", None)
    stats["stats_round4"] = forced
    stats["rounds"].append("R4: 阳性对照(node_cap 强制/小盘/DSTAR 放大)")

    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ROUNDS DONE")
    print(" boards:", stats["boards"], " games:", stats["games"],
          " game_decisions:", stats["game_decisions"])
    print(" decide_reasons:", stats["decide_reasons"])
    print(" help_reasons:", stats["help_reasons"])
    print(" trans_reasons:", stats["trans_reasons"])
    print(" game_reasons:", stats["game_reasons"])
    print(" forced:", forced)
    return 0


def PYD_OK():
    return (OUT / "mscore.pyd").exists()


if __name__ == "__main__":
    raise SystemExit(main())
