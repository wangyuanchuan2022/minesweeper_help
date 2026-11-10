# -*- coding: utf-8 -*-
# ABLATION A - preregistered (advisor nit):
#   Fixed input: 150 layouts from bench/data/_paired_150.json (same seeds).
#   Arm base: C++ decide via trial_decide (replay 150/150 verified).
#   Single variable: pbs tie-break ONLY, replacing deterministic (x,y)-min
#   with PRODUCTION-EQUIVALENT random: shuffle -> open_num5x5 weighting ->
#   np.random.choice (probability.py:1200-1217 exact semantics).
#   Arms:
#     det   = current C++ decide (replay results, already stored)
#     prodR = production-equivalent random tie-break (open_num5x5 weighted)
#     pureR = pure uniform random among tied (control, shows weighting effect)
#     py    = python production arm results from _paired_150.json
#   Metrics: wins per arm, McNemar exact p (paired), Wilson CI.
# IMPORTANT: pbs branch only fires on limitation>8 decisions (~320 rounds/150
#   games) and clicks9_fallback (~164) - tie-break changes only affect those.
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
        for (mx, my) in mines_xy1:
            self.mine[my][mx] = True
        self.cell = [[0]*W2 for _ in range(H+2)]
        for y in range(1, H+1):
            for x in range(1, W+1):
                self.cell[y][x] = 9
        self.opened = 0; self.dead = False; self.won = False; self.steps = 0
        first = (W//2+1, H//2+1)
        self._flood(first); self._win_check()
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
    """Production open_num5x5 exact semantics (probability.py:1555): 5x5 window
    +-2, out-of-board counts as opened, 0<=v<=8 counts as opened."""
    x, y = pos
    n = 0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            nx, ny = x+dx, y+dy
            if nx < 1 or nx > W or ny < 1 or ny > H:
                n += 1; continue
            v = cv[ny][nx]
            if 0 <= v <= 8: n += 1
    return n

def prod_random_pick(tied, cv, rng):
    """probability.py:1200-1217 exact: shuffle order (changes which cells are
    IN the tie list is NOT production - production builds tie list from
    shuffled index order but membership is order-independent), then
    open_num5x5 weighting, then np.random.choice among max-weight cells."""
    if len(tied) == 1: return tied[0]
    # production: _a = shuffle(indices); poses collects in shuffled order
    order = list(range(len(tied))); rng.shuffle(order)
    poses = [tied[i] for i in order if abs(0.0005 - 0.0) >= -0.0005]  # all tie members
    op = [open_num5x5(cv, p) for p in poses]
    mx = max(op)
    mask = [1 if v == mx else 0 for v in op]
    tot = sum(mask)
    if tot == 0: return poses[0]
    # np.random.choice with p=mask/tot
    r = rng.random()
    acc = 0.0
    for i, m in enumerate(mask):
        acc += m / tot
        if r <= acc: return poses[i]
    return poses[-1]

def replay_arm(mines_xy1, mode, rng):
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
            if mode == 'det':
                pass  # keep C++ chosen (x,y)-min
            elif mode == 'prodR':
                x, y = prod_random_pick(tied, g.cell, rng)
            elif mode == 'pureR':
                x, y = tied[rng.randrange(len(tied))]
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
layouts, pyr = d['layouts'], d['py']

t0 = time.perf_counter()
results = {}
for mode, seed in (('prodR', 20276000), ('pureR', 20277000)):
    rng = random.Random(seed)
    wins = 0; per = []
    for i in range(150):
        g = replay_arm(layouts[i], mode, rng)
        per.append(bool(g.won))
        wins += 1 if g.won else 0
    results[mode] = {'wins': wins, 'per': per, 'steps': None}
    print('%s: %d/150 wins  (%.0fs elapsed)' % (mode, wins, time.perf_counter()-t0), flush=True)

det = None  # reuse stored cpp results
import json as _j
det = [bool(c['won']) for c in d['cpp']]
py = [bool(r['won']) for r in pyr]

def mcnemar(a, b):
    n11 = sum(1 for x, y in zip(a, b) if x and y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n = n10 + n01
    if n == 0: return 1.0, n10, n01
    k = min(n10, n01)
    p = min(1.0, 2 * math.comb(n, k) / 2**n)
    return p, n10, n01

def wilson(k, n):
    z = 1.959963984540054
    p = k/n; dd = 1+z*z/n; cc = p + z*z/(2*n)
    hh = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (cc-hh)/dd, (cc+hh)/dd

out = {}
for name, arm in (('det', det), ('prodR', results['prodR']['per']),
                  ('pureR', results['pureR']['per']), ('py', py)):
    w = sum(arm); lo, hi = wilson(w, 150)
    out[name] = {'wins': w, 'ci': [round(lo,4), round(hi,4)]}
    print('%s: %d/150 = %.1f%%  CI[%.1f,%.1f]' % (name, w, 100*w/150, 100*lo, 100*hi))

for a_name, b_name in (('prodR','det'), ('pureR','det'), ('prodR','py'), ('det','py')):
    p, n10, n01 = mcnemar(out[a_name] and {'det':det,'prodR':results['prodR']['per'],'pureR':results['pureR']['per'],'py':py}[a_name],
                          {'det':det,'prodR':results['prodR']['per'],'pureR':results['pureR']['per'],'py':py}[b_name])
    print('paired %s vs %s: only_%s=%d only_%s=%d  McNemar p=%.4f'
          % (a_name, b_name, a_name, n10, b_name, n01, p))

with open(os.path.join(ROOT, 'bench/data/ablation_tiebreak_150.json'), 'w', encoding='utf-8') as f:
    _j.dump({'arms': out,
             'prodR_per': results['prodR']['per'],
             'pureR_per': results['pureR']['per'],
             'seed_prodR': 20276000, 'seed_pureR': 20277000}, f)
print('saved bench/data/ablation_tiebreak_150.json')
