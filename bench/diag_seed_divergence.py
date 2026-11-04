# -*- coding: utf-8 -*-
"""定位：同 seed 同棋盘生成代码，为什么历史与当前跑出不同局面？

=================== 已确认事实 ====================
  · seed 决定性检验通过：同进程/跨进程，棋盘哈希 30/30 可复现
  · 但对比历史 base_true.json（2026-11-02）与当前 evalh_200.json：
      opened 相同仅 91/200，steps 相同仅 50/200，胜负相同 156/200
  · seed=20261108：历史 23 步/381 格/胜，当前 1 步/1 格/败
  · `git diff b8f659d..HEAD -- sim/minesweeper_sim.py` **为空**
    ⇒ 棋盘生成代码没变

⇒ 差异只能来自：① 求解器代码变了 ② 首点坐标变了 ③ 驱动/台账变了
   ④ 状态文件（state/time_budget.json）不同导致 wr_threshold 不同

=================== 本脚本做什么 ====================
对手工构造的同一 seed，**直接比较三方**：
  A) Board 生成后的雷位哈希（不跑求解器）
  B) 首个决策点（n_opened==1 后）的 prob_map 指纹
  C) 首点坐标
并与历史记录里的 opened 对照，判断差异最早出现在哪一步。

用法: python bench/diag_seed_divergence.py --seed 20261108
"""
import argparse
import hashlib
import json
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
    ap.add_argument("--seed", type=int, default=20261108)
    args = ap.parse_args()

    print("=" * 96)
    print(f"定位 seed={args.seed} 的历史/当前分歧起点")
    print("=" * 96)

    # ---- A) 纯棋盘生成（不跑求解器）----
    import random
    sys.path.insert(0, ROOT)
    from sim.minesweeper_sim import Board

    rng = random.Random(args.seed)
    b = Board(W, H, A, rng)
    b.open(W // 2, H // 2)
    mines = sorted(b.mines)
    h = hashlib.sha256(",".join(f"{x},{y}" for x, y in mines).encode()).hexdigest()[:16]
    print(f"\n  [A] 只生成棋盘（不跑求解器）：")
    print(f"      首点 = ({W//2}, {H//2})  雷数 = {len(mines)}")
    print(f"      雷位哈希 = {h}")
    print(f"      首点已开格数 = {b.opened_count}")
    # 首点数字
    print(f"      首点数字 = {b._count(W//2, H//2)}")

    # ---- B) 完整跑一局，记录前 3 个决策的 prob_map 指纹 ----
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "diagdiv"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver

    marks = []
    orig_n51 = prob.ProbabilityMixin.number5_1
    orig_open = Board.open
    cur = {"board": None}

    def h_open(self, x, y):
        cur["board"] = self
        cur.setdefault("first", (x, y))
        return orig_open(self, x, y)

    def h_n51(self, cell_value):
        inner = cell_value[1:H + 1, 1:W + 1]
        n_op = int(np.sum((inner >= 0) & (inner <= 8)))
        out = orig_n51(self, cell_value)
        hm = getattr(self, "_last_heatmap", None) or {}
        pm = hm.get("prob") or {}
        if len(marks) < 3:
            key = ",".join(f"{k}:{round(v,6)}" for k, v in sorted(pm.items()))
            marks.append({
                "n_opened": n_op,
                "n_cand": len(pm),
                "pm_hash": hashlib.sha256(key.encode()).hexdigest()[:16],
                "best": hm.get("best"),
                "best_conf": hm.get("best_conf"),
                "total": hm.get("total"),
            })
        return out

    Board.open = h_open
    prob.ProbabilityMixin.number5_1 = h_n51
    try:
        r = ab.play_one(args.seed, W, H, A, "baseline", 80, 200000, None)
    finally:
        Board.open = orig_open
        prob.ProbabilityMixin.number5_1 = orig_n51

    b2 = cur.get("board")
    h2 = hashlib.sha256(",".join(f"{x},{y}" for x, y in sorted(b2.mines))
                        .encode()).hexdigest()[:16] if b2 else None
    print(f"\n  [B] 完整跑一局：")
    print(f"      首点实际 = {cur.get('first')}")
    print(f"      雷位哈希 = {h2}   {'✔ 与 [A] 相同' if h2 == h else '✘ 不同'}")
    print(f"      结果 won={r.get('won')} died={r.get('died')} "
          f"opened={r.get('opened')} steps={r.get('steps')}")
    print(f"      前 3 个决策点：")
    for m in marks:
        print(f"        n_opened={m['n_opened']:>3} 候选={m['n_cand']:>3} "
              f"pm_hash={m['pm_hash']} best={m['best']} "
              f"conf={m['best_conf']} total={m['total']}")

    # ---- C) 与历史记录对照 ----
    p = os.path.join(ROOT, "state", "eval_run", "base_true.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            hist = json.load(f)
        hr = {x["seed"]: x for x in hist["results"]}
        if args.seed in hr:
            x = hr[args.seed]
            print(f"\n  [C] 历史记录（base_true.json）：")
            print(f"      won={x['won']} died={x['died']} steps={x['steps']} "
                  f"opened={x['opened']} n_guess={x['n_guess']}")
            print(f"      log_survival={x.get('log_survival')}")
            recs = x.get("records") or []
            print(f"      决策记录数 = {len(recs)}")
            for rec in recs[:3]:
                print(f"        {rec}")

    # ---- D) 状态文件影响 ----
    tb = os.path.join(ROOT, "state", "time_budget.json")
    print(f"\n  [D] 状态文件影响：")
    if os.path.exists(tb):
        with open(tb, encoding="utf-8") as f:
            print(f"      state/time_budget.json = {f.read()}")
        print(f"      ⚠️ 该文件含 wr 阈值，会影响 number5_1 走哪条分支")
    else:
        print("      无 state/time_budget.json")

    print("\n" + "=" * 96)
    print("读法")
    print("=" * 96)
    print("  若 [A] 与 [B] 的雷位哈希相同 ⇒ 棋盘生成一致，差异来自求解器/状态。")
    print("  若历史与当前的 opened 差异极大（如 381 vs 1）⇒ 首个决策就分叉。")
    print("  `eval_harness` 的 install_stubs 会把 prob._TB_STATE_PATH 重定向到")
    print("  state_dir，因此**不同 state_dir 会读到不同的 wr 阈值** ——")
    print("  这是历史与当前读数不可比的直接嫌疑。")


if __name__ == "__main__":
    main()
