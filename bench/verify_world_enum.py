# -*- coding: utf-8 -*-
"""守恒断言回归：对整个 wr_cases.json 校验世界枚举器的正确性。

本轮背景：bench/compare_2ply_vs_winrate.py 的 enumerate_worlds 曾把
"组内列号"当成"全局前沿下标"（fm.append(clicks[ci])），导致不同组的雷
写到重叠坐标上、世界雷数少算（实测 95/96/97 而非 a=99）。下游所有
V*/safety/各臂读数因此"自洽地错"——最隐蔽的一类错误。

本脚本是那道闸的回归：逐用例断言
  ① 每个世界的盘面内雷数严格 == a
  ② 基底已标记雷全部包含在世界内
  ③ 世界内不含任何已开格
  ④ 雷数分项守恒：num10 + 前沿雷 + 内部雷 == 总数
  ⑤ rem<0 的前沿组合被排除（不产生世界）
另打印每个用例的世界数、雷数分布，便于人工核对。

用法: python bench/verify_world_enum.py
"""
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

from compare_2ply_vs_winrate import enumerate_worlds   # noqa: E402


def main():
    path = os.path.join(ROOT, "bench", "data", "wr_cases.json")
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 88)
    print("世界枚举守恒断言（逐用例）")
    print("=" * 88)
    bad = 0
    for ci, case in enumerate(cases):
        a = int(case["a"])
        try:
            worlds, clicks, clicks9, base = enumerate_worlds(case, validate=True)
        except AssertionError as e:
            bad += 1
            print(f"[{ci:2d}] 断言失败：{e}")
            continue
        cnt = Counter(len(w) for w in worlds)
        dist = ", ".join(f"{k}x{v}" for k, v in sorted(cnt.items()))
        ok = list(cnt.keys()) == [a]
        print(f"[{ci:2d}] worlds={len(worlds):4d} a={a:3d} "
              f"base={len(base):3d} F={len(clicks):2d} U={len(clicks9):2d} "
              f"雷数分布={dist} {'' if ok else '<<< 雷数不等于 a'}")
        if not ok:
            bad += 1

    print("-" * 88)
    print(f"用例总数 {len(cases)}，失败 {bad}")
    print("判据：每个用例的世界雷数分布必须恰好是 {a}，否则枚举器有错。")


if __name__ == "__main__":
    main()
