# -*- coding: utf-8 -*-
"""诊断：min-max 评分为何在打乱候选顺序后选格会变（25% 的点）。

=================== 现象（来自 prove_minmax_onepass.py T2）====================
打乱候选顺序后重算 argmax，148 点中只有 111 点（75%）选同一格，
**37 点（25%）选格改变**。

这与"单一评分函数的 argmax 应与候选顺序无关"矛盾，必须查清是
(a) 我的测试写错了（打乱方式不当），还是
(b) 评分函数本身含顺序依赖（例如并列时的 tie-break 依赖顺序）。

=================== 最可能的原因：并列（tie）====================
`np.argmax` 在并列时返回**第一个**最大值的下标。
min-max 评分 = min(norm(S0), norm(PLY3))，其中 norm 会把最小值映射为 0，
最大值映射为 1 ⇒ **极容易产生并列的 0**（当某候选在任一维上是最小值时，
min 就是 0）。

若多个候选的 min-max 分数并列（尤其是 0），argmax 取"第一个"⇒
打乱顺序就换了第一个 ⇒ 选格改变。

⇒ 这是**并列而非顺序依赖**：评分函数本身对顺序无依赖，
   但"打破并列的规则"是取下标最小者，而下标会随打乱变化。

=================== 本脚本要判定 ====================
把 min-max 分数向量打乱后，检查：
  · 若"最大值集合"（并列集）打乱前后是同一组坐标 ⇒ 顺序无关，只是 tie-break
  · 若最大值集合本身变化 ⇒ 评分函数真含顺序依赖（严重）
并量化：并列集大小的分布。

用法:
  python bench/diag_minmax_tie.py --limit 150
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from offline_multistep import (                            # noqa: E402
    load_v3_with_cases, build_multistep, score_minmax)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "minmax_tie_diag.json"))
    args = ap.parse_args()

    rows, cases = load_v3_with_cases(args.limit)
    rows, feats = build_multistep(rows, cases, 50_000_000)
    print("=" * 100)
    print("诊断：min-max 选格的顺序依赖是「并列 tie-break」还是「真顺序依赖」")
    print("=" * 100)
    print("  样本：%d 点" % len(rows))

    rng = np.random.default_rng(20261104)
    n = 0
    set_same = 0          # 并列集（最大值集合）坐标在打乱前后相同
    pick_same = 0
    tie_sizes = []
    detail = []
    for r, f in zip(rows, feats):
        if len(r["q"]) < 3:
            continue
        coords = [tuple(int(v) for v in x) for x in r["coords"]]
        s_mm = score_minmax(f, "PLY3")
        mx = float(s_mm.max())
        # 并列集（含浮点容差）
        tie_idx = [i for i in range(len(s_mm)) if abs(s_mm[i] - mx) < 1e-12]
        tie_coords = {coords[i] for i in tie_idx}
        tie_sizes.append(len(tie_idx))

        i0 = int(np.argmax(s_mm))
        pick0 = coords[i0]

        # 打乱后重算
        perm = rng.permutation(len(coords))
        f2 = {"S0": f["S0"][perm], "PLY3": f["PLY3"][perm]}
        s2 = score_minmax(f2, "PLY3")
        mx2 = float(s2.max())
        tie_idx2 = [i for i in range(len(s2)) if abs(s2[i] - mx2) < 1e-12]
        perm_coords = [coords[i] for i in perm]
        tie_coords2 = {perm_coords[i] for i in tie_idx2}
        pick1 = perm_coords[int(np.argmax(s2))]

        n += 1
        same_set = (tie_coords == tie_coords2)
        if same_set:
            set_same += 1
        if pick0 == pick1:
            pick_same += 1
        elif len(detail) < 5:
            detail.append({
                "tie_size": len(tie_idx),
                "tie_coords_before": sorted(tie_coords)[:6],
                "tie_coords_after": sorted(tie_coords2)[:6],
                "sets_identical": same_set,
                "pick_before": pick0, "pick_after": pick1,
            })

    from collections import Counter
    cs = Counter(tie_sizes)
    print()
    print("-" * 100)
    print("结果")
    print("-" * 100)
    print("  检验点数：%d" % n)
    print("  **并列集（最大值集合）打乱前后相同**：%d (%.1f%%)"
          % (set_same, 100 * set_same / n))
    print("  选格相同：%d (%.1f%%)" % (pick_same, 100 * pick_same / n))
    print()
    print("  并列集大小分布（前 10）：")
    for size, cnt in sorted(cs.items())[:10]:
        print("    并列 %d 个：%d 点" % (size, cnt))
    print()
    only_one = sum(v for k, v in cs.items() if k == 1)
    print("  并列集唯一（无并列，选格必然唯一）：%d/%d = %.1f%%"
          % (only_one, n, 100 * only_one / n))
    print("  存在并列（选格依赖 tie-break）：%d/%d = %.1f%%"
          % (n - only_one, n, 100 * (n - only_one) / n))

    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    if set_same == n:
        print("  ⇒ **评分函数本身对候选顺序无依赖**（并列集坐标始终相同）。")
        print("     25% 的选格变化来自 **并列时的 tie-break**（argmax 取第一个），")
        print("     不是评分函数含顺序依赖。")
        print()
        print("  ⇒ 对「单一评分函数」这个论断：**成立**。")
        print("     但必须申报：该准则在 %.0f%% 的点上存在并列，"
              % (100 * (n - only_one) / n))
        print("     需要额外的**确定性 tie-break 规则**（且该规则不应依赖候选顺序）。")
    else:
        print("  ✘ **评分函数存在真正的顺序依赖**（并列集坐标随顺序变化），")
        print("     需回查 norm() 的实现。")
        for d in detail:
            if not d["sets_identical"]:
                print("     反例：%s" % d)

    out = {
        "n_points": n,
        "tie_set_identical_after_shuffle": set_same,
        "pick_identical_after_shuffle": pick_same,
        "tie_size_distribution": {str(k): v for k, v in sorted(cs.items())},
        "n_unique_argmax": only_one,
        "n_with_tie": n - only_one,
        "conclusion": ("score function is order-independent; "
                       "selection variance comes from tie-break"
                       if set_same == n else "score function has order dependence"),
        "detail_examples": detail,
    }
    op = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(op), exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % op)
    return 0


if __name__ == "__main__":
    sys.exit(main())
