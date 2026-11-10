# -*- coding: utf-8 -*-
# Phase 2: C++ arms on the NEW layouts.
#  - det (old build): utils/trial_wholegame run_ab_mines (pre-3-layer)
#  - new (3-layer): trial_decide decide() via replay harness
#  - pureR + prodR seeds x2 (random-arm seed variance)
import json, sys, os, time, random, math
ROOT = r'D:\python\projects\minesweeper_help'
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'utils', 'trial_wholegame'))
import mscore as tw
d = json.load(open(os.path.join(ROOT, 'bench/data/_paired_new150.json'), encoding='utf-8'))
layouts = d['layouts']
out = tw.run_ab_mines(layouts, 30, 16, 99, "production", 200000, 3)
det = [{'won': bool(w), 'steps': st, 'dead': bool(dd)} for w, st, dd in out]
d['det'] = det
json.dump(d, open(os.path.join(ROOT, 'bench/data/_paired_new150.json'), 'w', encoding='utf-8'))
print('det (old build) on new layouts: %d/150' % sum(1 for r in det if r['won']))
