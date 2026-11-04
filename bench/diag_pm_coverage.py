# -*- coding: utf-8 -*-
"""查清 V5：为什么 best 有时不在本次 pm 里（2/5）。

=================== 已确认 ====================
原子记录器 V1–V4 全过：id 唯一、点击独立于 flush、best==点击+1 100%。
但 V5 只有 3/5 —— best 有 2 次不在本次 pm 里。

=================== 三种可能 ====================
  H1 `pos` 来自**免费推理分支**（`if 1 in res: pos = 所有 res>=0.99 的格`），
     而该分支的 `_prob` 是 `{tuple(c): float(r) for c, r in zip(clicks, res)}`
     —— 它只覆盖 **clicks**，不覆盖 clicks9（内部格）。
     若 pos[0] 是内部格，就不在 pm 里。
  H2 `pos` 来自 `process_bigger_situation`（大情况分支），
     其 `_prob` 由 pbs 返回，可能只含部分候选。
  H3 随机分支（clicks 为空）时 `_prob = {}`。

⇒ 无论哪种，结论都是：**pm 不覆盖全部候选**，所以
   「用 pm 算后悔度」这条路**本身不成立**，不是记录器的错。

=================== 本脚本做什么 ====================
对每个决策事件，打印：best、best 是否在 pm、pm 大小、n_opened、
以及 best 是否落在内区/边界，判断属于哪个分支。

用法: python bench/diag_pm_coverage.py --games 30
"""
import argparse
import hashlib
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20267000)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "pmcov"), True)
    import ab_policy as ab
    from atomic_decision_events import DecisionRecorder

    not_in_pm = []
    in_pm = []
    for gi in range(args.games):
        rec = DecisionRecorder()
        rec.install()
        try:
            ab.play_one(args.seed + gi, W, H, A, "baseline", 80,
                        200000, 2000000)
        except Exception:                              # noqa: BLE001
            pass
        finally:
            rec.uninstall()

        for e in rec.events:
            for c in e["clicks"]:
                if not c["best"] or not c["pm"]:
                    continue
                key = f"{c['best'][0]},{c['best'][1]}"
                item = {
                    "seed": args.seed + gi, "id": e["decision_id"],
                    "n_opened": e["n_opened"], "n_unopened": e["n_unopened"],
                    "best": c["best"], "pm_size": len(c["pm"]),
                    "best_conf": c["best_conf"], "total": c["total"],
                    "pick": c["grid_0based"],
                }
                if key in c["pm"]:
                    in_pm.append(item)
                else:
                    not_in_pm.append(item)

    print("=" * 100)
    print(f"pm 覆盖度诊断（{args.games} 局）")
    print("=" * 100)
    print(f"\n  best 在 pm 内：{len(in_pm)}")
    print(f"  best 不在 pm ：{len(not_in_pm)}")
    tot = len(in_pm) + len(not_in_pm)
    if tot:
        print(f"  覆盖率 {len(in_pm)/tot*100:.2f}%")

    print(f"\n  【不在 pm 的样本】（前 12 条）")
    for r in not_in_pm[:12]:
        print(f"    seed={r['seed']} id={r['id']} n_op={r['n_opened']:>3} "
              f"n_unop={r['n_unopened']:>3} pm_size={r['pm_size']:>3} "
              f"best={r['best']} conf={r['best_conf']} total={r['total']}")

    # 对比：在 pm 的样本特征
    print(f"\n  【在 pm 的样本】（前 6 条，作对照）")
    for r in in_pm[:6]:
        print(f"    seed={r['seed']} id={r['id']} n_op={r['n_opened']:>3} "
              f"pm_size={r['pm_size']:>3} best={r['best']} "
              f"conf={r['best_conf']} total={r['total']}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    if not_in_pm:
        # 看 pm_size 是否恒等于 n_cand 之类
        sizes = sorted(set(r["pm_size"] for r in not_in_pm))
        confs = [r["best_conf"] for r in not_in_pm
                 if r["best_conf"] is not None]
        print(f"  不在 pm 的样本 pm_size 取值：{sizes}")
        if confs:
            print(f"  其 best_conf：min {min(confs):.4f} "
                  f"max {max(confs):.4f}")
            all_one = all(abs(c - 1.0) < 1e-9 for c in confs)
            print(f"  是否全为 1.0（确定安全格分支）：{all_one}")
            if all_one:
                print("  ⇒ 确认走的是『有确定安全格』分支：")
                print("     该分支 pos 来自 res>=0.99 的格，而 _prob 只由")
                print("     clicks/clicks9 组装 ⇒ 若 pos 含内部格则不在 pm。")
    print("\n  ⇒ 结论：**pm 不覆盖全部候选**，")
    print("     『用 pm 算后悔度』这条路不成立，与记录器无关。")
    print("     要测残局质量，必须换判据（见 review/残局测量方法问题.md）。")


if __name__ == "__main__":
    main()
