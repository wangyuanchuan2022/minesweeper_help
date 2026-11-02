# -*- coding: utf-8 -*-
"""大情况 10x 达标预算（修正版）。

前版错误更正：脚本曾打印"S2+修耗时模型 → 超过 10x"，实际 487.1/155.8 = 3.1x，
末尾又算出 6.9x，自相矛盾。本版只做一件事：把每项按"实测/估算"分开列清，
算出到底缺什么才能到 10x，不含任何乐观假设。

实测基准（bench/instrument_walltime.py + count_vision_work.py + profile_pipeline.py）：
  视觉全盘识别            309.6 ms   <- 实测
  决策（含耗时模型 39.8）    78.4 ms   <- 实测
  点击后 sleep(0.1)       100.0 ms   <- 源码常量
  抓屏 ImageGrab 整屏       37.2 ms   <- 实测；_grab_board 只抓棋盘区，按比例折算
  合计单步                487.1 ms   <- 实测（与 487.1 口径一致）

用法: python bench/verify_large_speedup.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# ---------- 实测值 ----------
VISION_FULL = 309.6      # ms  实测：480 格 × 25 次 cv.matchTemplate
SPEEDUP_VECTOR = 18.3    # x   实测：numpy 批量同语义计分，逐格 0 不一致
DECISION = 78.4          # ms  实测：number5_1 扣掉视觉
FITK_WASTE = 39.8        # ms  实测：_ps_observe 顶层（record + refit + tbsave）
WAIT_SLEEP = 100.0       # ms  源码常量 time.sleep(0.1)
GRAB_FULL = 37.2         # ms  实测 ImageGrab 整屏
GRAB_BOARD_RATIO = 0.40  # 估算：_grab_board 只抓 30*24 × 16*24 区域

BASELINE = 487.1         # ms  实测单步
TARGET_10X = BASELINE / 10.0


def fmt(label, v, note=""):
    return f"  {label:<38}{v:8.1f} ms   {note}"


def main():
    grab_board = GRAB_FULL * GRAB_BOARD_RATIO
    print(f"目标：单步 ≤ {TARGET_10X:.1f} ms（基准 {BASELINE} ms 的 1/10）")
    print("=" * 78)
    print("【现状拆解】")
    print(fmt("视觉全盘识别", VISION_FULL, "实测"))
    print(fmt("决策（含耗时模型浪费 39.8）", DECISION, "实测"))
    print(fmt("点击后 sleep(0.1)", WAIT_SLEEP, "源码常量"))
    print(fmt("抓屏（棋盘区域，按 40% 折算）", grab_board, "估算"))
    print(f"  {'合计':<38}{VISION_FULL+DECISION+WAIT_SLEEP+grab_board:8.1f} ms")

    print("\n" + "=" * 78)
    print("【逐项可达性】每一档都标出依据与仍缺的部分")
    print("=" * 78)

    stages = []

    # 阶段1：仅矢量化视觉
    v1 = VISION_FULL / SPEEDUP_VECTOR
    d1, w1, g1 = DECISION, WAIT_SLEEP, grab_board
    stages.append(("① 仅矢量化视觉（已实测 18.3x）", v1, d1, w1, g1,
                   "实测", "视觉已解决；决策/等待成为瓶颈"))

    # 阶段2：+ 修耗时模型（删落盘/拟合，或按注释只取 200 条窗口）
    d2 = DECISION - FITK_WASTE + 2.0     # 保留 2ms 用于必要观测
    stages.append(("② +修耗时模型（39.8→2）", v1, d2, w1, g1,
                   "实测+估算", "决策仍 40ms 级"))

    # 阶段3：+ 决策其余部分下沉 C++（分组/概率组装/热力图 dict）
    d3 = 10.0                            # 估算：C++ 化后保留调用与 Python 边界开销
    stages.append(("③ +决策主体下沉 C++", v1, d3, w1, g1,
                   "估算", "需 C++ 改造，仍差等待"))

    # 阶段4：+ 等待降到 15ms（点击后不必等 100ms，改为检测棋盘稳定）
    w2 = 15.0
    stages.append(("④ +点击等待 100→15ms", v1, d3, w2, g1,
                   "估算", "视觉 17ms 此时是最大项"))

    # 阶段5：+ 增量识别（只认新开格）
    v2 = 1.5                             # 估算：diff 全盘 + 识别 ~8 格
    stages.append(("⑤ +增量识别（只认新开格）", v2, d3, w2, g1,
                   "估算", "达标"))

    for name, v, d, w, g, basis, note in stages:
        total = v + d + w + g
        print(f"{name}")
        print(f"   视觉{v:6.1f} + 决策{d:6.1f} + 等待{w:6.1f} + 抓屏{g:5.1f} "
              f"= {total:6.1f} ms   提速 {BASELINE/total:5.2f}x   "
              f"{'✅达标' if total <= TARGET_10X else '❌未达标'}  [{basis}]")
        print(f"   → {note}")
        print()

    print("=" * 78)
    print("结论（诚实口径）：")
    print("  1) 大局面 10x 无法靠算法搜索达成 —— part_solve 实测仅 2.3ms，")
    print("     即使优化到 0，单步也只从 487 降到 485ms。")
    print("  2) 必须同时动四处：视觉矢量化(实测18.3x) + 去掉耗时模型浪费 +")
    print("     决策主体下沉C++ + 等待/抓屏优化。单做任何一项都只有 2~4x。")
    print("  3) 其中只有『视觉 18.3x』是已实测；其余为估算，需实现后复测。")
    print("  4) 增量识别是唯一能让视觉逼近 0 的手段，但风险最高：")
    print("     flood-fill 大范围变化、动画未结束、DPI/窗口移动都会导致漏识别。")


if __name__ == "__main__":
    main()
