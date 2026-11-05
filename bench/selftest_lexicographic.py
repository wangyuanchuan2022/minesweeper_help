# -*- coding: utf-8 -*-
"""B' 的仪器自测：证明 lexicographic 判定器有区分力（否则"全更差"不可信）。

=================== 为什么必须先做这一步 ====================
`offline_lexicographic.py` 初跑显示：所有 (ε, 增益准则) 组合都"更差"。
按本会话已反复验证的判别法则：

> 若**所有**条件都给同一方向的结果，先怀疑是代码/口径问题，
> 而不是相信"确实都没用"。

因此本脚本做三组已知答案的对照，证明判定器能分别产出
"更好 / 相同 / 更差"三种结果：

  对照 1（应"相同"）：把 gain 指标设成 S0 本身
        ⇒ 字典序第 1 步就唯一确定，应与 S0 基线完全一致
  对照 2（应"更好"）：直接用 q 当"增益指标"（作弊上限）
        ⇒ 必须在**每一点都选到最优**，regret ≡ 0
  对照 3（应"更差"）：用一个反指标（−q，即故意选最差）
        ⇒ regret 必须显著高于 S0
  对照 4（应介于其间）：随机选格（固定种子）
        ⇒ regret 应高于 S0 但低于对照 3

若四组全部符合预期 ⇒ 判定器有区分力，主脚本"全更差"是数据性质而非 bug。
若有任何一组不符 ⇒ 主脚本结论作废，回去修代码。

用法:
  python bench/selftest_lexicographic.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

import numpy as np                                        # noqa: E402
from offline_lexicographic import load_rows, s0_regrets    # noqa: E402


def pick_by_key(row, key_fn, eps=0.0):
    """通用：先 safety(>=max-eps) 过滤，再用 key_fn 取最大（确定性破并列）。"""
    s0 = np.asarray(row["F"]["S0"], dtype=np.float64)
    n = len(s0)
    max_s = float(s0.max())
    kept = [i for i in range(n) if s0[i] >= max_s - eps - 1e-12]
    vals = [(key_fn(row, i), s0[i], -i) for i in kept]
    return max(vals)[2] * -1


def eval_with_key(rows, key_fn, eps=0.0):
    regs = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        idx = pick_by_key(r, key_fn, eps)
        regs.append(float(q.max() - q[idx]))
    return regs


def main():
    rows, src = load_rows(os.path.join("state", "ab", "mm_v3.json"))
    print("=" * 100)
    print("B' 仪器自测：判定器是否具备区分力")
    print("=" * 100)
    print("  数据：%s（%d 点）" % (src, len(rows)))

    base = s0_regrets(rows)
    base_mean = sum(base) / len(base)
    print("  S0 基线 mean regret = %.6f（n=%d）" % (base_mean, len(base)))

    # 对照 1：gain = S0 本身（应等于基线）
    def key_s0(row, i):
        return row["F"]["S0"][i]

    r1 = eval_with_key(rows, key_s0, eps=0.0)
    m1 = sum(r1) / len(r1)

    # 对照 2：gain = q（作弊上限，应 regret≡0）
    def key_q(row, i):
        return row["q"][i]

    r2 = eval_with_key(rows, key_q, eps=0.0)
    m2 = sum(r2) / len(r2)

    # 对照 3：gain = -q（应显著更差）
    def key_negq(row, i):
        return -row["q"][i]

    r3 = eval_with_key(rows, key_negq, eps=0.0)
    m3 = sum(r3) / len(r3)

    # 对照 4：随机选格（固定种子，应介于两者之间）
    rng = np.random.default_rng(20261104)
    r4 = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        s0 = np.asarray(r["F"]["S0"], dtype=np.float64)
        kept = [i for i in range(len(s0)) if s0[i] >= s0.max() - 1e-12]
        idx = kept[int(rng.integers(len(kept)))]
        r4.append(float(q.max() - q[idx]))
    m4 = sum(r4) / len(r4)

    print()
    # ⚠️ 重要更正：对照 2 的预期**不是** 0。
    # 字典序第 1 步是 safety 过滤（eps=0 只保留最安全候选），
    # 若 q 最优格不是最安全格，则**在该约束下根本选不到它**。
    # 正确预期是「约束内上限」：safety 保留集内能拿到的最好 q。
    oracle_in_constraint = []
    for r in rows:
        q = np.asarray(r["q"], dtype=np.float64)
        if len(q) < 2 or (q.max() - q.min()) < 1e-12:
            continue
        s0 = np.asarray(r["F"]["S0"], dtype=np.float64)
        kept = [i for i in range(len(s0)) if s0[i] >= s0.max() - 1e-12]
        best_in_kept = max(q[i] for i in kept)
        oracle_in_constraint.append(float(q.max() - best_in_kept))
    m_oracle = sum(oracle_in_constraint) / len(oracle_in_constraint)

    print("  %-36s %14s %s" % ("对照", "mean regret", "预期"))
    print("  " + "-" * 76)
    print("  %-36s %14.6f %s" % ("S0 基线（参照）", base_mean, "—"))
    print("  %-36s %14.6f %s" % ("1. gain=S0（应与基线相同）", m1, "== 基线"))
    print("  %-36s %14.6f %s" % ("2a. 约束内上限（safety集内最优）", m_oracle,
                                  "字典序的理论下限"))
    print("  %-36s %14.6f %s" % ("2b. gain=q（实测）", m2, "== 2a"))
    print("  %-36s %14.6f %s" % ("3. gain=-q（故意选最差）", m3, "显著更差"))
    print("  %-36s %14.6f %s" % ("4. safety集内随机", m4, "介于其间"))

    print()
    ok1 = abs(m1 - base_mean) < 1e-9
    ok2 = abs(m2 - m_oracle) < 1e-9       # 对照 2 的正确判据
    ok3 = m3 > base_mean + 1e-6
    ok4 = (m4 > base_mean) and (m4 < m3)
    print("  判定：")
    print("    对照1 与基线相同              : %s" % ("✔" if ok1 else "✘"))
    print("    对照2 == 约束内上限            : %s" % ("✔" if ok2 else "✘"))
    print("    对照3 显著更差                : %s" % ("✔" if ok3 else "✘"))
    print("    对照4 介于其间                : %s" % ("✔" if ok4 else "✘"))
    allok = ok1 and ok2 and ok3 and ok4

    print()
    print("  与 S0 基线的对照（主脚本真正关心的量）：")
    print("    S0 基线         = %.6f" % base_mean)
    print("    约束内上限(2a)  = %.6f  （字典序**最好可能**只能到这里）" % m_oracle)
    print("    随机(4)         = %.6f" % m4)
    print()
    if m_oracle >= base_mean - 1e-9:
        print("  ⇒ **即使完美选择次级准则，也不可能优于 S0 基线。**")
        print("     这解释了主脚本『所有组合都更差』：不是准则选得不好，")
        print("     而是『先按 safety 过滤』这个**决策顺序本身锁死了上限**。")
        print()
        print("     数学原因：S0 基线取 argmax(safety)，并列时取下标最小；")
        print("     而 safety 过滤后保留集内的最优 q 值 ≤ 全局最优 q，")
        print("     且 S0 可能恰好选中了并列中最优的那个 —— 当约束内上限")
        print("     不优于基线时，任何次级准则都只能持平或变差。")
    else:
        print("  ⇒ 存在可改进空间（上限低于基线 %.6f），值得继续找准则。"
              % (base_mean - m_oracle))

    print()
    print("  ⇒ 判定器区分力：%s"
          % ("完整（能产出相同/上限/更差三档）" if allok
             else "**有问题，主脚本结论作废**"))
    return 0 if allok else 2


if __name__ == "__main__":
    sys.exit(main())
