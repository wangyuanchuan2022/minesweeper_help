# -*- coding: utf-8 -*-
# Phase 3: 3-layer arm + pureR/prodR (2 seeds each) on NEW layouts.
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

def open_num5x5(cv, pos):
    x, y = pos; n = 0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            nx, ny = x+dx, y+dy
            if nx < 1 or nx > W or ny < 1 or ny > H: n += 1; continue
            v = cv[ny][nx]
            if 0 <= v <= 8: n += 1
    return n

def prod_random_pick(tied, cv, rng):
    if len(tied) == 1: return tied[0]
    order = list(range(len(tied))); rng.shuffle(order)
    poses = [tied[i] for i in order]
    op = [open_num5x5(cv, p) for p in poses]
    mx = max(op); mask = [1 if v == mx else 0 for v in op]; tot = sum(mask)
    if tot == 0: return poses[0]
    r = rng.random(); acc = 0.0
    for i, m in enumerate(mask):
        acc += m / tot
        if r <= acc: return poses[i]
    return poses[-1]

def replay(mines, mode, rng):
    g = Sim(mines)
    while not g.dead and not g.won and g.steps < 3000:
        r = td.decide(g.frame(), a=A, w=W, h=H)
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
        # mode only affects the pbs tie-break branches (limitation/
        # clicks9_fallback/memory_gate) - the C++ layer itself already does
        # Filter-U; pureR/prodR override ONLY these branches for comparison.
        if mode in ('pureR', 'prodR') and reason in ('limitation', 'memory_gate') \
           and len(r['tied']) > 1:
            tied = [(int(c[0]), int(c[1])) for c in r['tied']]
            if mode == 'pureR': x, y = tied[rng.randrange(len(tied))]
            else: x, y = prod_random_pick(tied, g.cell, rng)
        if x >= 1 and y >= 1: g.click(x, y)
        else:
            fb = None
            for yy in range(1, H+1):
                for xx in range(1, W+1):
                    if g.cell[yy][xx] == 9: fb = (xx, yy); break
                if fb: break
            if not fb: break
            g.click(fb[0], fb[1])
    return g

d = json.load(open(os.path.join(ROOT, 'bench/data/_paired_new150.json'), encoding='utf-8'))
layouts = d['layouts']
from collections import Counter
results = {}
reasons = Counter()
# 3-layer as-is
t0 = time.perf_counter()
per = []
for i in range(150):
    g = Sim(layouts[i])
    while not g.dead and not g.won and g.steps < 3000:
        r = td.decide(g.frame(), a=A, w=W, h=H)
        reasons[str(r['reason']).split(':')[0]] += 1
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
results['new3layer'] = per
print('3-layer on NEW layouts: %d/150 (%.0fs)' % (sum(per), time.perf_counter()-t0), flush=True)
print('  reasons:', dict(reasons.most_common()), flush=True)
# pureR + prodR x2 seeds
for mode, seed in (('pureR', 20286000), ('prodR', 20287000), ('prodR', 20288000)):
    rng = random.Random(seed)
    per = []
    t0 = time.perf_counter()
    for i in range(150):
        g = replay(layouts[i], mode, rng)
        per.append(bool(g.won))
    key = mode if mode == 'pureR' else '%s_s%d' % (mode, seed)
    results[key] = per
    print('%s: %d/150 (%.0fs)' % (key, sum(per), time.perf_counter()-t0), flush=True)

d.update({k: v for k, v in results.items()})
json.dump(d, open(os.path.join(ROOT, 'bench/data/_paired_new150.json'), 'w', encoding='utf-8'))
print('saved _paired_new150.json')
