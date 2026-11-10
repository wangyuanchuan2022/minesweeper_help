# -*- coding: utf-8 -*-
# Memory-guarded smoke test of the FIXED OPTIMAL layer.
# Self-guarding: a watchdog thread kills the process if RSS > 1.5GB.
# Run 5 games max; report reason mix + any slow calls.
import json, sys, os, time, threading
ROOT = r'D:\python\projects\minesweeper_help'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_decide'))
os.chdir(ROOT)

# watchdog: sample own RSS every 200ms, hard-exit at 1.5GB
def _watchdog():
    import ctypes, ctypes.wintypes as wt
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    psapi = ctypes.windll.psapi
    h = ctypes.windll.kernel32.GetCurrentProcess()
    while True:
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            ws = pmc.WorkingSetSize
            if ws > 1_500_000_000:
                print('WATCHDOG: RSS %.2f GB > 1.5GB - HARD EXIT' % (ws/1e9), flush=True)
                os._exit(9)
        time.sleep(0.2)
threading.Thread(target=_watchdog, daemon=True).start()

import mscore as td

W, H, A, W2 = 30, 16, 99, 32

class Sim:
    def __init__(self, mines_xy1):
        self.mine = [[False]*W2 for _ in range(H+2)]
        for (mx, my) in mines_xy1: self.mine[my][mx] = True
        self.cell = [[0]*W2 for _ in range(H+2)]
        for y in range(1, H+1):
            for x in range(1, W+1): self.cell[y][x] = 9
        self.opened = 0; self.dead = False; self.won = False; self.steps = 0
        self._flood((W//2+1, H//2+1)); self._win_check()
    def number(self, x, y):
        c = 0
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                if dx==0 and dy==0: continue
                nx, ny = x+dx, y+dy
                if 1<=nx<=W and 1<=ny<=H and self.mine[ny][nx]: c += 1
        return c
    def _flood(self, start):
        stack = [start]
        while stack:
            x, y = stack.pop()
            if self.cell[y][x] != 9 or self.mine[y][x]: continue
            n = self.number(x, y); self.cell[y][x] = n; self.opened += 1
            if n == 0:
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        if dx==0 and dy==0: continue
                        nx, ny = x+dx, y+dy
                        if 1<=nx<=W and 1<=ny<=H: stack.append((nx, ny))
    def _win_check(self):
        self.won = self.opened >= W*H - A
    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]: self.dead = True; return
        self._flood((x, y)); self._win_check()
    def frame(self):
        return [row[:] for row in self.cell]

d = json.load(open(os.path.join(ROOT, 'bench/data/_paired_150.json'), encoding='utf-8'))
layouts = d['layouts']

from collections import Counter
reasons = Counter(); slow = []
wins = 0
N = 5
t0 = time.perf_counter()
for gi in range(N):
    g = Sim(layouts[gi])
    while not g.dead and not g.won and g.steps < 3000:
        fr = g.frame()
        t1 = time.perf_counter()
        r = td.decide(fr, a=A, w=W, h=H)
        el = (time.perf_counter()-t1)*1000
        reason = str(r['reason'])
        reasons[reason.split(':')[0]] += 1
        if el > 300: slow.append((reason, int(r['world_upper']), round(el)))
        if reason == 'certain_safe':
            batch = [(int(c[0]), int(c[1])) for c in r['tied']][:64]
            clicked_any = False
            for (bx, by) in batch:
                if g.cell[by][bx] == 9:
                    g.click(bx, by); clicked_any = True
                    if g.dead or g.won: break
            if not clicked_any: break
            continue
        x, y = int(r['x']), int(r['y'])
        if x >= 1 and y >= 1: g.click(x, y)
        else:
            fb = None
            for yy in range(1, H+1):
                for xx in range(1, W+1):
                    if g.cell[yy][xx] == 9: fb = (xx, yy); break
                if fb: break
            if not fb: break
            g.click(fb[0], fb[1])
    wins += 1 if g.won else 0
    print('game %d: won=%s steps=%d  reasons so far %s' % (gi, g.won, g.steps, dict(reasons)), flush=True)
el = time.perf_counter()-t0
print('SMOKE OK: %d games %.0fs wins %d' % (N, el, wins))
print('reason mix:', dict(reasons.most_common()))
print('slow (>300ms) calls:', slow[:10])
