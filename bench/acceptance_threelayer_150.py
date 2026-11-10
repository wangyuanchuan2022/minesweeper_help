# -*- coding: utf-8 -*-
# FULL 150-game acceptance run of the three-layer decide, memory-guarded.
import json, sys, os, time, threading, math
ROOT = r'D:\python\projects\minesweeper_help'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_decide'))
os.chdir(ROOT)

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
            if pmc.WorkingSetSize > 1_500_000_000:
                print('WATCHDOG: RSS > 1.5GB - HARD EXIT', flush=True)
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
layouts, cppr = d['layouts'], d['cpp']
det = [bool(c['won']) for c in cppr]

from collections import Counter
reasons = Counter(); slow = []
per = []
t0 = time.perf_counter()
for gi in range(150):
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
    per.append(bool(g.won))
    if (gi+1) % 25 == 0:
        print('%d/150 wins=%d (%.0fs) reasons=%s' % (gi+1, sum(per), time.perf_counter()-t0, dict(reasons.most_common())), flush=True)

el = time.perf_counter()-t0
wins = sum(per)
def mcnemar(a, b):
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n = n10 + n01
    if n == 0: return 1.0, n10, n01
    return min(1.0, 2*math.comb(n, min(n10, n01))/2**n), n10, n01
def wilson(k, n):
    z = 1.959963984540054
    p = k/n; dd = 1+z*z/n; cc = p+z*z/(2*n)
    hh = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (cc-hh)/dd, (cc+hh)/dd
lo, hi = wilson(wins, 150)
p, n10, n01 = mcnemar(per, det)
tot = sum(reasons.values())
print('\n=== THREE-LAYER ACCEPTANCE (150 games, %.0fs) ===' % el)
print('wins: %d/150 = %.1f%%  Wilson CI [%.1f, %.1f]' % (wins, 100*wins/150, 100*lo, 100*hi))
print('vs det arm (%d/150): only_new=%d only_det=%d  McNemar p=%.4f' % (sum(det), n10, n01, p))
print('reason mix:')
for k, v in reasons.most_common():
    print('  %-22s %6d (%.1f%%)' % (k, v, 100*v/tot))
print('slow calls (>300ms): %d  worst: %s' % (len(slow), max(slow, key=lambda t: t[2]) if slow else 'none'))
with open(os.path.join(ROOT, 'bench/data/threelayer_150.json'), 'w', encoding='utf-8') as f:
    json.dump({'per': per, 'reasons': dict(reasons), 'slow_calls': len(slow),
               'wins': wins, 'mcnemar_p_vs_det': round(p, 4)}, f)
print('saved bench/data/threelayer_150.json')
