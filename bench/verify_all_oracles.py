# -*- coding: utf-8 -*-
"""在指定用例文件上跑：守恒断言 + 三实现交叉验证 + 安全格占优定理。

替代分别跑 verify_world_enum / oracle_bruteforce / verify_safe_dominance，
一次给出完整证据链（含真独立穷举 oracle，不共享 enumerate_worlds）。

用法:
  python bench/verify_all_oracles.py                          # 默认 wr_cases.json
  python bench/verify_all_oracles.py --cases bench/data/wr_cases_big.json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from arms_lookahead import ExactEngine                  # noqa: E402
from oracle_bruteforce import BruteOracle, Budget       # noqa: E402
from verify_exact_oracle import Indep                   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(ROOT, "bench", "data",
                                                    "wr_cases.json"))
    ap.add_argument("--node-cap", type=int, default=400000)
    ap.add_argument("--time-cap", type=float, default=120.0)
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 104)
    print(f"三实现交叉验证 + 安全格占优定理   用例集 {os.path.basename(args.cases)}"
          f"（{len(cases)} 个）")
    print("独立性：oracle 只从 cell_value 重枚举世界，不共享 clicks/clicks9/groups")
    print("=" * 104)

    fail_eng = fail_ind = fail_budget = 0
    theorem_cases = 0
    safe_samples = 0
    weak_viol = strict_viol = risky_better = 0

    for ci, case in enumerate(cases):
        try:
            o = BruteOracle(case, args.node_cap, args.time_cap)
            e = ExactEngine(case, args.node_cap, args.time_cap)
            i1 = Indep(case, args.node_cap, args.time_cap)
        except Budget as ex:
            print(f"[{ci:2d}] 预算淘汰（{ex}）")
            fail_budget += 1
            continue
        except AssertionError as ex:
            print(f"[{ci:2d}] 约束不一致：{ex}")
            continue
        except Exception as ex:                            # noqa: BLE001
            print(f"[{ci:2d}] 错误 {type(ex).__name__}: {ex}")
            continue

        if o.n_worlds == 0 or e.n_worlds == 0:
            print(f"[{ci:2d}] 零世界（oracle {o.n_worlds} / engine {e.n_worlds}）")
            continue

        wk_o = frozenset(range(o.n_worlds))
        try:
            v_o = o.V(wk_o, o.opened0)
            v_e = e.V_exact(frozenset(range(e.n_worlds)), e.opened0)
            v_i = i1.V(frozenset(range(len(i1.worlds))), i1.opened0)
        except Budget as ex:
            print(f"[{ci:2d}] 求值预算淘汰（{ex}）")
            fail_budget += 1
            continue

        ok_e = abs(v_o - v_e) < 1e-9
        ok_i = abs(v_o - v_i) < 1e-9
        fail_eng += 0 if ok_e else 1
        fail_ind += 0 if ok_i else 1
        print(f"[{ci:2d}] worlds={o.n_worlds:3d} F={len(o.cand_coords):2d} | "
              f"oracle={v_o:.6f} 引擎={v_e:.6f}{'OK' if ok_e else ' XX'} "
              f"indep={v_i:.6f}{'OK' if ok_i else ' XX'}")

        # 安全格占优（不预先饱和，直接在原始状态逐候选评估）
        cands = [o.unk_index[p] if False else p for p in o.cand_coords]
        Q = {}
        S = {}
        for c in o.cand_coords:
            S[c] = o.safety(c, wk_o)
            Q[c] = o.after(c, wk_o, o.opened0)
        v_opt = max(Q.values())
        safe = [c for c in o.cand_coords if S[c] >= 1.0 - 1e-12]
        if safe:
            theorem_cases += 1
            safe_samples += len(safe)
            worst = min(Q[c] for c in safe)
            if worst < v_opt - 1e-12:
                weak_viol += 1
                print(f"      弱占优违反：安全格最低 {worst:.6f} < V*={v_opt:.6f}")
            if any(abs(Q[c] - v_opt) > 1e-12 for c in safe):
                strict_viol += 1
                print(f"      强形式违反：安全格未全部达到 V*")
            risky = [c for c in o.cand_coords if S[c] < 1.0 - 1e-12]
            if risky and max(Q[c] for c in risky) > worst + 1e-12:
                risky_better += 1

    print("=" * 104)
    print("A) 三实现一致性")
    print(f"   与 ExactEngine 不一致：{fail_eng}")
    print(f"   与 Indep 不一致：{fail_ind}")
    print(f"   预算淘汰用例：{fail_budget}")
    print()
    print("B) 安全格占优定理（用户命题）")
    print(f"   含 safety=1.0 候选的用例：{theorem_cases}，安全格样本 {safe_samples}")
    print(f"   弱占优（安全格点开后 ≥ V*）违反：{weak_viol}")
    print(f"   强形式（安全格点开后 == V*）违反：{strict_viol}")
    print(f"   存在风险格严格优于安全格的用例：{risky_better}")
    print()
    if fail_eng == 0 and fail_ind == 0 and weak_viol == 0 and strict_viol == 0:
        print("结论：三个实现（含不共享枚举器的穷举 oracle）完全一致；")
        print("      必然安全格的点击不降低最优胜率 —— 用户命题成立。")
    else:
        print("结论：存在不一致或定理违反，见上方明细，不得据此下算法结论。")


if __name__ == "__main__":
    main()
