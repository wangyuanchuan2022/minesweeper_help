# -*- coding: utf-8 -*-
"""用**现有数据**判定历史 base_true.json 由哪种首点规则产生。

=================== 为什么这一步能定案 ====================
已确认（advisor 要求的最小双版本对拍已做完）：
  · seed=20261108：历史 commit b8f659d 与当前 HEAD **都**在首个决策点
    选 (0,15)、踩雷、opened=1
  · 但历史记录 base_true.json 里该 seed 是 won=true, steps=23, opened=381
  ⇒ 历史记录**不可能由这两个版本中的任何一个**产生。

因此历史 40.5% 不是"当前算法的能力"，也不是"代码退化的证据"，
而是**用另一套规则/环境跑出来的**。哪种规则？可用 opened 分布判定：

  · 延迟布雷（deferred）：84.15% 的对局首点只开 1 格
    ⇒ 踩雷局里会有大量 opened<=2
  · 3x3 安全（safe3x3）：首点必开一大片（均值 41.2 格），
    **不可能**出现 opened=1 的踩雷局

所以：若历史踩雷局存在 opened=1，则**不是** 3x3 规则；
若历史踩雷局几乎不可能是 opened=1，则**不是** deferred 规则。

用法: python bench/identify_hist_rule.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summarize(name, path, key="results"):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = [r for r in d.get(key, []) if "error" not in r]
    wins = [r for r in rows if r["won"]]
    died = [r for r in rows if r["died"]]
    print(f"\n【{name}】{os.path.basename(path)}")
    print(f"  总局 {len(rows)}  胜 {len(wins)} ({len(wins)/len(rows)*100:.2f}%)  "
          f"踩雷 {len(died)}")
    op = sorted(r["opened"] for r in rows)
    print(f"  全部 opened：中位 {op[len(op)//2]}  min {op[0]}  max {op[-1]}")
    if died:
        dop = sorted(r["opened"] for r in died)
        n1 = sum(1 for x in dop if x <= 1)
        n5 = sum(1 for x in dop if x <= 5)
        print(f"  踩雷局 opened：中位 {dop[len(dop)//2]}  min {dop[0]}")
        print(f"    踩雷局中 opened<=1 的比例：{n1}/{len(died)} "
              f"= {n1/len(died)*100:.2f}%")
        print(f"    踩雷局中 opened<=5 的比例：{n5}/{len(died)} "
              f"= {n5/len(died)*100:.2f}%")
    return rows, died


def main():
    print("=" * 100)
    print("判定历史 base_true.json 由哪种首点规则产生")
    print("=" * 100)
    print("\n判据：3x3 规则下首点必开≥1 大片（均值 41.2 格），")
    print("      ⇒ 踩雷局**不可能**出现 opened=1；")
    print("      而延迟布雷下 84% 的对局首点只开 1 格 ⇒ 踩雷局会有大量 opened<=2。")

    h_rows, h_died = summarize(
        "历史记录", os.path.join(ROOT, "state", "eval_run", "base_true.json"))

    # 当前 deferred 规则的实测（200 局）
    cur = os.path.join(ROOT, "state", "ab", "evalh_200.json")
    if os.path.exists(cur):
        c_rows, c_died = summarize("当前（Arbiter/deferred）", cur)

    # rule_gap 的 600 局对照（两个规则都有）
    rg = os.path.join(ROOT, "state", "ab", "rule_gap_600.json")
    if os.path.exists(rg):
        with open(rg, encoding="utf-8") as f:
            d = json.load(f)
        print(f"\n【bench/rule_gap.py 600 局配对】")
        for rule in ("deferred", "safe3x3"):
            res = d["results"][rule]
            rows = [v for v in res.values() if not v.get("error")]
            died = [v for v in rows if v.get("died")]
            op = sorted(v["opened"] for v in rows if v.get("opened") is not None)
            dop = sorted(v["opened"] for v in died if v.get("opened") is not None)
            n1 = sum(1 for x in dop if x <= 1)
            print(f"  {rule:<10} 胜 {sum(1 for v in rows if v['won'])}/{len(rows)}"
                  f" = {sum(1 for v in rows if v['won'])/len(rows)*100:5.2f}%"
                  f"  全部opened中位 {op[len(op)//2]:>3}"
                  f"  踩雷局opened中位 {dop[len(dop)//2]:>3}"
                  f"  踩雷局opened<=1 占 {n1/len(died)*100:>5.2f}%")

    # ---- 判定 ----
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    h_dop = sorted(r["opened"] for r in h_died)
    h_n1 = sum(1 for x in h_dop if x <= 1)
    h_med = h_dop[len(h_dop) // 2]
    print(f"  历史踩雷局 opened<=1 比例 = {h_n1}/{len(h_died)} "
          f"= {h_n1/len(h_died)*100:.2f}%")
    print(f"  历史踩雷局 opened 中位 = {h_med}")

    if h_n1 == 0:
        print("\n  ⇒ 历史记录**没有** opened<=1 的踩雷局")
        print("     ⇒ 与 deferred（会大量出现）不符，与 3x3 相符")
        print("     ⇒ 历史 40.5% 大概率是 **3x3 规则**下的读数，")
        print("        与当前 Arbiter 读数**不可比较**。")
    elif h_n1 / len(h_died) > 0.10:
        print("\n  ⇒ 历史记录有明显比例 opened<=1 的踩雷局")
        print("     ⇒ 规则上是 deferred，但同 seed 复现不出 ⇒")
        print("        说明当时环境/代码与 b8f659d 也不一致（不可复现）。")
    else:
        print("\n  ⇒ 比例很低，介于两者之间，需进一步分层判定。")

    print("\n  ⚠️ 无论哪种情况，历史 40.5% 都**不能**作为当前算法基线，")
    print("     也不能作为'算法退化'的证据 —— 它是不可复现的跨环境读数。")


if __name__ == "__main__":
    main()
