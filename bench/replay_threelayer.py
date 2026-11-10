# -*- coding: utf-8 -*-
# Full replay harness: replicate play_one_mines decision loop EXACTLY
# (batch certain_safe cap-64 -> single decide -> first-unopened fallback),
# recording per-decision (reason, x, y, is_mine) using trial_decide's decide().
import json, sys, os
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
        self.opened = 0
        self.dead = False
        self.won = False
        self.steps = 0
        first = (W//2+1, H//2+1)
        assert not self.mine[first[1]][first[0]]
        self._flood(first)
        self._win_check()
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
            n = self.number(x, y)
            self.cell[y][x] = n
            self.opened += 1
            if n == 0:
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        if dx==0 and dy==0: continue
                        nx, ny = x+dx, y+dy
                        if 1<=nx<=W and 1<=ny<=H:
                            stack.append((nx, ny))
    def _win_check(self):
        self.won = self.opened >= W*H - A
    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]:
            self.dead = True
            return
        self._flood((x, y))
        self._win_check()
    def frame(self):
        return [row[:] for row in self.cell]

def replay(mines_xy1):
    g = Sim(mines_xy1)
    trace = []
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
                    trace.append(('certain_safe', bx, by, g.mine[by][bx], g.steps))
                    g.click(bx, by)
                    clicked_any = True
                    if g.dead or g.won: break
            if not clicked_any:
                # C++ driver: nb>0 with all already-open -> loop continue; guard
                trace.append(('certain_safe_empty', -1, -1, False, g.steps))
                break
            continue
        x, y = int(r['x']), int(r['y'])
        if x >= 1 and y >= 1:
            trace.append((reason, x, y, g.mine[y][x], g.steps))
            g.click(x, y)
        else:
            # fallback: first unopened
            fb = None
            for yy in range(1, H+1):
                for xx in range(1, W+1):
                    if g.cell[yy][xx] == 9: fb = (xx, yy); break
                if fb: break
            if not fb: break
            trace.append(('fallback_' + reason, fb[0], fb[1], g.mine[fb[1]][fb[0]], g.steps))
            g.click(fb[0], fb[1])
    return g, trace

d = json.load(open(os.path.join(ROOT, 'bench/data/_paired_150.json'), encoding='utf-8'))
layouts, pyr, cppr = d['layouts'], d['py'], d['cpp']

targets = [i for i in range(150) if not cppr[i]['won'] and cppr[i]['steps'] <= 2
           and (pyr[i]['won'] or pyr[i]['steps'] > 2)]
print('replaying %d target games + game 0 control' % len(targets))
mismatch = 0
for gi in targets + [0]:
    g, trace = replay(layouts[gi])
    exp = cppr[gi]
    agree = (g.won == exp['won']) and (g.dead == exp['dead']) and (g.steps == exp['steps'])
    tag = 'OK ' if agree else 'DIFF'
    if not agree: mismatch += 1
    print('%s game %3d replay(won=%s dead=%s steps=%d) vs cpp(won=%s dead=%s steps=%d)'
          % (tag, gi, g.won, g.dead, g.steps, exp['won'], exp['dead'], exp['steps']))
    if not g.won and g.steps <= 5:
        for t in trace[-3:]:
            print('    trace: reason=%s xy=(%d,%d) is_mine=%s step#=%d' % t)
print('replay mismatches:', mismatch)
