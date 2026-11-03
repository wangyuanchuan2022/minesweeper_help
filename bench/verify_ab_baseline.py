# -*- coding: utf-8 -*-
"""台架等价性验证：ab_policy 的 baseline 臂必须与未打补丁的生产流水线逐局一致。

=================== 为什么要这道闸 ====================
bench/ab_policy.py 用 monkey-patch 替换 win_rate / process_bigger_situation。
即使 baseline 臂"不打补丁"，它复刻的 play() 循环也可能与原评估台
（sim/eval_harness.py 的 play_one）有细微差异（步进顺序、flush 时机、
in_decision 标记等）。若不等价，则臂间差异可能来自台架而非算法。

判据：同一批 seed、同一棋盘，两边的 won/died/steps 必须逐局相同。
任一局不同即 FAIL，此时不得用 ab_policy 的整局胜率下结论。

用法: python bench/verify_ab_baseline.py --games 12
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs, play_one as ref_play
    from ab_policy import play_one as ab_play

    state = os.path.join(ROOT, "state", "ab", "equiv")
    install_stubs(state, True)

    print("=" * 84)
    print(f"台架等价性：ab_policy(baseline) vs sim/eval_harness（{args.games} 局）")
    print("判据：won / died / steps 逐局必须完全相同")
    print("=" * 84)

    diff = 0
    rows = []
    for i in range(args.games):
        sd = args.seed + i
        try:
            r = ref_play(sd, 30, 16, 99, limit=80, diag=False)
        except Exception as e:                          # noqa: BLE001
            print(f"[{sd}] 参考台错误 {type(e).__name__}: {e}")
            continue
        try:
            a = ab_play(sd, 30, 16, 99, "baseline", limit=80)
        except Exception as e:                          # noqa: BLE001
            print(f"[{sd}] ab 台错误 {type(e).__name__}: {e}")
            continue
        same = (r.get("won") == a.get("won")
                and r.get("died") == a.get("died")
                and r.get("steps") == a.get("steps"))
        if not same:
            diff += 1
        rows.append({"seed": sd, "same": same,
                     "ref": {"won": r.get("won"), "died": r.get("died"),
                             "steps": r.get("steps")},
                     "ab": {"won": a.get("won"), "died": a.get("died"),
                            "steps": a.get("steps")}})
        print(f"[{sd}] ref won={r.get('won')} died={r.get('died')} "
              f"steps={r.get('steps')} | ab won={a.get('won')} "
              f"died={a.get('died')} steps={a.get('steps')} "
              f"{'OK' if same else 'XX 不一致'}")

    print("-" * 84)
    print(f"对比 {len(rows)} 局，不一致 {diff} 局")
    if diff == 0 and rows:
        print("结论：台架等价 —— baseline 臂行为与生产评估台逐局一致，")
        print("      可用 ab_policy 做整局配对 A/B。")
    else:
        print("结论：台架不等价，整局 A/B 读数无效，必须先修台架。")

    out = os.path.join(state, "equiv.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "diff": diff}, f, ensure_ascii=False,
                  indent=1)
    print(f"结果已写 {out}")


if __name__ == "__main__":
    main()
