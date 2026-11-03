# -*- coding: utf-8 -*-
"""分析 2000 局结果：胜率对比、耗时分布、峰值内存趋势、降级计数。"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

D = os.path.join(ROOT, "state", "ab", "sweep_node_baseline")
p = os.path.join(D, "node_2000000.jsonl")

rows = []
for line in open(p, encoding="utf-8"):
    try:
        rows.append(json.loads(line))
    except Exception:
        pass

print("=" * 88)
print(f"2000 局结果分析（读 {len(rows)} 行）")
print("=" * 88)

ok = [r for r in rows if not r.get("error")]
err = [r for r in rows if r.get("error")]
print(f"有效 {len(ok)} 局，错误 {len(err)} 局")

# ---- 胜率 ----
wins = sum(1 for r in ok if r.get("won"))
print(f"\n胜率: {wins}/{len(ok)} = {wins / len(ok) * 100:.2f}%")
print("历史基线对照：项目真实历史 33.86%（316 局）；本轮 2000 局 CI "
      "[33.38%, 37.57%] ⇒ 33.86% 落在 CI 内")

# ---- 耗时 ----
ms = [r["ms"] for r in ok]
ms_s = sorted(ms)


def pctl(v, q):
    k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[k]


print("\n整局耗时（ms）：")
print(f"  中位 {statistics.median(ms):.0f}  p90 {pctl(ms_s, 0.9):.0f}  "
      f"p99 {pctl(ms_s, 0.99):.0f}  max {max(ms):.0f}  "
      f"合计 {sum(ms) / 1000:.1f}s")

# ---- 峰值内存：单局 vs 跨局累积（关键判别）----
print("\n峰值内存趋势（判别单局峰值 vs 跨局累积）：")
peaks = [r["peak_rss_mb"] for r in ok]
print(f"  全部局峰值：min {min(peaks):.1f}  max {max(peaks):.1f} MB")
# 分 5 段看趋势
n = len(peaks)
seg = n // 5
for i in range(5):
    chunk = peaks[i * seg:(i + 1) * seg] if i < 4 else peaks[4 * seg:]
    if not chunk:
        continue
    print(f"  段{i + 1}（局 {i * seg}-{i * seg + len(chunk) - 1}）："
          f"max {max(chunk):.1f} MB  中位 {statistics.median(chunk):.1f} MB")

# 线性趋势
if n > 10:
    xs = list(range(n))
    mx = statistics.mean(xs)
    my = statistics.mean(peaks)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, peaks))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den else 0
    print(f"  线性趋势：{slope:.6f} MB/局 ⇒ 2000 局累计约 "
          f"{slope * n:.1f} MB")
    if abs(slope * n) > 50:
        print("  ⚠️ 跨局累积明显（>50MB），需复查")
    else:
        print("  ✅ 无明显跨局累积")

# ---- 降级 ----
deg = sum(r.get("degrade_decisions", 0) for r in ok)
deg_games = sum(1 for r in ok if r.get("degrade_decisions", 0) > 0)
print(f"\n降级：决策总数 {deg}，发生降级的局 {deg_games}")

# ---- 与历史同类测量对比 ----
print("\n与历史对照：")
print("  2026-11-03 12 局（同一 cap）：胜率 33.33%，中位 458ms，p90 9794ms")
print(f"  2026-11-03 2000 局（本次）：胜率 {wins / len(ok) * 100:.2f}%，"
      f"中位 {statistics.median(ms):.0f}ms，p90 {pctl(ms_s, 0.9):.0f}ms")
print("  ⚠️ 耗时差异大，需查是否样本分布不同（12 局样本小、易被难局主导）")
