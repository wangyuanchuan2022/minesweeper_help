# -*- coding: utf-8 -*-
"""对比历史 base_true.json 与当前 evalh_200.json 的逐局轨迹差异。

目的：判定 81/200 vs 71/200 的差异形态 —— 是"完全不同局面"还是
"同局面少量翻转"。这决定它是代码差异还是别的原因。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, "state", "eval_run", "base_true.json"),
              encoding="utf-8") as f:
        h = json.load(f)
    with open(os.path.join(ROOT, "state", "ab", "evalh_200.json"),
              encoding="utf-8") as f:
        n = json.load(f)

    hr = {x["seed"]: x for x in h["results"] if "error" not in x}
    nr = {x["seed"]: x for x in n["results"] if "error" not in x}
    common = sorted(set(hr) & set(nr))

    print("=" * 96)
    print(f"逐局轨迹对比（{len(common)} 个共同 seed）")
    print("=" * 96)

    same_op = sum(1 for s in common if hr[s]["opened"] == nr[s]["opened"])
    same_st = sum(1 for s in common if hr[s]["steps"] == nr[s]["steps"])
    same_win = sum(1 for s in common if hr[s]["won"] == nr[s]["won"])
    print(f"\n  opened 完全相同：{same_op}/{len(common)}")
    print(f"  steps  完全相同：{same_st}/{len(common)}")
    print(f"  胜负    相同   ：{same_win}/{len(common)}")

    if same_op / len(common) < 0.5:
        print("\n  ⇒ 大量局的 opened 都不同 ⇒ **两次跑的局面不同**。")
        print("     同 seed 但轨迹分叉 ⇒ 说明有进程级/时序相关的非确定因素，")
        print("     或两次代码/配置不同。需进一步定位。")
    else:
        print("\n  ⇒ 大部分局 opened 相同 ⇒ 同局面，只有少量决策分叉。")

    print("\n  差异样例（steps 或 opened 不同，前 10）：")
    print(f"  {'seed':>10}  {'历史 steps/opened/won':>26}  "
          f"{'当前 steps/opened/won':>26}")
    shown = 0
    for s in common:
        if hr[s]["steps"] == nr[s]["steps"] and hr[s]["opened"] == nr[s]["opened"]:
            continue
        a = f"{hr[s]['steps']}/{hr[s]['opened']}/{hr[s]['won']}"
        b = f"{nr[s]['steps']}/{nr[s]['opened']}/{nr[s]['won']}"
        print(f"  {s:>10}  {a:>26}  {b:>26}")
        shown += 1
        if shown >= 10:
            break

    # 关键：首局（seed=20261102）单独看
    s0 = common[0]
    print(f"\n  第一个 seed={s0} 的完整对比：")
    for k in ("won", "died", "steps", "opened", "n_guess", "ms"):
        if k in hr[s0] and k in nr[s0]:
            print(f"    {k:<10} 历史 {hr[s0][k]!r:<22} 当前 {nr[s0][k]!r}")


if __name__ == "__main__":
    main()
