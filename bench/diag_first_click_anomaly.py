# -*- coding: utf-8 -*-
"""定位：首个决策点 number5_1 抛出异常后，为什么会点到一个角落格？

=================== 已确认 ====================
seed=20261108，首点 (15,8) 只开 1 格（首格数字=3）。
`number5_1` 被调用 1 次，但：
  · `_last_heatmap` 从未被赋值（best/pm/total 全 None）
  · 实际点击 pending = [(0, 15)] —— 棋盘左下角
  · 该格是雷 ⇒ 立刻死亡
历史版本同 seed 能活 23 步。

⇒ 说明 `number5_1` 在**写入 _last_heatmap 之前**就异常退出了，
   而异常路径仍然发出了一个点击。

=================== 本脚本做什么 ====================
1. 捕获 `number5_1` 内部的真实异常（不吞）
2. 追踪 (0,15) 这个点击是从哪一行代码发出的（调用栈）
3. 判定是"随机兜底分支"还是"pos 未初始化"

用法: python bench/diag_first_click_anomaly.py --seed 20261108
"""
import argparse
import os
import sys
import traceback

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

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "fcanom"), True)
    import numpy as np
    import ab_policy as ab
    import utils.probability as prob
    from sim.eval_harness import SimDriver

    orig_n51 = prob.ProbabilityMixin.number5_1
    orig_flush = SimDriver.flush_clicks
    state = {"calls": 0, "exc": None, "tb": None}

    def h_n51(self, cell_value):
        state["calls"] += 1
        try:
            return orig_n51(self, cell_value)
        except Exception as e:                          # noqa: BLE001
            if state["exc"] is None:
                state["exc"] = {
                    "type": type(e).__name__,
                    "msg": str(e)[:300],
                    "tb": traceback.format_exc()[-2500:],
                }
            raise

    def h_flush(self):
        if self.pending and "pick" not in state:
            state["pick"] = list(self.pending)
            state["in_dec"] = bool(self.in_decision)
            # 记录调用栈，看点击从哪来
            state["stack"] = [
                f"{os.path.basename(f.filename)}:{f.lineno}@{f.name}"
                for f in traceback.extract_stack()[-8:-1]
            ]
        return orig_flush(self)

    prob.ProbabilityMixin.number5_1 = h_n51
    SimDriver.flush_clicks = h_flush
    try:
        r = ab.play_one(args.seed, W, H, A, "baseline", 80, 200000, None)
    finally:
        prob.ProbabilityMixin.number5_1 = orig_n51
        SimDriver.flush_clicks = orig_flush

    print("=" * 100)
    print(f"seed={args.seed}  结果 died={r.get('died')} opened={r.get('opened')}")
    print("=" * 100)
    print(f"\n  number5_1 调用次数 = {state['calls']}")
    print(f"  首次点击 pending = {state.get('pick')}  "
          f"in_decision = {state.get('in_dec')}")
    print(f"\n  点击调用栈（由外到内）：")
    for s in state.get("stack") or []:
        print(f"    {s}")

    if state["exc"]:
        print(f"\n  ★ number5_1 抛出的异常：")
        print(f"    {state['exc']['type']}: {state['exc']['msg']}")
        print(f"    栈尾：")
        for line in state["exc"]["tb"].strip().split("\n")[-18:]:
            print(f"      {line}")
    else:
        print("\n  number5_1 未抛异常 ⇒ 说明是内部走了某个分支但没写 _last_heatmap")

    # 判定
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    pick = state.get("pick")
    if pick:
        x, y = pick[0]
        edge = (x == 0 or x == W - 1 or y == 0 or y == H - 1)
        print(f"  首次点击 = {pick[0]}（0-based），贴边={edge}")
        print("  ⚠️ 这不是概率决策的结果：", end="")
        if state["exc"]:
            print("number5_1 抛异常后仍有点击发出。")
            print("     ⇒ 异常被 play_one 的 try/except 吞掉，但 pos 已入队/沿用，")
            print("        导致『无意义点击 → 大概率踩雷』。这是**真缺陷**。")
        else:
            print("number5_1 内部走了不写 _last_heatmap 的分支。")
            print("     ⇒ 同样说明该分支发出的点击没有概率依据。")


if __name__ == "__main__":
    main()
