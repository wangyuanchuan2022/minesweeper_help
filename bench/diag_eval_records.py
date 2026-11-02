# -*- coding: utf-8 -*-
"""诊断 eval_harness 的单局决策记录：分支/概率表/选择保真度。

用法: python bench/diag_eval_records.py state/eval_run/smoke.json
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "state", "eval_run", "smoke.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    print(f"配置: {d.get('config')}")
    print(f"胜率 {d.get('wins')}/{d.get('n')}  CI {d.get('ci')}")
    print("=" * 84)
    for r in d["results"]:
        print(f"seed {r['seed']}  won={r['won']}  died={r['died']}  steps={r['steps']}"
              f"  guess={r['n_guess']} deep={r['n_deep']} greedy={r['n_greedy']}"
              f" unk={r['n_unknown']} anom={r['anomalies']}"
              f" min_max_safety={r.get('min_max_safety')}")
        recs = r.get("records") or []
        # 汇总分支分布与概率值分布
        branches = {}
        errs = {}
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            b = rec.get("branch")
            branches[b] = branches.get(b, 0) + 1
            if rec.get("err"):
                errs[rec["err"]] = errs.get(rec["err"], 0) + 1
        print(f"   分支分布: {branches}")
        if errs:
            print(f"   ⚠️ 异常: {errs}")
        # 打印可疑决策：max_safety 高但 chosen_safety 低，或 chosen_safety 极低
        bad = []
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            ms_, cs_ = rec.get("max_safety"), rec.get("chosen_safety")
            if ms_ is None or cs_ is None:
                continue
            if cs_ < 0.5 or (ms_ - cs_) > 1e-6:
                bad.append(rec)
        print(f"   低安全/非最优决策数: {len(bad)}")
        for rec in bad[:12]:
            print(f"     step={rec.get('step')} branch={rec.get('branch')}"
                  f" maxS={rec.get('max_safety')} chosenS={rec.get('chosen_safety')}"
                  f" ntab={rec.get('n_table')} total={rec.get('total')}"
                  f" move={rec.get('move')}")
        # 最后 3 步（含死亡那步）
        print("   最后 3 个决策:")
        for rec in [x for x in recs if isinstance(x, dict)][-3:]:
            print(f"     step={rec.get('step')} branch={rec.get('branch')}"
                  f" maxS={rec.get('max_safety')} chosenS={rec.get('chosen_safety')}"
                  f" ntab={rec.get('n_table')} total={rec.get('total')}"
                  f" move={rec.get('move')}")
        print()


if __name__ == "__main__":
    main()
