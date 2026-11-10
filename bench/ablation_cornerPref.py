# -*- coding: utf-8 -*-
# ABLATION D4: corner-preference tie-break (mrgris minesweepr-validated:
# expert 33.1% -> 37.8% with corners-then-edges preference, 100k games).
# Same 150 layouts, same three branches, single variable = tie-break rule:
# among tied cells prefer FEWEST neighbors (corner 3 < edge 5 < interior 8),
# then smallest (x,y). Purely deterministic, no oracle leakage.
import json, sys, os, time, random, math
ROOT = r'D:\python\projects\minesweeper_help'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_decide'))
os.chdir(ROOT)
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

def nbr_count(x, y):
    return sum(1 for dy in (-1,0,1) for dx in (-1,0,1)
               if not (dx==0 and dy==0)
               and 1<=x+dx<=W and 1<=y+dy<=H)

def corner_pick(tied):
    # prefer fewest neighbors (corner first), then smallest (x,y)
    best = min(tied, key=lambda c: (nbr_count(c[0], c[1]), c[0], c[1]))
    return best

def replay_arm(mines_xy1):
    g = Sim(mines_xy1)
    max_steps = 3000
    while not g.dead and not g.won and g.steps < max_steps:
        fr = g.frame()
        r = td.decide(fr, a=A, w=W, h=H)
        reason = str(r['reason'])
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
        if reason in ('limitation', 'clicks9_fallback', 'memory_gate') and len(r['tied']) > 1:
            tied = [(int(c[0]), int(c[1])) for c in r['tied']]
            x, y = corner_pick(tied)
        if x >= 1 and y >= 1:
            g.click(x, y)
        else:
            fb = None
            for yy in range(1, H+1):
                for xx in range(1, W+1):
                    if g.cell[yy][xx] == 9: fb = (xx, yy); break
                if fb: break
            if not fb: break
            g.click(fb[0], fb[1])
    return g

d = json.load(open(os.path.join(ROOT, 'bench/data/_paired_150.json'), encoding='utf-8'))
layouts, cppr, pyr = d['layouts'], d['cpp'], d['py']
det = [bool(c['won']) for c in cppr]; py = [bool(r['won']) for r in pyr]
ab = json.load(open(os.path.join(ROOT, 'bench/data/ablation_tiebreak_150.json'), encoding='utf-8'))
pureR = ab['pureR_per']
fu = json.load(open(os.path.join(ROOT, 'bench/data/ablation_filterU_150.json'), encoding='utf-8'))
filterU = fu['filterU_per']

t0 = time.perf_counter()
per = []
for i in range(150):
    g = replay_arm(layouts[i])
    per.append(bool(g.won))
    if (i+1) % 50 == 0: print('corner %d/150 wins=%d (%.0fs)' % (i+1, sum(per), time.perf_counter()-t0), flush=True)
print('cornerPref: %d/150 (%.0fs)' % (sum(per), time.perf_counter()-t0), flush=True)

def mcnemar(a, b):
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n = n10 + n01
    if n == 0: return 1.0, n10, n01
    k = min(n10, n01)
    return min(1.0, 2*math.comb(n, k)/2**n), n10, n01

def wilson(k, n):
    z = 1.959963984540054
    p = k/n; dd = 1+z*z/n; cc = p+z*z/(2*n)
    hh = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (cc-hh)/dd, (cc+hh)/dd

arms = {'det': det, 'pureR': pureR, 'filterU': filterU, 'cornerPref': per, 'py': py}
for name, arm in arms.items():
    w = sum(arm); lo, hi = wilson(w, 150)
    print('%-10s %d/150 = %.1f%% CI[%.1f,%.1f]' % (name, w, 100*w/150, 100*lo, 100*hi))
for b_name in ('det', 'pureR', 'filterU', 'py'):
    p, n10, n01 = mcnemar(per, arms[b_name])
    print('paired cornerPref vs %s: only_corner=%d only_%s=%d p=%.4f' % (b_name, n10, b_name, n01, p))

with open(os.path.join(ROOT, 'bench/data/ablation_cornerPref_150.json'), 'w', encoding='utf-8') as f:
    json.dump({'cornerPref_per': per}, f)
print('saved bench/data/ablation_cornerPref_150.json')
