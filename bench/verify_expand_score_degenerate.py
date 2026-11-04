# -*- coding: utf-8 -*-
"""核查：ExactEngine.expand_score 在"安全率平局集"内是否恒定。

背景：上个分析里 `expand` 臂的 regret 与 `lowest`（取最小位索引）**完全一致**，
配对差 0/0。这有两种可能：
  A) expand_score 在该平局集内对所有格真的相同（那它是无信息打分，结论仍成立）
  B) 我的调用/缓存有 bug（那结论作废）

本脚本用**独立复算**判定（不依赖 expand_score 的返回值）：
  · 直接取 click_outcomes 的展开格数，手算 p_safe × E[新开格数]
  · 与 expand_score 返回值逐格比对
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arms_lookahead import ExactEngine, WKey       # noqa: E402


def bucket(lim):
    return "(8,20]" if lim <= 20 else "(20,60]" if lim <= 60 else "(60,inf]"


def main():
    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    # 只抽 world 数很小的几个点，保证能在秒级跑完
    import itertools
    import math

    def count_worlds(c):
        a, num10, U = c["a"], c["num10"], c["U"]
        tot = 0
        for idx in itertools.product(*[range(n) for n in c["ck"]]):
            mt = sum(sum(int(v) for v in c["groups"][gi][sel])
                     for gi, sel in enumerate(idx))
            rem = a - num10 - mt
            if rem == 0:
                tot += 1
            elif 0 < rem <= U:
                tot += math.comb(U, rem)
            if tot > 500:
                return tot
        return tot

    checked = 0
    for c in cases:
        nw = count_worlds(c)
        if nw > 400:
            continue
        try:
            eng = ExactEngine(c, node_cap=800000, time_cap=25,
                              world_cap=None)
        except Exception:                                # noqa: BLE001
            continue
        if eng.n_worlds == 0:
            continue
        wk = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        saf = {cd: eng.safety(cd, wk2) for cd in cands}
        smax = max(saf.values())
        tie = [cd for cd in cands if saf[cd] >= smax - 1e-12]
        if len(tie) < 2:
            continue

        checked += 1
        print(f"\n[{checked}] lim={c['limitation']} worlds={eng.n_worlds} "
              f"n_tie={len(tie)}")
        # 独立复算：p_safe × Σ wt·(新开格数)
        indep = {}
        for cd in tie:
            p_safe, outs = eng.click_outcomes(cd, wk2, op2)
            # 注意：click_outcomes 返回的 wt 已在 safe 内归一化（和 = 1）
            indep[cd] = p_safe * sum(wt * eng.pop(op3 & ~op2)
                                     for _sub, op3, wt in outs)
        # 引擎自带打分
        builtin = {cd: eng.expand_score(cd, wk2, op2) for cd in tie}
        diffs = [abs(indep[cd] - builtin[cd]) for cd in tie]
        print(f"    expand_score vs 独立复算：最大差 {max(diffs):.3e}")
        vals = sorted(set(round(builtin[cd], 9) for cd in tie))
        print(f"    expand_score 在平局集内的不同取值个数：{len(vals)}"
              f"（共 {len(tie)} 格）  取值={vals[:6]}"
              f"{' …' if len(vals) > 6 else ''}")
        picks_lowest = min(tie)
        picks_expand = max(tie, key=lambda cd: (builtin[cd], -cd))
        print(f"    lowest 选 idx={picks_lowest}  expand 选 idx={picks_expand}"
              f"  {'★相同' if picks_lowest == picks_expand else '不同'}")
        if len(vals) <= 1:
            print("    ⚠️ 该点 expand_score 恒定 ⇒ 破平局时退化到 lowest"
                  "（这是 expand 臂的表现，不是 bug，但说明它对该点无信息）")
        if checked >= 8:
            break

    print(f"\n共核查 {checked} 个有平局的决策点")


if __name__ == "__main__":
    main()
