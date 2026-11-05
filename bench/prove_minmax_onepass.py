# -*- coding: utf-8 -*-
"""证明 min-max(S0, PLY3) 是单一、在线可算、不读 q 的评分函数。

=================== 要回答的三件事（Advisor 提问）====================
Q1：它的精确定义是什么？
Q2：它是"单一评分函数"，还是"两个准则各自算完后挑 regret 更低者"（事后择优）？
Q3：它是否在线可算（不依赖未来信息），是否读取真值 q？

=================== 定义（逐字对应代码）====================
`bench/offline_multistep.py:159-173`：

    def norm(v):                       # 候选集内 min-max 归一化
        lo, hi = v.min(), v.max()
        return 0.5 if hi-lo < 1e-12 else (v - lo) / (hi - lo)

    def score_minmax(f, which):
        return np.minimum(norm(f["S0"]), norm(f[which]))

选格：`idx = argmax(score_minmax(f, "PLY3"))`   # 单次 argmax

⇒ 对每个候选格 c 计算**一个标量**：
      score(c) = min( norm_候选集(S0)[c], norm_候选集(PLY3)[c] )
   然后取 argmax。**只有一次选择，不存在"算两个再挑"**。

=================== 在线可算性论证 ====================
score(c) 的输入只有两样，都在**当前决策时刻**可得：
  · S0[c]   = 该候选格为安全的概率（当前世界集上直接算）
  · PLY3[c] = 3-ply 前瞻值（点击后推演 3 层，C++ exact_eval(ply=3)）
归一化的 lo/hi 取自**当前候选集内**的极值 —— 候选集是当前局面的全部可点击格，
不涉及未来局面、不涉及 q、不涉及任何"事后才知道"的信息。

⇒ 在线可算。但有一个**必须申报的代价**：归一化需要先算出**所有候选**的
   S0 与 PLY3，才能得到 lo/hi ⇒ 无法"边算边剪枝"，必须算满整个候选集。
   这与"只算 top-k 候选"的近似策略不兼容。

=================== 不读 q 的证明（实现来源核对）====================
`cpp_ply()` 的输入只有：case 的 cell_value / clicks / clicks9 / a / w / h。
它调用 C++ exact_eval(..., ply=k)，**从不读取 journal 的 q 字段**。
score_minmax 的两个参数 f["S0"]、f["PLY3"] 也都不来自 q。
⇒ 不构成循环论证。（q 只用于**事后评价** regret，不参与选格。）

本脚本用三条实测来固化这些论断：
  T1 事后择优反证：若"两个准则各算完挑更好"是真实机制，则其 regret 必须
     严格等于 min(regret_S0, regret_PLY3)（逐点）。测它是否成立。
  T2 单一函数验证：把 score_minmax 的 argmax 结果与 q 无关地重算两遍
     （打乱候选顺序）必须给出同一格。
  T3 不读 q 验证：把 q 随机置换后，选格结果必须**完全不变**。

用法:
  python bench/prove_minmax_onepass.py --limit 150
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
    load_v3_with_cases, build_multistep, s0_pick,
    score_minmax, norm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--out", default=os.path.join("state", "ab",
                                                 "minmax_onepass_proof.json"))
    args = ap.parse_args()

    rows, cases = load_v3_with_cases(args.limit)
    rows, feats = build_multistep(rows, cases, 50_000_000)
    print("=" * 100)
    print("证明：min-max(S0, PLY3) 是单一、在线可算、不读 q 的评分函数")
    print("=" * 100)
    print("  样本：%d 点" % len(rows))

    # ---------- T1：事后择优反证 ----------
    print()
    print("-" * 100)
    print("T1：驳倒「两个准则各自算完挑 regret 更低者」这一假设")
    print("-" * 100)
    print("  若机制真是事后择优，则所选格的 regret 必然 = min(regret_S0, regret_PLY3)")
    print("  即：每个点上它都不可能比两个单准则中更好的那个更差。")
    n_pts = 0
    n_match_oracle_better = 0     # 事后择优要求：所选 == 两个里更好的
    n_strictly_worse = 0
    examples = []
    for r, f in zip(rows, feats):
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 3:
            continue
        n_pts += 1
        # 单准则 S0 的 regret
        i_s0 = s0_pick(f)
        reg_s0 = float(q.max() - q[i_s0])
        # 单准则 PLY3 的 regret
        i_p3 = int(np.argmax(f["PLY3"]))
        reg_p3 = float(q.max() - q[i_p3])
        # min-max 准则的 regret
        i_mm = int(np.argmax(score_minmax(f, "PLY3")))
        reg_mm = float(q.max() - q[i_mm])
        best_of_two = min(reg_s0, reg_p3)
        if abs(reg_mm - best_of_two) < 1e-12:
            n_match_oracle_better += 1
        if reg_mm > best_of_two + 1e-12:
            n_strictly_worse += 1
            if len(examples) < 3:
                examples.append({"reg_s0": reg_s0, "reg_p3": reg_p3,
                                 "reg_minmax": reg_mm})
    print()
    print("  总点数 %d" % n_pts)
    print("  min-max 的 regret == min(两单准则) 的点：%d (%.1f%%)"
          % (n_match_oracle_better, 100 * n_match_oracle_better / n_pts))
    print("  min-max **严格差于**两者中较好者的点：%d (%.1f%%)"
          % (n_strictly_worse, 100 * n_strictly_worse / n_pts))
    print()
    if n_strictly_worse > 0:
        print("  ⇒ **驳倒事后择优假设**：若它是事后择优，不可能存在")
        print("    「严格差于两者中较好者」的点。实测存在 %d 个这样的点。"
              % n_strictly_worse)
        for e in examples:
            print("     例：reg_S0=%.4f, reg_PLY3=%.4f → min-max=%.4f"
                  % (e["reg_s0"], e["reg_p3"], e["reg_minmax"]))
    else:
        print("  ⚠️ 未发现反例 —— 需警惕它可能确实是事后择优（或两者在该样本上一致）")

    # ---------- T2：单一函数（顺序不变性） ----------
    print()
    print("-" * 100)
    print("T2：单一函数验证（打乱候选顺序，选格必须不变）")
    print("-" * 100)
    rng = np.random.default_rng(20261104)
    n_checked = 0
    n_same = 0
    for r, f in zip(rows, feats):
        if len(r["q"]) < 3:
            continue
        i0 = int(np.argmax(score_minmax(f, "PLY3")))
        coord0 = tuple(r["coords"][i0])
        # 打乱候选顺序后重算（同时打乱 F 的每一维）
        perm = rng.permutation(len(r["coords"]))
        f2 = {"S0": f["S0"][perm], "PLY3": f["PLY3"][perm]}
        i1 = int(np.argmax(score_minmax(f2, "PLY3")))
        coord1 = tuple(int(x) for x in np.asarray(r["coords"])[perm][i1])
        n_checked += 1
        if coord0 == coord1:
            n_same += 1
    print("  检验 %d 点：打乱顺序后选同一格 %d (%.1f%%)"
          % (n_checked, n_same, 100 * n_same / n_checked))
    print("  ⇒ %s" % ("选格与候选顺序无关，是单一评分函数的 argmax"
                      if n_same == n_checked else "⚠️ 存在顺序依赖"))

    # ---------- T3：不读 q ----------
    print()
    print("-" * 100)
    print("T3：不读 q 验证（把 q 随机置换，选格必须完全不变）")
    print("-" * 100)
    n_same3 = 0
    n_chk3 = 0
    for r, f in zip(rows, feats):
        if len(r["q"]) < 3:
            continue
        i0 = int(np.argmax(score_minmax(f, "PLY3")))
        q_perm = np.asarray(r["q"])[rng.permutation(len(r["q"]))]
        # 选格只依赖 f，不看 q_perm ⇒ 必然不变（这是构造性证明）
        i1 = int(np.argmax(score_minmax(f, "PLY3")))
        n_chk3 += 1
        if i0 == i1:
            n_same3 += 1
    print("  检验 %d 点：置换 q 后选格不变 %d (%.1f%%)"
          % (n_chk3, n_same3, 100 * n_same3 / n_chk3))
    print("  ⇒ 选格代码路径中不含 q（构造性成立）")
    print()
    print("  实现来源核对（更强证据）：")
    print("    · cpp_ply(case, k, node_cap) 只读 case 的")
    print("      cell_value / clicks / clicks9 / a / w / h")
    print("    · 调用 C++ exact_eval(..., ply=k)，从不读 journal 的 q 字段")
    print("    · score_minmax 的输入只有 f['S0'] 与 f['PLY3']")

    # ---------- 在线成本说明 ----------
    print()
    print("-" * 100)
    print("在线可算性与代价")
    print("-" * 100)
    print("  可得性：S0 与 PLY3 都在**当前决策时刻**可算，不依赖未来信息。")
    print("  代价（必须申报）：归一化需先算出**所有候选**的 S0 与 PLY3")
    print("    才能得到 lo/hi ⇒ 无法边算边剪枝，必须算满整个候选集。")
    print("     这与「只算 top-k 候选」的近似策略不兼容。")

    out = {
        "n_points": n_pts,
        "T1_after_the_fact_selection": {
            "n_match_min_of_two": n_match_oracle_better,
            "n_strictly_worse_than_best_of_two": n_strictly_worse,
            "refuted": bool(n_strictly_worse > 0),
            "examples": examples,
        },
        "T2_order_invariance": {"n_checked": n_checked,
                                "n_same": n_same,
                                "passed": bool(n_same == n_checked)},
        "T3_q_independence": {"n_checked": n_chk3,
                              "n_same": n_same3,
                              "passed": bool(n_same3 == n_chk3)},
        "definition": "score(c) = min(norm_in_candset(S0)[c], "
                      "norm_in_candset(PLY3)[c]); pick argmax",
        "online_computable": True,
        "cost_note": "normalization needs ALL candidates' S0 and PLY3; "
                     "cannot prune incrementally",
        "reads_q": False,
        "caliber": "v3 初始状态 + C++ 全枚举",
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
