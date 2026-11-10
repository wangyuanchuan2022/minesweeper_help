# -*- coding: utf-8 -*-
"""C++ 零调用取证（内存安全版）：多轮真实负载 + 逐轮内存守卫。

⚠️ 为什么要单独写这一版（2026-11-10 真实事故）：
   上一版 tools/cpp_line_cov_rounds.py 的 Round 2 对 1886 个扩展中局盘**逐个跑
   help 模式** decide(is_play=False)。help 模式会给每个局面做
   help_exact_transitions，最多物化 HELP_TRANSITION_WORLD_CAP=200000 个完整世界，
   ExactEngine 的 memo/V_exact 还按世界子集继续扩张 ⇒ 峰值内存无上限，
   实机把内存撑爆。这违反用户铁律「内存 ≤1.5GB 硬上限」，是**探针**的错，不是
   产品代码的错（生产 is_play=True 走 DSTAR≤768 世界，前一轮 900 局/21559 次
   decide 全程正常）。

本版的安全设计（三道闸）：
   1. **help 模式限流**：只在前 HELP_CASES 个基础盘上跑（这些盘实测 100% 落
      memory_gate，不会真去物化 20 万世界）；扩展盘/整局一律只跑 auto 模式。
   2. **逐轮内存守卫**：每 20 个局面读一次当前进程工作集，
      超过 MEM_GUARD_MB 立刻停止该轮并记录（fail-closed，不硬闯）。
   3. **transitions_for 限流**：悬停转移只对前 TRANS_CASES 个盘的前 3 个前沿格。

用法：
  set MINGW_BIN=<含 g++.exe / gcov.exe 的目录>
  python tools/cpp_line_cov_rounds_safe.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_line_cov"
STATS = OUT / "_rounds_safe_stats.json"

W, H = 30, 16
FIRST = (16, 8)

HELP_CASES = 400      # help 模式（含 transitions）只跑这些
TRANS_CASES = 300     # 悬停转移只跑这些
MEM_GUARD_MB = 1200   # 进程工作集超此值即停（用户 1.5GB 硬上限下留 300MB 余量）


def _ws_mb():
    """当前进程工作集（MB）。Windows 下用 ctypes 读 GetProcessMemoryInfo。"""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        h = k32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return -1.0
        return pmc.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return -1.0


class MemGuard:
    """逐轮内存守卫：超限即停该轮（fail-closed，不硬闯）。"""

    def __init__(self, limit_mb):
        self.limit = limit_mb
        self.trips = []
        self.peak = 0.0

    def check(self, where):
        ws = _ws_mb()
        if ws > self.peak:
            self.peak = ws
        if ws > self.limit:
            self.trips.append({"where": where, "ws_mb": round(ws, 1)})
            print("  [MEM GUARD] %s: ws=%.0fMB > %.0fMB -> STOP this round"
                  % (where, ws, self.limit), flush=True)
            return False
        return True


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
        self.mines = sum(1 for y in range(1, h + 1)
                         for x in range(1, w + 1) if self.mine[y][x])
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

    def check_win(self):
        self.won = self.opened >= self.W * self.H - self.mines

    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]:
            self.dead = True
            self.cell[y][x] = 10
            return
        self._flood((x, y))
        self.check_win()

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


def main():
    if not (OUT / "mscore.pyd").exists():
        print("FATAL: 先跑 tools/cpp_line_cov_driver.py 构建插桩 pyd")
        return 1
    for f in OUT.glob("*.gcda"):
        f.unlink()
    print("gcda cleared")

    sys.path.insert(0, str(OUT))
    import numpy as np  # noqa: E402
    import mscore  # noqa: E402

    guard = MemGuard(MEM_GUARD_MB)
    stats = {"rounds": [], "decide_reasons": {}, "help_reasons": {},
             "trans_reasons": {}, "game_reasons": {},
             "boards": 0, "games": 0, "game_decisions": 0,
             "mem_guard_trips": guard.trips, "peak_ws_mb": 0.0}

    def bump(d, k):
        d[str(k)] = d.get(str(k), 0) + 1

    def load(p):
        return json.loads((ROOT / p).read_text(encoding="utf-8"))

    # ---------------- Round 1: 400 基础盘 auto + help + 悬停 ----------------
    cases = load("bench/data/midgame_cases.json")["cases"]
    stop = False
    for i, c in enumerate(cases):
        if i % 20 == 0 and not guard.check("R1/base"):
            stop = True
            break
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        a = int(c["a"])
        bump(stats["decide_reasons"], mscore.decide(cv, a=a, w=W, h=H, is_play=True).get("reason"))
        if i < HELP_CASES:
            bump(stats["help_reasons"], mscore.decide(cv, a=a, w=W, h=H, is_play=False).get("reason"))
        fr = mscore.split_frontier(cv)
        if i < TRANS_CASES:
            for fx, fy in list(fr[0])[:3]:
                t = mscore.transitions_for(cv, a=a, w=W, h=H, x=int(fx), y=int(fy))
                if t is not None:
                    bump(stats["trans_reasons"], t.get("reason"))
        stats["boards"] += 1
    stats["rounds"].append({"name": "R1 400 基础盘 auto/help/悬停", "stopped_by_guard": stop})

    # ---------------- Round 2: 扩展盘，**只跑 auto**（内存安全）----------------
    stop = False
    for p in ("bench/data/midgame_cases_ext.json", "bench/data/midgame_cases_ext2.json"):
        try:
            ext = load(p)["cases"]
        except Exception as e:  # noqa: BLE001
            print("SKIP", p, e)
            continue
        for i, c in enumerate(ext):
            if i % 20 == 0 and not guard.check("R2/ext-auto"):
                stop = True
                break
            cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
            bump(stats["decide_reasons"],
                 mscore.decide(cv, a=int(c["a"]), w=W, h=H, is_play=True).get("reason"))
            stats["boards"] += 1
        if stop:
            break
        print("  ext auto done:", p, len(ext), "ws=%.0fMB" % _ws_mb(), flush=True)
    stats["rounds"].append({"name": "R2 扩展盘 auto-only（不跑 help，防内存爆）",
                            "stopped_by_guard": stop})

    # ---------------- Round 3: 整局重放（600 局）----------------
    layouts = load("bench/data/_baseline2000.json")["layouts"]
    stop = False
    for li, layout in enumerate(layouts[:600]):
        if li % 20 == 0 and not guard.check("R3/games"):
            stop = True
            break
        g = Sim(layout)
        g.start()
        guard_n = 0
        while not g.dead and not g.won and g.steps < 800 and guard_n < 900:
            guard_n += 1
            r = mscore.decide(np.ascontiguousarray(g.frame(), dtype=np.int32),
                              a=99, w=W, h=H, is_play=True)
            reason = str(r.get("reason"))
            bump(stats["game_reasons"], reason)
            stats["game_decisions"] += 1
            if reason == "certain_safe":
                batch = [(int(p0), int(p1)) for p0, p1 in (r.get("tied") or [])][:64]
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
            print("  game", li, "ws=%.0fMB" % _ws_mb(), flush=True)
    stats["rounds"].append({"name": "R3 600 局整局重放", "stopped_by_guard": stop})

    # ---------------- Round 4: 阳性对照（小盘 / 强制 node_cap）----------------
    forced = {"small_boards": 0, "node_cap_forced": 0}
    for (bw, bh, mines) in ((4, 3, [(2, 1), (4, 2), (1, 3)]),
                            (5, 5, [(1, 1), (3, 2), (5, 4), (2, 5), (4, 3)]),
                            (9, 9, [(1, 1), (3, 3), (5, 5), (7, 7), (9, 9)]),
                            (1, 1, []), (2, 2, [(1, 1)])):
        cell = [[0] * (bw + 2) for _ in range(bh + 2)]
        for y in range(1, bh + 1):
            for x in range(1, bw + 1):
                cell[y][x] = 9
        cv = np.ascontiguousarray(cell, dtype=np.int32)
        for play in (True, False):
            try:
                mscore.decide(cv, a=len(mines), w=bw, h=bh, is_play=play)
                mscore.split_frontier(cv)
                forced["small_boards"] += 1
            except Exception:  # noqa: BLE001
                pass
    for c in cases[:60]:
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        for cap in (1, 50, 500):
            try:
                mscore.decide(cv, a=int(c["a"]), w=W, h=H, is_play=True, node_cap=cap)
                forced["node_cap_forced"] += 1
            except Exception:  # noqa: BLE001
                pass
    stats["forced"] = forced
    stats["rounds"].append({"name": "R4 阳性对照(小盘/强制 node_cap)", "stopped_by_guard": False})
    stats["peak_ws_mb"] = round(guard.peak, 1)
    stats["mem_guard_trips"] = guard.trips

    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SAFE ROUNDS DONE")
    print(" boards:", stats["boards"], "games:", stats["games"],
          "game_decisions:", stats["game_decisions"])
    print(" decide:", stats["decide_reasons"])
    print(" help:", stats["help_reasons"])
    print(" trans:", stats["trans_reasons"])
    print(" game:", stats["game_reasons"])
    print(" peak_ws_mb: %.0f  guard_trips: %d" % (guard.peak, len(guard.trips)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
