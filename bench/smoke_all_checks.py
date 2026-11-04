# -*- coding: utf-8 -*-
"""冒烟测试：本轮所有 50% 目标相关脚本能否用最小参数跑通。

目的：把测试套件交给用户批量跑之前，先自证每个脚本"能跑、不报错、
有输出"。任何脚本失败都在这里暴露，而不是等用户跑长任务时才发现。

用法: python bench/smoke_all_checks.py [--include-long]
"""
import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable

# (id, 脚本, 最小参数, 预计秒数, 是否长任务)
CHECKS = [
    # ---- A 组：只读已有数据，秒级 ----
    ("A1", "bench/analyze_death_stage.py", [], 5, False),
    ("A2", "bench/identify_hist_rule.py", [], 5, False),
    ("A3", "bench/compare_hist_vs_now.py", [], 5, False),
    ("A4", "bench/verify_goal_feasibility.py", [], 10, False),
    ("A5", "bench/recompute_paired_ab.py",
     ["--raw", "state/ab/tiebreak_ab.json"], 10, False),
    ("A6", "bench/pool_paired_ab.py",
     ["--raws", "state/ab/tiebreak_ab.json", "state/ab/tiebreak_ab_rep2.json"],
     15, False),
    ("A7", "bench/analyze_ab_ci_and_redundancy.py", [], 60, False),

    # ---- B 组：自检/仪器验证，秒~分钟级 ----
    ("B1", "bench/verify_flood_chain.py", [], 30, False),
    ("B2", "bench/verify_probe_chain.py", [], 30, False),
    ("B3", "bench/verify_expand_score_degenerate.py", [], 90, False),
    ("B4", "bench/diag_seed_divergence.py", ["--seed", "20261108"], 30, False),
    ("B5", "bench/diag_first_click_anomaly.py", ["--seed", "20261108"], 30, False),
    ("B6", "bench/verify_endgame_safety.py", ["--seed", "20261127"], 60, False),

    # ---- C 组：小样本实跑（验证通路）----
    ("C1", "bench/verify_seed_determinism.py", ["--games", "6"], 180, False),
    ("C2", "bench/verify_workers_determinism.py", ["--games", "6"], 240, False),
    ("C3", "bench/analyze_first_move_quality.py", ["--games", "10"], 90, False),


    ("C5", "bench/endgame_prod_snapshot.py", ["--selftest"], 120, False),
    ("C6", "bench/atomic_decision_events.py", ["--selftest"], 120, False),
    ("C7", "bench/verify_atomic_commit.py", [], 60, False),
    ("C8", "bench/verify_exit_code_semantics.py", [], 60, False),
    ("C9", "bench/verify_rebuilt_script.py", [], 30, False),
    ("C10", "bench/measure_endgame_stratified.py", ["--games", "5"], 120, False),

    # ---- C11-C13：启发式指标评估链（2026-11-04 追加）----
    # ⚠️ 这三个脚本的正确性由各自的内部断言负责：
    #   · verify_global_count_equiv 自带阴性对照（必须检出 19 格不一致）
    #   · eval_heuristic_metrics 的平局集天花板自证（ceiling <= S0 regret）
    #   · eval_search_vs_heuristics 的 11 臂同台（S0 读数须与上面脚本一致）
    ("C11", "bench/verify_global_count_equiv.py", [], 120, False),
    ("C12", "bench/eval_heuristic_metrics.py",
     ["--max-worlds", "120", "--time-cap", "15",
      "--out", "state/ab/_smoke_hm.json"], 300, False),
    ("C13", "bench/eval_search_vs_heuristics.py",
     ["--max-worlds", "120", "--time-cap", "15",
      "--out", "state/ab/_smoke_svh.json"], 300, False),

    # ---- D 组：长任务（默认跳过，--include-long 才跑）----
    ("D1", "bench/rule_gap.py", ["--games", "20"], 600, True),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-long", action="store_true",
                    help="连 D 组长任务一起冒烟（会慢很多）")
    ap.add_argument("--only", default=None, help="只跑指定 id，如 A1,B2")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    results = []

    print("=" * 96)
    print("冒烟测试：50% 目标测试套件")
    print("=" * 96, flush=True)

    for cid, script, extra, est, is_long in CHECKS:
        if only and cid not in only:
            continue
        if is_long and not args.include_long:
            print(f"  [skip] {cid} {script}（长任务，默认跳过）")
            results.append((cid, script, "skip", 0, ""))
            continue
        if not os.path.exists(script):
            print(f"  [MISS] {cid} {script} —— 文件不存在")
            results.append((cid, script, "missing", 0, "文件不存在"))
            continue

        cmd = [PY, "-X", "utf8", script] + extra
        print(f"  [run ] {cid} {script} {' '.join(extra)}  "
              f"(预计 {est}s) …", flush=True)
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=max(est * 6, 300), encoding="utf-8",
                               errors="replace")
            dt = time.time() - t0
            if p.returncode == 0:
                # 检查是否有实质输出（不是只 print 一行就退）
                n_out = len((p.stdout or "").strip().split("\n"))
                st = "ok" if n_out >= 2 else "thin"
                print(f"  [{st:>4}] {cid} {dt:>5.1f}s  输出 {n_out} 行")
                results.append((cid, script, st, dt, ""))
            else:
                tail = (p.stderr or p.stdout or "")[-600:]
                print(f"  [FAIL] {cid} {dt:>5.1f}s  exit={p.returncode}")
                print(f"         {tail}")
                results.append((cid, script, "fail", dt, tail))
        except subprocess.TimeoutExpired:
            dt = time.time() - t0
            print(f"  [TIME] {cid} 超时 {dt:.0f}s")
            results.append((cid, script, "timeout", dt, "超时"))

    # ---- 汇总 ----
    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    n_ok = sum(1 for r in results if r[2] == "ok")
    n_thin = sum(1 for r in results if r[2] == "thin")
    n_bad = sum(1 for r in results if r[2] in ("fail", "timeout", "missing"))
    n_skip = sum(1 for r in results if r[2] == "skip")
    print(f"  通过 {n_ok}  输出偏少 {n_thin}  失败 {n_bad}  跳过 {n_skip}"
          f"  合计 {len(results)}")
    if n_bad or n_thin:
        print("\n  需处理：")
        for cid, script, st, dt, msg in results:
            if st in ("fail", "timeout", "missing", "thin"):
                print(f"    {cid} {script:<50} {st}")
    else:
        print("\n  ✅ 全部可运行")

    print("\n  ⚠️ 冒烟只证明「脚本能跑通」，不证明「读数正确」。")
    print("     正确性由各脚本内部的负向自测/阳性对照负责。")
    return 0 if (n_bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
