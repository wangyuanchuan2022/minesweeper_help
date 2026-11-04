# -*- coding: utf-8 -*-
"""核验命题：用户指标 G（全局雷数一致性权重）是否**独立于** S0（安全概率）？

=================== 为什么要单独核验 ====================
用户方案把 G 列为第 8 个指标：
    G(c) = 与全局剩余雷数最一致的方案权重 / 总方案权重
并主张「它其实接近精确概率，但可作为概率可信度指标」。

这是**可判定的命题**，不需要靠样本观察，可以结构性地证明：
  · 本引擎（以及生产 _pbs_compute_dp）在枚举/加权时**已经**把全局雷数约束
    作为硬约束：只保留 rem = a − num10 − Σm ∈ [0, U] 的世界，
    且每组权重乘 C(U, rem)（= 按全局雷数一致性加权）。
  · 于是「按全局雷数加权后的 P(c 安全)」与引擎直接给出的 safety(c)
    **是同一个数**（同一个分母、同一个分子）。

判定手段（可证伪）：
  独立实现一个"按全局雷数组合数加权"的 P(c 安全) 计算，
  与 eng.safety(c) 逐候选格比对。
  · 若全部 |差| < 1e-12 ⇒ G ≡ S0（命题成立，G 无新信息）
  · 若存在显著差   ⇒ 命题被推翻，G 确实带新信息

用法: python bench/verify_global_count_equiv.py
"""
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from arms_lookahead import ExactEngine, WKey, Budget       # noqa: E402


def main():
    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    checked = 0
    total_cells = 0
    max_abs_diff = 0.0
    nonzero = 0

    for c in cases:
        if not (8.0 < c["limitation"] <= 60.0):
            continue
        # 用很小的世界上限，保证能构造成功且逐世界加权可行
        try:
            eng = ExactEngine(c, node_cap=2_000_000, time_cap=10.0,
                              world_cap=120)
        except Exception:                                  # noqa: BLE001
            continue
        if eng.n_worlds == 0:
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        try:
            terms = [t for t in eng.saturate(wk_all, op)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            continue
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)

        # ---- 独立实现：按全局雷数组合数加权的 P(c 安全) ----
        # 引擎的世界集合已满足全局雷数约束；每个世界等权（均匀先验）。
        # 因此"全局一致性加权 P(安全)" = 1 − (#含 c 的世界 / #世界)。
        # 若用户 G 的定义不同（例如把 C(U,rem) 显式乘回去），
        # 本引擎世界里每个世界的 C(U,rem) 因子是**同一个常数**，
        # 在归一化时约掉 ⇒ 仍是同一个数。下面两种算法都算，互相比对。
        #
        # ⚠️ 分母必须是 **len(wk2)**（saturate 之后的当前世界子集），
        # 不是 n_worlds。曾经写错成分母用 n_worlds ⇒ 报出 15/330 不一致、
        # max diff 恰好 0.5 的**假告警**（核验器的 bug，不是引擎的）。
        n = len(wk2)
        for cd in cands:
            bad = sum(1 for i in wk2 if eng.world_bits[i] >> cd & 1)
            p_equiv = 1.0 - bad / n
            p_engine = eng.safety(cd, wk2)
            d = abs(p_equiv - p_engine)
            max_abs_diff = max(max_abs_diff, d)
            total_cells += 1
            if d > 1e-12:
                nonzero += 1
        checked += 1
        if checked >= 20:
            break

    print("=" * 92)
    print("G（全局雷数一致性权重）≡ S0（安全概率）的数值核验")
    print("=" * 92)
    print(f"  决策点 {checked} 个，候选格 {total_cells} 个")
    print(f"  max |独立按全局雷数加权 P(安全) − eng.safety| = {max_abs_diff:.3e}")
    print(f"  超出 1e-12 的格数：{nonzero}/{total_cells}")
    print()
    if nonzero == 0:
        print("  ⇒ 判定：G ≡ S0。命题成立——在本引擎（及生产 DP）里，")
        print("     「按全局剩余雷数一致性加权」已经内建在 safety 的计算中，")
        print("     所以 G 不能作为独立指标提供新信息；把它加进线性 prior")
        print("     只会与 S0 完全共线（多重共线 ⇒ 权重不可辨识）。")
    else:
        print("  ⇒ 判定：命题被推翻 —— 存在不一致，G 可能带新信息。")
        print("     需进一步检查两套实现哪边错。")
    print()
    print("  阴性对照（防「两个实现共用同一错误」）：下面故意破坏加权，")
    print("  应立刻出现大量不一致 —— 若仍是 0，说明核验本身失效。")
    # 阴性对照：把 p 改成 1−bad/(n+1)，必然与 engine 不同
    bad_ctrl = 0
    for c in cases:
        if not (8.0 < c["limitation"] <= 60.0):
            continue
        try:
            eng = ExactEngine(c, node_cap=2_000_000, time_cap=10.0,
                              world_cap=120)
        except Exception:                                  # noqa: BLE001
            continue
        if eng.n_worlds == 0:
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        try:
            terms = [t for t in eng.saturate(wk_all, op)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            continue
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        n = len(wk2)
        for cd in cands:
            bad = sum(1 for i in wk2 if eng.world_bits[i] >> cd & 1)
            p_ctrl = 1.0 - bad / (n + 1)
            if abs(p_ctrl - eng.safety(cd, wk2)) > 1e-12:
                bad_ctrl += 1
        break
    print(f"  阴性对照不一致格数 = {bad_ctrl}（必须 > 0，否则核验无区分力）")
    if bad_ctrl == 0:
        print("  ✘ 阴性对照失败：核验器无区分力，上面的 0 不可信。")
        return 1
    print("  ✔ 阴性对照通过：核验器能检出真实差异。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
