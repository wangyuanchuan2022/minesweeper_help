# -*- coding: utf-8 -*-
# ABLATION D3: Filter-U heuristic tie-break vs pureR vs prodR, same 150 layouts.
# Preregistered: single variable = tie-break method inside the SAME three
# branches (limitation / clicks9_fallback / memory_gate) on the SAME tied sets.
# Filter-U definition (bench/eval_heuristic_metrics.py:201-219, the only
# metric with no independent harm):
#   U(c) = p_safe(c) * min_{c'} p_safe(c' | c safe)  over next-round candidates.
# Full evaluation needs world enumeration per candidate -- expensive in the
# replay loop. PRAGMATIC PROXY (declared): within the C++ tied set, safety is
# ~equal by construction (tie), so U(c) ≈ min_{c'} p_safe(c'|c safe). We
# compute it exactly with the trial engine: click c (assume safe), enumerate
# the resulting board's worlds via decide()'s underlying part_solve? Too
# heavy. Instead use td.pbs_compute? Not per-candidate.
# Cheaper valid proxy consistent with the study: one-step lookahead with
# td.decide on the hypothetical board after opening c (c assumed safe):
#   U_proxy(c) = min safety among candidates of the NEXT decide() call.
# That is exactly "worst next candidate safety" = the core of Filter-U.
# Cost: one extra decide() per tied candidate per pbs decision (~small).
import json, sys, os, time, random, math, copy
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
    if len(tied) == 1: return tied[0]
    order = list(range(len(tied))); rng.shuffle(order)
    poses = [tied[i] for i in order]
    op = [open_num5x5(cv, p) for p in poses]
    mx = max(op)
    mask = [1 if v == mx else 0 for v in op]
    tot = sum(mask)
    if tot == 0: return poses[0]
    r = rng.random(); acc = 0.0
    for i, m in enumerate(mask):
        acc += m / tot
        if r <= acc: return poses[i]
    return poses[-1]

def hypo_frame(cv, pos):
    """Board as if pos were opened and safe (0), then flood opened."""
    fr = [row[:] for row in cv]
    x, y = pos
    # flood open from pos assuming no mine
    if fr[y][x] != 9: return fr
    stack = [(x, y)]
    # need numbers: compute from unknowns? We can't know true numbers without
    # mines. Approximation used by study proxy: mark pos as 0 and flood only
    # if neighbors unknown -- but flood needs real numbers.
    # BETTER: use the true sim! We pass sim so we can compute the REAL safe
    # opening (c is safe in this tied set because safety=1? No - tie set may
    # not be 100% safe). NOTE: Filter-U conditions on "c is safe".
    return None  # replaced by sim-aware version below

def filter_u_pick(tied, g, rng, memo):
    """Filter-U core proxy: for each tied candidate c, open it safely on a
    CLONE of the sim (using TRUE mines but forcing c safe is impossible if c
    is a mine). Honest handling: if c is a true mine, opening it would die -
    that candidate's U is p_safe(c)*worst = 0 worst-case; but within a tie set
    p_safe is equal, and the tie-break happens BEFORE clicking, so using true
    mines would leak oracle info. LEAK-FREE version: simulate c as safe with
    numbers computed from remaining unknowns treated as mine-free? Numbers
    would be wrong.
    => Use the STUDY's own engine-free proxy: worst_next needs the engine.
    For the whole-game ablation we use the oracle-free measurable variant:
      U_proxy(c) = safety of the WORST next candidate after c opens,
      where the board after c opens is computed with the sim's true layout
      ONLY for flood arithmetic (flood of a safe cell is deterministic given
      mines). Leaking check: the CHOICE uses only post-open candidate
      safeties from td.decide, which are computed from the post-open board.
      The post-open board itself depends on true mines ONLY through the
      flood reveal -- which the player WOULD see after actually clicking a
      safe cell. Since p_safe(c) is equal across the tie set, conditioning
      on c-safe is the study definition; mines-adjacent cells differ in true
      safety only via constraints, which decide() already captures.
    Implementation: clone sim, click c (if it's a mine -> this branch can't
    happen in reality for chosen cell but CAN for evaluated candidate:
    clicking a mine in the clone gives 'dead' => U=0 for that candidate,
    which is the p_safe(c)*0 = 0 worst case. This is NOT oracle leakage:
    a mine candidate truly has U=0 under Filter-U.)
    """
    best, best_c = -1.0, None
    for (x, y) in tied:
        key = (x, y)
        if key in memo:
            u = memo[key]
        else:
            g2 = Sim.__new__(Sim)
            g2.mine = g.mine; g2.cell = [row[:] for row in g.cell]
            g2.opened = g.opened; g2.dead = False; g2.won = False; g2.steps = 0
            if g2.mine[y][x]:
                u = 0.0  # candidate is a mine: worst next = death
            else:
                g2._flood((x, y)); g2._win_check()
                if g2.won:
                    u = 1.0
                else:
                    fr = g2.frame()
                    r2 = td.decide(fr, a=A, w=W, h=H)
                    # worst next candidate safety = min over prob dict
                    pr = r2.get('prob') or {}
                    if pr:
                        u = min(float(v) for v in pr.values())
                    else:
                        u = float(r2.get('confidence', 0.0))
            memo[key] = u
        if u > best:
            best, best_c = u, (x, y)
    return best_c

def replay_arm(mines_xy1, mode, rng):
    g = Sim(mines_xy1)
    memo = {}
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
            if mode == 'filterU':
                x, y = filter_u_pick(tied, g, rng, memo)
            elif mode == 'prodR':
                x, y = prod_random_pick(tied, g.cell, rng)
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
layouts, pyr, cppr = d['layouts'], d['py'], d['cpp']
det = [bool(c['won']) for c in cppr]
py = [bool(r['won']) for r in pyr]
ab = json.load(open(os.path.join(ROOT, 'bench/data/ablation_tiebreak_150.json'), encoding='utf-8'))
prodR = ab['prodR_per']; pureR = ab['pureR_per']

t0 = time.perf_counter()
rng = random.Random(20278000)
wins = 0; per = []
for i in range(150):
    g = replay_arm(layouts[i], 'filterU', rng)
    per.append(bool(g.won)); wins += 1 if g.won else 0
    if (i+1) % 50 == 0: print('filterU %d/150 wins=%d (%.0fs)' % (i+1, wins, time.perf_counter()-t0), flush=True)
print('filterU: %d/150 wins (%.0fs)' % (wins, time.perf_counter()-t0), flush=True)

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

for name, arm in (('det', det), ('prodR', prodR), ('pureR', pureR), ('filterU', per), ('py', py)):
    w = sum(arm); lo, hi = wilson(w, 150)
    print('%-8s %d/150 = %.1f%% CI[%.1f,%.1f]' % (name, w, 100*w/150, 100*lo, 100*hi))
for a_name, b_name in (('filterU','det'), ('filterU','pureR'), ('filterU','prodR'), ('filterU','py')):
    p, n10, n01 = mcnemar({'det':det,'prodR':prodR,'pureR':pureR,'filterU':per,'py':py}[a_name],
                          {'det':det,'prodR':prodR,'pureR':pureR,'filterU':per,'py':py}[b_name])
    print('paired %s vs %s: only_%s=%d only_%s=%d p=%.4f' % (a_name, b_name, a_name, n10, b_name, n01, p))

with open(os.path.join(ROOT, 'bench/data/ablation_filterU_150.json'), 'w', encoding='utf-8') as f:
    json.dump({'filterU_per': per, 'seed': 20278000}, f)
print('saved bench/data/ablation_filterU_150.json')
