# -*- coding: utf-8 -*-
"""2000 局失败位置分析：游戏究竟死在哪一步？

=================== 为什么先做这个 ====================
胜率从 35.45% 提到 50%+ 需要 +14.5pp。在改任何算法之前，必须先知道
**失败集中在哪个阶段**——否则优化方向只能靠猜。

本脚本只读已有的 2000 局逐局记录（state/ab/sweep_node_baseline/
node_2000000.jsonl），做纯统计，不跑任何模拟。

可用字段：seed / won / died / steps / opened / ms / peak_rss_mb 等。
注意：**没有**"死在第几次决策"的直接字段，只能从 steps 与 opened 推断
（steps 是主循环步数，opened 是最终已开格数）。

判据说明：
  · opened 很小就死 ⇒ 早期死亡（开局信息不足，第 1-3 次猜测就炸）
  · opened 接近 381（=480-99）才死 ⇒ 后期死亡（残局难猜）
  · 这条分界决定"该优化开局还是优化残局"

用法: python bench/analyze_death_stage.py
"""
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

TOTAL_SAFE = 30 * 16 - 99          # 381 格需要全部打开才算赢


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def main():
    path = os.path.join(ROOT, "state", "ab", "sweep_node_baseline",
                        "node_2000000.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    print("=" * 96)
    print(f"2000 局失败位置分析（{len(rows)} 局，读 {path}）")
    print("=" * 96)

    err = [r for r in rows if r.get("error")]
    ok = [r for r in rows if not r.get("error")]
    wins = [r for r in ok if r["won"]]
    died = [r for r in ok if r["died"]]
    unfinished = [r for r in ok if not r["won"] and not r["died"]]

    print(f"\n总览：{len(ok)} 局有效  |  胜 {len(wins)} "
          f"({len(wins)/len(ok)*100:.2f}%)  踩雷 {len(died)} "
          f"({len(died)/len(ok)*100:.2f}%)  "
          f"未完成 {len(unfinished)}  error {len(err)}")

    if unfinished:
        print(f"\n⚠️ 未完成 {len(unfinished)} 局（卡死/触顶）—— 这也是可回收的胜率！")
        us = sorted(u["opened"] for u in unfinished)
        print(f"   其 opened：min {us[0]} med {us[len(us)//2]} max {us[-1]}")
        print(f"   其 steps ："
              f"{sorted(u['steps'] for u in unfinished)[:10]} …")

    # ---- 死亡位置分布 ----
    print("\n" + "=" * 96)
    print("踩雷局的『死亡时已开格数』分布（越接近 381 说明越接近赢）")
    print("=" * 96)
    op = sorted(r["opened"] for r in died)
    print(f"  n={len(op)}  min {op[0]}  p10 {pct(op, .1):.0f}  "
          f"p25 {pct(op, .25):.0f}  中位 {pct(op, .5):.0f}  "
          f"p75 {pct(op, .75):.0f}  p90 {pct(op, .9):.0f}  max {op[-1]}")
    print(f"  需开满 {TOTAL_SAFE} 格")

    print("\n  分桶（按已完成比例 = opened/381）：")
    buckets = [(0.0, 0.10), (0.10, 0.25), (0.25, 0.50), (0.50, 0.75),
               (0.75, 0.90), (0.90, 0.99), (0.99, 1.01)]
    for lo, hi in buckets:
        sub = [r for r in died if lo <= r["opened"] / TOTAL_SAFE < hi]
        if not sub:
            continue
        bar = "#" * int(len(sub) / len(died) * 60)
        print(f"  [{lo*100:>5.0f}%,{min(hi,1)*100:>5.1f}%)  {len(sub):>4} 局 "
              f"{len(sub)/len(died)*100:>5.1f}%  {bar}")

    # ---- 若能在某个阶段全额挽回，胜率上限是多少 ----
    print("\n" + "=" * 96)
    print("『若该阶段全部救回』的胜率上限（乐观上界，用于判断值得投哪一段）")
    print("=" * 96)
    base = len(wins) / len(ok) * 100
    print(f"  当前胜率 {base:.2f}%")
    for lo in (0.10, 0.25, 0.50, 0.75):
        n_rescue = sum(1 for r in died if r["opened"] / TOTAL_SAFE >= lo)
        upper = (len(wins) + n_rescue) / len(ok) * 100
        print(f"  救回所有『已开≥{lo*100:>3.0f}%』才死的局（{n_rescue:>3} 局）"
              f" ⇒ 胜率上界 {upper:>6.2f}%  (+{upper-base:.2f}pp)")

    # ---- steps 分布 ----
    print("\n" + "=" * 96)
    print("steps（主循环步数）分布")
    print("=" * 96)
    for label, group in (("胜局", wins), ("踩雷局", died)):
        if not group:
            continue
        st = sorted(r["steps"] for r in group)
        print(f"  {label:<6} n={len(st):>4}  中位 {st[len(st)//2]:>3}  "
              f"p90 {pct(st, .9):>3}  max {st[-1]}")

    # ---- 胜局 vs 败局的特征差异 ----
    print("\n" + "=" * 96)
    print("胜局与踩雷局的特征差异（找可操作的区分量）")
    print("=" * 96)
    for key in ("steps", "opened", "ms"):
        w = [r[key] for r in wins if key in r]
        d = [r[key] for r in died if key in r]
        if not w or not d:
            continue
        print(f"  {key:<8} 胜局中位 {statistics.median(w):>9.1f}   "
              f"踩雷局中位 {statistics.median(d):>9.1f}")

    out = {
        "n": len(ok), "wins": len(wins), "died": len(died),
        "unfinished": len(unfinished),
        "win_rate": len(wins) / len(ok),
        "death_opened_median": pct(op, .5),
        "death_opened_p25": pct(op, .25),
        "death_opened_p75": pct(op, .75),
    }
    dest = os.path.join(ROOT, "state", "ab", "death_stage.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n摘要已写 {dest}")


if __name__ == "__main__":
    main()
