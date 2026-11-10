# -*- coding: utf-8 -*-
"""量化 LAYER 2 成本估算器方向性错误的真实影响面，并评估拟采用的修法风险。

==================== 已确认的事实（2026-11-09）====================
失败盘面（game9 step138..142）的真实异常 = `lookahead_skip: hard cap`，
而不是内存/时间/节点闸（把 node_cap 提到 500 万仍 45ms 失败）。

根因：C(n9, rem) 是**单峰函数**（峰值在 rem ≈ n9/2），而 cpp/mscore.cpp
两处成本估算都用 `rem_max`（rem 的最大值）当"上界"：
  · LAYER 2 准入（:2601 `log2_total_est`）+ py_decide 诊断（:4243）
  · OPTIMAL PRE-GUARD（:2811 `choose(n9, rem_max)`）
当 rem_max 靠近 n9（本例 29/29）时 C(n9,rem_max)=1，而实际某行的
rem≈14 时 C(29,14)=77,558,760 ⇒ **低估 7.76e7 倍**。
⇒ 准入闸放行了根本算不动的盘面 ⇒ LAYER 2 物化时撞 HARD_CAP ⇒
  白烧 45ms（该局总耗时 0.4s 的 56%）再降级 pbs。

==================== 本脚本要回答的两个问题 ====================
Q1 影响面：真实对局中，有多大比例的决策「修正后的估算 > COST_CAP」？
   （= 当前被误导放进 LAYER 2、实际算不动的比例）
Q2 修法风险：若改用「解析上界」（world_upper × max_s C(n9,s)）作为准入，
   会不会**误杀**本来算得动的盘面？判据 = 修正上界 > cap 但
   reason 实际是 win_rate（= LAYER 2 成功）的比例。

若 Q2 计数≈0 ⇒ 直接用解析上界（O(1)、sound、无需枚举行）即可修好；
若 Q2 计数显著 ⇒ 需"上界放行 + 精确计数兜底"的混合闸。

⚠️ 口径：本脚本只复算**估算器**，不重算决策；reason 来自真实 decide。
   修正上界的公式须与 get_list_sizes 的钳制一致：
     rem ∈ [0, R]，R = min(a - num10, N)，N = clicks9 大小
     有效展开指数 s ∈ [1, N-1]（rem>N-1 会被钳到 N-1；rem==N 亦得 N-1）
     ⇒ max_s C(N, s) = C(N, min(R, N-1, floor(N/2)))

用法（py310）：
    <PY310_PYTHON> bench/quantify_cost_estimator.py --games 300
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

W, H, A, W2 = 30, 16, 99, 32
COST_CAP_LOG2_DEFAULT = 19.4


def lgamma_choose_log2(n, r):
    """log2 C(n,r)，用 lgamma 避免溢出；r 越界/非法返回 -inf。"""
    if r < 0 or r > n or n < 0:
        return float("-inf")
    return (math.lgamma(n + 1) - math.lgamma(r + 1)
            - math.lgamma(n - r + 1)) / math.log(2)


def fixed_cost_log2(world_upper, n9, rem_cap):
    """修正版：world_upper × max_{s ∈ [0, min(R,N-1)]} C(N,s) 的上界。

    与旧版对比：旧版直接 C(N, min(R, N))，在 R 接近 N 时严重低估。
    """
    if n9 <= 0:
        return math.log2(max(1, world_upper))
    N = n9
    R = max(0, min(rem_cap, N))
    s_hi = min(R, N - 1) if N > 1 else 0
    # C(N,s) 在 s<N/2 单调增 ⇒ 区间 [0,s_hi] 上的最大值在 min(s_hi, floor(N/2))
    s_star = min(s_hi, N // 2) if N > 1 else 0
    lb = lgamma_choose_log2(N, s_star)
    if lb == float("-inf"):
        lb = 0.0
    return math.log2(max(1, world_upper)) + max(0.0, lb)


def old_cost_log2(world_upper, n9, rem_cap):
    """旧版（当前 cpp 实现）：C(N, min(R,N))。"""
    if n9 <= 0:
        return math.log2(max(1, world_upper))
    rem = min(rem_cap, n9)
    return math.log2(max(1, world_upper)) + max(0.0, lgamma_choose_log2(n9, rem))


class Sim:
    def __init__(self, mines_xy1):
        import numpy as np
        self.mine = [[False] * W2 for _ in range(H + 2)]
        for (mx, my) in mines_xy1:
            self.mine[my][mx] = True
        self.cv = np.zeros((H + 2, W2), dtype=np.int32)
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                self.cv[y][x] = 9
        self.opened = 0
        self.dead = False
        self.steps = 0
        self.won = False
        self._flood(1, 1)

    def number(self, x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= W and 1 <= ny <= H and self.mine[ny][nx]:
                    c += 1
        return c

    def _flood(self, sx, sy):
        stack = [(sx, sy)]
        while stack:
            x, y = stack.pop()
            if self.cv[y][x] != 9 or self.mine[y][x]:
                continue
            n = self.number(x, y)
            self.cv[y][x] = n
            self.opened += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))

    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]:
            self.dead = True
            return
        self._flood(x, y)
        self.won = self.opened >= W * H - A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--cap", type=float, default=COST_CAP_LOG2_DEFAULT)
    args = ap.parse_args()

    import numpy as np
    from utils import native

    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    layouts = d["layouts"][: args.games]

    stats = {
        "decisions": 0,
        "old_over_cap": 0,          # 旧估算就超 cap（不会进 LAYER 2）
        "fixed_over_cap": 0,       # 修正估算超 cap
        "newly_rejected": 0,       # 修正后新增被拒（旧估算放行）
        "over_reject_win_rate": 0, # 修正后拒绝但实际 win_rate 成功 ⇒ 误杀
        "layer2_success": 0,
        "layer2_fail": 0,
        "underestimate_max": 0.0,  # 旧 vs 修正 差值的最大（log2）
        "times": {},
    }
    samples = []

    for i in range(args.games):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            sp = native.split_frontier(g.cv)
            n9 = len(sp[1])
            num10 = int(sp[3])
            r = native.mscore.decide(g.cv, a=A, w=W, h=H)
            reason = str(r["reason"])
            wu = int(r["world_upper"])
            old_c = float(r.get("cost_log2") or 0)
            fixed_c = fixed_cost_log2(wu, n9, A - num10)
            stats["decisions"] += 1
            stats["times"][reason] = stats["times"].get(reason, 0) + 1
            if reason == "win_rate":
                stats["layer2_success"] += 1
            if reason == "node_cap_fallback":
                stats["layer2_fail"] += 1
            if old_c > args.cap:
                stats["old_over_cap"] += 1
            if fixed_c > args.cap:
                stats["fixed_over_cap"] += 1
            if fixed_c > args.cap and old_c <= args.cap:
                stats["newly_rejected"] += 1
                if reason == "win_rate":
                    stats["over_reject_win_rate"] += 1
                if len(samples) < 20:
                    samples.append({"game": i, "step": g.steps, "reason": reason,
                                    "world_upper": wu, "n9": n9,
                                    "num10": num10, "rem_cap": A - num10,
                                    "old": round(old_c, 2),
                                    "fixed": round(fixed_c, 2)})
            delta = fixed_c - old_c
            if delta > stats["underestimate_max"]:
                stats["underestimate_max"] = delta
            # 走棋（与生产 decide 循环一致）
            if reason == "certain_safe":
                batch = [(int(c[0]), int(c[1])) for c in (r["tied"] or [])][:64]
                clicked = False
                for (bx, by) in batch:
                    if g.cv[by][bx] == 9:
                        g.click(bx, by)
                        clicked = True
                        if g.dead or g.won:
                            break
                if not clicked:
                    break
                continue
            x, y = int(r["x"]), int(r["y"])
            if x >= 1 and y >= 1:
                g.click(x, y)
            else:
                fb = None
                for yy in range(1, H + 1):
                    for xx in range(1, W + 1):
                        if g.cv[yy][xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                g.click(fb[0], fb[1])

    print("=" * 78)
    print("样本 %d 局 / %d 个决策，COST_CAP_LOG2 = %.2f" %
          (args.games, stats["decisions"], args.cap))
    print("reason 分布:", json.dumps(stats["times"], sort_keys=True,
                                    ensure_ascii=False))
    print()
    print("Q1 影响面（估算器低估 ⇒ 放行算不动的盘面）:")
    print("  旧估算 > cap（根本不会进 LAYER 2）: %d (%.2f%%)"
          % (stats["old_over_cap"],
             100 * stats["old_over_cap"] / max(1, stats["decisions"])))
    print("  修正估算 > cap:                     %d (%.2f%%)"
          % (stats["fixed_over_cap"],
             100 * stats["fixed_over_cap"] / max(1, stats["decisions"])))
    print("  修正后新增被拒（旧估算放行）:        %d (%.2f%%)  ← 应≈当前 45ms 空烧次数"
          % (stats["newly_rejected"],
             100 * stats["newly_rejected"] / max(1, stats["decisions"])))
    print("  旧→修正 最大低估（log2）:            %.2f（即低估 %.1e 倍）"
          % (stats["underestimate_max"], 2 ** stats["underestimate_max"]))
    print()
    print("Q2 修法风险（解析上界是否误杀本来算得动的盘面）:")
    print("  LAYER 2 成功(win_rate) 次数:         %d" % stats["layer2_success"])
    print("  LAYER 2 失败(node_cap_fallback):     %d" % stats["layer2_fail"])
    print("  修正后拒绝但实际 win_rate 成功(误杀): %d" % stats["over_reject_win_rate"])
    if stats["over_reject_win_rate"] == 0:
        print("  ⇒ 误杀 0 ⇒ 可直接用解析上界修准入闸（O(1)、无需枚举行）")
    else:
        print("  ⇒ 存在误杀 ⇒ 需混合闸（上界放行 + 精确计数兜底）")
    print()
    print("样例（修正后新增被拒的前 %d 条）:" % len(samples))
    for s in samples:
        print("  game=%-3d step=%-4d reason=%-18s wu=%-6d n9=%-3d rem_cap=%-3d "
              "old=%.2f fixed=%.2f"
              % (s["game"], s["step"], s["reason"], s["world_upper"], s["n9"],
                 s["rem_cap"], s["old"], s["fixed"]))


if __name__ == "__main__":
    main()
