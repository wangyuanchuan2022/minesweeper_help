# -*- coding: utf-8 -*-
"""专项判定：safety=1.0 的格是否必然最优（用户命题的直接检验）。

=================== 为什么要单独做 ====================
前一个实验（bench/arms_lookahead.py）在"免费推理饱和"之后比较各臂，
而饱和会把所有 safety=1.0 的格提前开掉 ⇒ 决策点上不再有安全格
（实测 11/11 用例的 safe_probe 都是 0）⇒ 根本测不到用户命题。

用户命题（严格版）：
    在没有点击次数限制、允许连续行动的模型里，必然安全格 c 的点击
    弱占优任何其它点击——先点 c 拿信息，再执行原策略，最优胜率不降低。
    因此应有  after(c) == V*(S)  对所有 safety(c)=1 的候选 c 成立
    （"≥" 是弱占优；"==" 是"点它不吃亏"的强形式）

=================== 本脚本怎么做 ====================
不先饱和！直接在**原始状态**（生产求解器当时的局面）上：
  · 列出全部候选格（前沿 + 内部）
  · 对每个候选算 safety 与 after(c) = 点它后用完美策略续玩的价值
  · 找出 safety==1.0 的格，检验 after(c) 是否等于 max_c after(c) = V*
  · 同时报告"安全格被高风险格排在前面"的反例（若存在）

这正是用户所说的"它免费给你提供信息，没有任何风险"的机检形式：
若 after(c) < V*，说明先点它反而变差——那用户命题就不成立（我们预计不会）。

用法: python bench/verify_safe_dominance.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from arms_lookahead import ExactEngine      # noqa: E402
from oracle_bruteforce import BruteOracle, Budget  # noqa: E402


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 100)
    print("判定：safety=1.0 的格点开后，最优胜率是否不降（用户命题）")
    print("口径：不预先饱和，直接在生产决策状态上逐候选评估 after(c)")
    print("=" * 100)

    n_cases_with_safe = 0
    n_safe_cells = 0
    n_weak_viol = 0
    n_strict_viol = 0
    n_better_than_risky = 0
    n_risky_strictly_better = 0
    rows = []

    for ci, case in enumerate(cases):
        try:
            eng = ExactEngine(case, node_cap=400000, time_cap=120)
            wk = frozenset(range(eng.n_worlds))
            op = eng.opened0
            cands = [eng._idx_of(xy) for xy in eng.cand_coords]
            cands = list(dict.fromkeys(cands))
            if not cands:
                continue
            Q = {c: eng.after_click(c, wk, op) for c in cands}
            S = {c: eng.safety(c, wk) for c in cands}
            v_opt = max(Q.values())
            safe = [c for c in cands if S[c] >= 1.0 - 1e-12]
            risky = [c for c in cands if S[c] < 1.0 - 1e-12]
            if not safe:
                print(f"[{ci:2d}] 无 safety=1.0 候选（跳过）")
                continue
            n_cases_with_safe += 1
            n_safe_cells += len(safe)
            worst_safe = min(Q[c] for c in safe)
            best_risky = max((Q[c] for c in risky), default=None)
            weak_ok = worst_safe >= v_opt - 1e-12
            strict_ok = all(abs(Q[c] - v_opt) <= 1e-12 for c in safe)
            if not weak_ok:
                n_weak_viol += 1
            if not strict_ok:
                n_strict_viol += 1
            if best_risky is not None:
                if min(Q[c] for c in safe) >= best_risky - 1e-12:
                    n_better_than_risky += 1
                elif best_risky > max(Q[c] for c in safe) + 1e-12:
                    n_risky_strictly_better += 1
            rows.append({"idx": ci, "n_safe": len(safe),
                         "worst_safe": round(worst_safe, 6),
                         "best_risky": None if best_risky is None
                         else round(best_risky, 6),
                         "v_opt": round(v_opt, 6),
                         "weak_ok": weak_ok, "strict_ok": strict_ok})
            print(f"[{ci:2d}] 安全格 {len(safe):2d}/{len(cands):2d} | "
                  f"V*={v_opt:.6f} 安全格最低={worst_safe:.6f} "
                  f"最佳风险格={('-' if best_risky is None else f'{best_risky:.6f}')} | "
                  f"弱占优 {'OK' if weak_ok else 'FAIL'} "
                  f"强形式 {'OK' if strict_ok else 'FAIL'}")
            if not weak_ok:
                bad = [(eng._coord(c), round(Q[c], 6), round(S[c], 4))
                       for c in safe if Q[c] < v_opt - 1e-12]
                print(f"      >>> 违反：{bad}")

        except Budget as e:
            print(f"[{ci:2d}] 预算淘汰：{e}")
        except Exception as e:                              # noqa: BLE001
            print(f"[{ci:2d}] 错误 {type(e).__name__}: {e}")

    print("=" * 100)
    print(f"含 safety=1.0 候选的用例：{n_cases_with_safe}，安全格样本 {n_safe_cells} 个")
    print(f"弱占优（after(c) ≥ V*）违反：{n_weak_viol} 个用例")
    print(f"强形式（after(c) == V*）违反：{n_strict_viol} 个用例")
    print(f"安全格不劣于最佳风险格：{n_better_than_risky}/{n_cases_with_safe} 个用例")
    print(f"存在风险格严格优于所有安全格：{n_risky_strictly_better} 个用例")
    print()
    if n_weak_viol == 0:
        print("结论：用户命题成立 —— 必然安全格的点击不降低最优胜率，")
        print("      任何把高风险格排在它前面的策略在这个模型里都是错误的。")
    else:
        print("结论：用户命题不成立（见上面反例）。")


if __name__ == "__main__":
    main()
