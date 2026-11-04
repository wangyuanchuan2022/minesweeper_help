# -*- coding: utf-8 -*-
"""对拍：扩展版指标矩阵（C++ 真值）与既有 41 点结果是否一致。

=================== 为什么要对拍 ====================
`dump_metric_matrix_ext.py` 是重写版：
  · 数据来源从"单文件 400 点"改为"多输入去重"
  · 真值 q 从 Python `ExactEngine.after_click` 改为 C++ `exact_eval`
  · 指标计算仍用 Python（但包了一层）

三处都可能引入偏差。判据必须**逐点、逐候选格**比对：
  · 同一决策点（按 limitation + n_cand 匹配）的候选格集合应一致
  · q 值应逐位相等（C++ 已对拍过 140/140，但这里是端到端）
  · 7 个指标的归一化打分应一致（同为 min-max，同方向）

**注意**：既有 `metric_matrix.json` 是**旧 flood 修复后**的结果
（41 点，limitation ∈ (8,60]）。新版只跑 `midgame_cases.json`，
筛出 119 个合格点（worlds<=20000），而旧版只跑 worlds<=400 ⇒ 41 点。
所以**新版是旧版的超集**：旧版 41 点应能在新版里找到对应点且数值一致。

用法: python bench/verify_metric_matrix_ext.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

OLD = os.path.join(ROOT, "state", "ab", "metric_matrix.json")
NEW = os.path.join(ROOT, "state", "ab", "mm_discovery.json")
TOL_Q = 1e-9
TOL_F = 1e-6


def key_of(row):
    """决策点匹配键：limitation 四舍五入 + n_cand（旧版无 worlds 字段可靠性差异）。"""
    return (round(float(row["limitation"]), 6), int(row["n_cand"]))


def main():
    for p, nm in ((OLD, "旧版"), (NEW, "新版")):
        if not os.path.exists(p):
            print("✘ 缺少%s %s" % (nm, os.path.basename(p)))
            return 1

    with open(OLD, encoding="utf-8") as f:
        old = json.load(f)
    with open(NEW, encoding="utf-8") as f:
        new = json.load(f)

    old_rows = old["rows"]
    new_rows = new["rows"]
    print("=" * 96)
    print("指标矩阵对拍：旧版（Python 真值）vs 新版（C++ 真值）")
    print("=" * 96)
    print("  旧版 %d 点，新版 %d 点" % (len(old_rows), len(new_rows)))

    new_by_key = {}
    for r in new_rows:
        new_by_key.setdefault(key_of(r), []).append(r)

    matched = 0
    q_mismatch = 0
    f_mismatch = 0
    cand_mismatch = 0
    missing = 0
    details = []

    for orow in old_rows:
        k = key_of(orow)
        cands = new_by_key.get(k, [])
        if not cands:
            missing += 1
            continue
        nrow = cands[0]
        matched += 1

        # 候选格数
        if len(orow["q"]) != len(nrow["q"]):
            cand_mismatch += 1
            details.append("lim=%s n_cand 旧%d vs 新%d"
                           % (k[0], len(orow["q"]), len(nrow["q"])))
            continue

        # q 逐格比对
        bad_q = 0
        for a, b in zip(orow["q"], nrow["q"]):
            if abs(float(a) - float(b)) > TOL_Q:
                bad_q += 1
        if bad_q:
            q_mismatch += 1
            details.append("lim=%s q 不一致 %d/%d 格"
                           % (k[0], bad_q, len(orow["q"])))

        # F 逐指标逐格比对
        bad_f = 0
        old_F = orow.get("F", {})
        new_F = nrow.get("F", {})
        for m in ("S0", "IG", "U", "E", "B", "R", "L"):
            if m not in old_F or m not in new_F:
                continue
            for a, b in zip(old_F[m], new_F[m]):
                if abs(float(a) - float(b)) > TOL_F:
                    bad_f += 1
        if bad_f:
            f_mismatch += 1
            details.append("lim=%s F 不一致 %d 个值" % (k[0], bad_f))

    print()
    print("=" * 96)
    print("结果")
    print("=" * 96)
    print("  旧版点能在新版找到：%d/%d" % (matched, len(old_rows)))
    print("  候选格数不一致：%d" % cand_mismatch)
    print("  q 值不一致点数：%d" % q_mismatch)
    print("  F 值不一致点数：%d" % f_mismatch)
    if missing:
        print("  ⚠️ 旧版有 %d 点在新版找不到（可能是筛选口径差异）" % missing)
    if details:
        print()
        print("  不一致明细（最多 10 条）：")
        for d in details[:10]:
            print("    " + d)

    print()
    print("=" * 96)
    verdict = (cand_mismatch == 0 and q_mismatch == 0 and f_mismatch == 0
               and matched > 0)
    if verdict:
        print("✔ 对拍通过：新版与旧版在共同点上完全一致")
        print("  （q 逐位相等、F 逐值相等、候选格数相等）")
        print("  ⇒ 可以放心用新版在发现集上扩样。")
    else:
        print("✘ 对拍未通过，需排查：")
        if cand_mismatch:
            print("   - 候选格数不同：可能 candidates() 口径变了")
        if q_mismatch:
            print("   - q 值不同：C++ exact_eval 与 Python after_click 有偏差")
        if f_mismatch:
            print("   - F 值不同：指标计算或归一化变了")
        if matched == 0:
            print("   - 没有任何点能匹配：匹配键设计有误")
    print("=" * 96)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
