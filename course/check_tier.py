# -*- coding: utf-8 -*-
"""L3 档位核对：每模块翻译块/可视化/测验数量下限（m0 封面除外）。"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
SEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "segments")

for fname in sorted(os.listdir(SEG)):
    if not fname.endswith(".html"):
        continue
    t = io.open(os.path.join(SEG, fname), encoding="utf-8").read()
    mid = re.search(r'id="(m\d+)"', t).group(1)
    pairs = len(re.findall(r'class="translate-pair"', t))
    viz = (len(re.findall(r'data-kind="(?:steps|bars|timeline)"', t))
           + len(re.findall(r'class="[^"]*\b(?:onion-scene|tower-scene|fork-scene|flow-scene)\b[^"]*"', t)))
    quiz = len(re.findall(r'data-quiz="', t))
    bet = len(re.findall(r'data-bet-pair=', t))
    static_svg = len(re.findall(r'<svg\b', t))
    ok = (mid == "m0") or (pairs >= 3 and (viz + bet) >= 2 and quiz >= 2)
    print("%-14s pairs=%d viz=%d bet=%d quiz=%d svg=%d %s"
          % (fname, pairs, viz, bet, quiz, static_svg, "OK" if ok else "**FAIL**"))
