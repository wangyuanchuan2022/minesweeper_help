# -*- coding: utf-8 -*-
"""P2：角落开局 vs 中心开局 —— 整局配对 A/B。

=================== 证据链与必须守住的边界 ====================
**中间指标（已由精确组合数学证明，可复核）**：
Arbiter 是延迟布雷（只保证首点**本格**安全），首格数字 = 其真实邻域雷数。
邻域格数取决于位置：角落 3 格 / 边缘 5 格 / 中心 8 格。
用精确组合数 C(N-nb, a)/C(N, a)（N=479, a=99，扣除首点自身）：

| 位置 | P(首格数字 = 0) |
|---|---:|
| 角落 | **49.85%** |
| 边缘 | 31.25% |
| 中心（生产当前硬编码） | **15.45%** |

**外部参照**：Couëtoux 博士论文转述文献 [28] 的首点启发式，第一条即
"the first move needs to be in one of the **corners**"。

**⚠️ 这三条都**不能**推出"角落开局胜率更高"**：
  · 首格为 0 的概率 ≠ 胜率。零连锁展开通常有利，但它是否转化为整局胜率
    必须由固定种子配对 A/B 检验；存在反向可能（角落开出 1~3 时约束结构
    与中心不同，后续猜解难度未必更优）。
  · 文献的首步启发式是**经验法则**，本身未给出首点位置的配对实验数据。
  · 因此本脚本的定位是：**把"角落开局"从"有理由的候选"变成"有证据的结论
    或明确的否定"**，而不是去验证一个已知结论。

=================== 判据（两个指标必须分开报）====================
1. **首格数字为 0 的比率**（中间指标）——验证机制是否如预期生效。
   若实测角落的 0 率没有显著高于中心，说明组合学推导与实现不符，
   后续胜率对比也就没有意义（先查仪器）。
2. **整局胜率**（最终判据）——固定 seed 配对 + McNemar + 95%CI + MDE。

=================== 为什么必须固定 seed 配对 ====================
两种开局在同一 seed 下生成的是**不同棋盘**（延迟布雷在首点之后才生成，
首点不同 ⇒ 布雷池不同）。所以这**不是**"同一棋盘上选不同格"的配对，
而是"两种策略各自跑同一 seed 序列"的配对。seed 作区组，消除棋盘运气差异。
**能回答**：一直点角落 vs 一直点中心，哪个整体更好。
**不能回答**：同一棋盘上角落比中心好多少。

用法:
  python bench/ab_first_click_corner.py --games 2000 --seed 20270000
  python bench/ab_first_click_corner.py --games 2000 --compare center corner edge
"""
import argparse
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 例外授权：B 线对局引擎仍为 Python（用户已批准，耗时读数不可用于排序）
USABLE_FOR_RANKING = False
TIME_CAVEAT = ("耗时读数不可用于策略排序（同进程 win_rate 调用存在 2.4~5.4x "
               "可复现漂移，见 bench/report_repeat_slowdown.py）")


def first_pos(kind, w, h):
    """返回 (x, y) 0-based 首点坐标。"""
    if kind == "center":
        return (w // 2, h // 2)
    if kind == "corner":
        return (0, 0)
    if kind == "edge":
        return (0, h // 2)
    raise ValueError("unknown kind %s" % kind)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def mcnemar_p(a_only, b_only):
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def mde80(p, n):
    if n <= 0:
        return None
    se = math.sqrt(2 * p * (1 - p) / n)
    return (1.959964 + 0.841621) * se


def run_arm(kind, games, seed, w, h, a, limit, node_cap):
    """跑一个首点策略，返回逐局结果（含首格数字，用于中间指标）。"""
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "firstclick"), True)
    import ab_policy as ab

    fx, fy = first_pos(kind, w, h)
    results = []
    t0 = time.perf_counter()
    for i in range(games):
        sd = seed + i
        try:
            r = ab.play_one(sd, w, h, a, "baseline", limit=limit,
                            node_cap=node_cap, world_cap=4096,
                            first=(fx, fy))
        except Exception:
            continue
        if isinstance(r, dict):
            results.append({"seed": sd, "won": bool(r.get("won")),
                            "died": bool(r.get("died")),
                            "opened": r.get("opened"),
                            "first_num": r.get("first_num")})
        if (i + 1) % 200 == 0:
            print("    %s: %d/%d" % (kind, i + 1, games), flush=True)
    wall = time.perf_counter() - t0
    return results, wall


def summarize(results, kind, wall, w, h):
    n = len(results)
    won = sum(1 for r in results if r["won"])
    rate = won / n if n else 0.0
    lo, hi = wilson(won, n)
    # 中间指标：首格数字为 0 的比率
    nums = [r["first_num"] for r in results if r.get("first_num") is not None]
    zero = sum(1 for x in nums if x == 0)
    zlo, zhi = wilson(zero, len(nums)) if nums else (0.0, 0.0)
    return {
        "kind": kind, "n": n, "won": won, "rate": rate, "ci95": [lo, hi],
        "wall_s": wall,
        "first_num_n": len(nums), "first_num_zero": zero,
        "zero_rate": (zero / len(nums) if nums else None),
        "zero_rate_ci95": [zlo, zhi] if nums else None,
        "USABLE_FOR_RANKING": USABLE_FOR_RANKING,
        "time_caveat": TIME_CAVEAT,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", nargs="*",
                    default=["center", "corner"],
                    help="要比较的首点策略：center / corner / edge")
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20270000)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--node-cap", type=int, default=200000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    print("=" * 100)
    print("P2：角落开局 vs 中心开局 —— 整局配对 A/B")
    print("=" * 100)
    print("  [!] 例外授权：对局引擎为 Python（用户已批准），耗时不参与排序")
    print("  [!] " + TIME_CAVEAT)
    print()
    print("  设计：固定 seed 序列，两策略各跑同一批 seed，逐局配对（seed 作区组）")
    print("  注意：延迟布雷下首点不同 ⇒ 棋盘不同，这是策略级配对，不是同盘选格配对")
    print()
    print("  臂=%s  局数=%d  seed=%d+" % (args.compare, args.games, args.seed))
    print()

    all_res = {}
    sums = []
    for kind in args.compare:
        print("--- 跑 %s ---" % kind, flush=True)
        res, wall = run_arm(kind, args.games, args.seed,
                            args.w, args.h, args.a,
                            args.limit, args.node_cap)
        all_res[kind] = res
        s = summarize(res, kind, wall, args.w, args.h)
        sums.append(s)
        print("  %s: 胜率 %d/%d = %.2f%%  95%%CI [%.2f, %.2f]"
              % (kind, s["won"], s["n"], s["rate"] * 100,
                 s["ci95"][0] * 100, s["ci95"][1] * 100))
        if s["zero_rate"] is not None:
            print("       中间指标：首格数字=0 的比率 %d/%d = %.2f%%  95%%CI [%.2f, %.2f]"
                  % (s["first_num_zero"], s["first_num_n"],
                     s["zero_rate"] * 100,
                     s["zero_rate_ci95"][0] * 100,
                     s["zero_rate_ci95"][1] * 100))
        else:
            print("       中间指标：无 first_num 字段（需在 play_one 里记录）")
        print()

    # ---- 配对比较 ----
    if len(args.compare) >= 2:
        base = args.compare[0]
        base_map = {r["seed"]: r["won"] for r in all_res[base]}
        print("=" * 100)
        print("配对比较（同 seed；基线 = %s）" % base)
        print("=" * 100)
        for kind in args.compare[1:]:
            amap = {r["seed"]: r["won"] for r in all_res[kind]}
            common = sorted(set(base_map) & set(amap))
            if not common:
                print("  %s vs %s: 无共同 seed" % (kind, base))
                continue
            a_only = sum(1 for s in common if amap[s] and not base_map[s])
            b_only = sum(1 for s in common if base_map[s] and not amap[s])
            n = len(common)
            ra = sum(1 for s in common if amap[s]) / n
            rb = sum(1 for s in common if base_map[s]) / n
            p = mcnemar_p(a_only, b_only)
            mde = mde80(rb, n)
            print()
            print("  %s vs %s（n=%d）" % (kind, base, n))
            print("    %s 胜率 %.2f%%   %s 胜率 %.2f%%   Delta=%+.2fpp"
                  % (base, rb * 100, kind, ra * 100, (ra - rb) * 100))
            print("    配对：%s 独赢 %d，%s 独赢 %d，McNemar p=%.4f"
                  % (kind, a_only, base, b_only, p))
            if mde:
                verdict = ("可检出该效应" if abs(ra - rb) >= mde
                           else "低于 MDE，不可判定")
                print("    MDE(80%%) ~ %.2fpp => %s" % (mde * 100, verdict))
            print("    判定：" + ("显著" if p < 0.05
                                else "不显著（不等于无效应，受 MDE 限制）"))

    # ---- 中间指标与最终判据的对照（核心表）----
    print()
    print("=" * 100)
    print("中间指标 vs 最终判据（必须分开看，不能混淆）")
    print("=" * 100)
    print("  %-8s %-14s %-22s %s" % ("臂", "首格为0率", "整局胜率", "说明"))
    print("  " + "-" * 92)
    for s in sums:
        zr = ("%.2f%%" % (s["zero_rate"] * 100)
              if s["zero_rate"] is not None else "—")
        print("  %-8s %-14s %-22s %s"
              % (s["kind"], zr,
                 "%.2f%% (n=%d)" % (s["rate"] * 100, s["n"]),
                 "中间指标改善 ≠ 胜率提升" if s["kind"] != args.compare[0] else "基线"))
    print()
    print("  ⚠️ 读法：")
    print("    · 若某臂首格为0率显著更高，但整局胜率无显著差异 ⇒ 中间指标未转化为收益")
    print("    · 若两者都显著 ⇒ 才可声称该策略有效")
    print("    · 若首格为0率反而没更高 ⇒ 先查仪器（组合学推导与实现是否一致）")

    out = args.out or os.path.join(
        ROOT, "state", "ab", "firstclick_%s.json" % "_".join(args.compare))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summaries": sums,
                   "USABLE_FOR_RANKING": USABLE_FOR_RANKING,
                   "time_caveat": TIME_CAVEAT,
                   "args": vars(args)}, f, ensure_ascii=False, indent=1)
    print()
    print("  明细已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
