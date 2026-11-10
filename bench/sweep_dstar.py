# -*- coding: utf-8 -*-
import json, sys, os, time
from collections import Counter
ROOT = r'D:\python\projects\minesweeper_help'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_decide'))
os.chdir(ROOT)
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

results = {}
for dstar in ('400', '800', '1600'):
    # each DSTAR needs a fresh process (env read once per process)
    code = '''
import json, sys, os, time
from collections import Counter
ROOT = r'D:\\python\\projects\\minesweeper_help'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_decide'))
os.chdir(ROOT)
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
import mscore as td
reasons = Counter(); per = []
t0 = time.perf_counter()
for i in range(150):
    g = Sim(d['layouts'][i])
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
tot = sum(reasons.values())
print('%s|%d|%s' % (sum(per), round(time.perf_counter()-t0),
      json.dumps({k: [v, round(100*v/tot,1)] for k, v in reasons.most_common()})))
'''
    env = dict(os.environ); env['MSW_DSTAR'] = dstar
    import subprocess
    r = subprocess.run([r'D:\anaconda3\envs\py310\python.exe', '-c', code],
                       capture_output=True, text=True, env=env, timeout=1800)
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'ERR|0|{}'
    wins, el, reasons = line.split('|', 2)
    print('DSTAR=%s: wins %s/150, %ss, reasons %s' % (dstar, wins, el, reasons), flush=True)
    results[dstar] = {'wins': int(wins), 'reasons': json.loads(reasons)}
json.dump(results, open(os.path.join(ROOT, 'bench/data/dstar_sweep_150.json'), 'w', encoding='utf-8'))
print('saved dstar_sweep_150.json')
