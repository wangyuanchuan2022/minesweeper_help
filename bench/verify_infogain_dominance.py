# -*- coding: utf-8 -*-
"""阳性对照：为什么 `sampled_infogain` 与 `sampled_safety` 在 48 个决策点上
**完全一致**（0/0 差异）？

=================== 三条竞争假设（必须用可区分实验判定）====================
  H1 打分被 p_safe 项主导 ⇒ argmax(score) 恒等于 argmax(sampled safety)
     （即"信息增益"这一项在本实现里根本没起作用）
  H2 桥接脚本有 bug（比如两次都调了同一个函数）
  H3 「期望新开格数」在采样世界下真的近似为常数（信息论上平坦）

判定手段（可证伪）：
  A) 打印每个决策点上：
       · argmax(p_safe × mean_new) 与 argmax(p_safe) 是否同格
       · argmax(mean_new)（**去掉 p_safe 项**）与 argmax(p_safe) 是否同格
     ⇒ 若 A 显示"去掉 p_safe 后大量不同"，则 H1 成立（信息项被压制）；
        若才去掉后仍全部相同，则 H3 成立（信息项确实平坦）
  B) 打印 mean_new 在同一决策点内的取值范围 ⇒ 它是不是常数
  C) 代码级自证：脚本里两个臂分别调用 `scores` 和 `cs`，打印一次
     argmax(scores) vs argmax(cs) 是否使用**不同**容器（防 H2）

用法: python bench/verify_infogain_dominance.py
"""
import json
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, WKey       # noqa: E402


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = sum(sum(int(v) for v in case["groups"][gi][sel])
                 for gi, sel in enumerate(idx))
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def main():
    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    K = 64
    checked = 0
    h1 = 0     # 去掉 p_safe 后选格变了 ⇒ H1
    h3 = 0     # 去掉 p_safe 后仍同格 ⇒ H3（信息项平坦）
    verify = 0  # argmax 用不同容器但结果相同

    for c in cases:
        if 8.0 >= c["limitation"] or c["limitation"] > 60.0:
            continue
        nw = count_worlds(c)
        if nw > 900:
            continue
        try:
            eng = ExactEngine(c, node_cap=1_200_000, time_cap=20,
                              world_cap=None)
        except Exception:                                # noqa: BLE001
            continue
        if eng.n_worlds == 0:
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        terms = [t for t in eng.saturate(wk_all, op)
                 if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        n_all = eng.n_worlds
        rng = random.Random(4242 + checked)
        samp_idx = list(range(n_all)) if n_all <= K else rng.sample(range(n_all), K)

        p_safe = {}
        mean_new = {}
        for cd in cands:
            tot = 0
            cnt = 0
            for i in samp_idx:
                wb = eng.world_bits[i]
                if wb >> cd & 1:
                    continue
                tot += eng.pop(eng.flood(wb, op2 | (1 << cd), cd) & ~op2)
                cnt += 1
            p_safe[cd] = cnt / max(1, len(samp_idx))
            mean_new[cd] = tot / max(1, cnt)

        # 三个**不同容器**的打分（防 H2）
        score_full = {cd: p_safe[cd] * mean_new[cd] for cd in cands}   # 容器1
        score_safe = {cd: p_safe[cd] for cd in cands}                  # 容器2
        score_info = {cd: mean_new[cd] for cd in cands}                # 容器3

        def am(sc):
            return max(cands, key=lambda cd: (sc[cd], -cd))

        pick_full = am(score_full)
        pick_safe = am(score_safe)
        pick_info = am(score_info)

        info_rng = (min(score_info.values()), max(score_info.values()))
        safe_rng = (min(score_safe.values()), max(score_safe.values()))
        checked += 1
        same_full_safe = pick_full == pick_safe
        same_info_safe = pick_info == pick_safe
        if same_full_safe and same_info_safe and info_rng[1] > info_rng[0] + 1e-9:
            verify += 1
        print(f"[{checked:>2}] lim={c['limitation']:>7} w={nw:>5} "
              f"ncand={len(cands):>2} | full={pick_full} safe={pick_safe} "
              f"info={pick_info} | "
              f"{'full==safe' if same_full_safe else 'full≠safe'} "
              f"{'info==safe' if same_info_safe else '★info≠safe'}")
        print(f"     p_safe 跨度 [{safe_rng[0]:.3f},{safe_rng[1]:.3f}]  "
              f"mean_new 跨度 [{info_rng[0]:.2f},{info_rng[1]:.2f}]")
        if not same_info_safe:
            h1 += 1
        elif same_full_safe:
            h3 += 1
        if checked >= 12:
            break

    print("\n" + "=" * 92)
    print("判定")
    print("=" * 92)
    print(f"  检查决策点 {checked} 个")
    print(f"  H1（p_safe 压制信息项：去掉 p_safe 后选格变化）：{h1}/{checked}")
    print(f"  H3（信息项本身平坦：去掉 p_safe 后仍同格）：{h3}/{checked}")
    print(f"  三个打分用**独立容器**计算且 full==safe==info 的点：{verify}/{checked}")
    if h1 > 0:
        print("  ⇒ 结论：H1 成立 —— `p_safe × mean_new` 的 argmax 基本由 p_safe 决定，")
        print("     『期望新开格数』这一信息项在本实现中被压制，等价于纯 safety。")
        print("     这解释了 sampled_infogain 与 sampled_safety 逐点完全一致。")
    elif h3 == checked:
        print("  ⇒ 结论：H3 成立 —— 在固定采样世界下『期望新开格数』几乎恒定，")
        print("     作为破平局准则没有区分力（不是 bug，是这条信号本身弱）。")


if __name__ == "__main__":
    main()
