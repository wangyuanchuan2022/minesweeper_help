# -*- coding: utf-8 -*-
"""端到端单步耗时模型：把"每步"各段加起来，量化 10x 的可行性边界。

口径说明（重要）：
- 视觉/决策/等待 三段都有实测来源；"每步触发几次扫描"按代码路径推得。
- 不含真实游戏窗口交互，因此 click 与 sleep 按源码常量计，抓屏用真实 ImageGrab。

用法: python bench/step_budget.py
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import ImageGrab

# --- 来自 instrument_walltime.py 的实测（中位数） ---
VISION_FULL_SCAN = 308.7   # ms  complete_scan 全盘（未开格全部重认）
DECISION = 387.1 - 308.7   # ms  number5_1 扣掉视觉后的决策部分 = 78.4
FITK = 49.2                # ms  其中耗时模型拟合（纯浪费）
RECORD = 11.5              # ms  其中样本落盘（整文件重写）
TBSAVE = 5.2

# --- 源码常量 ---
CLICK_SLEEP = 100          # ms  number5_1 里点完 time.sleep(0.1)
MOVE_WAIT = 100            # ms  complete_scan 先 moveTo 再抓屏


def grab_ms(n=5):
    t0 = time.perf_counter()
    for _ in range(n):
        ImageGrab.grab()
    return (time.perf_counter() - t0) / n * 1000


def main():
    g = grab_ms()
    print(f"ImageGrab.grab 整屏实测 = {g:.1f} ms")
    print("=" * 72)

    print("【现状】单步（一次决策 + 后续全盘重扫）")
    cur = CLICK_SLEEP + VISION_FULL_SCAN + DECISION
    print(f"  点击+等待        {CLICK_SLEEP:7.1f} ms  {CLICK_SLEEP/cur*100:5.1f}%")
    print(f"  视觉全盘扫描     {VISION_FULL_SCAN:7.1f} ms  {VISION_FULL_SCAN/cur*100:5.1f}%")
    print(f"  决策(含耗时模型) {DECISION:7.1f} ms  {DECISION/cur*100:5.1f}%")
    print(f"  ── 合计          {cur:7.1f} ms")

    print("\n【优化后】保守方案（全部为已验证或低风险项）")
    # 1) 视觉：实测逐格等价批量 18.3x → 308.7/18.3
    v2 = VISION_FULL_SCAN / 18.3
    # 2) 增量重扫：只扫变化的未开格（flood-fill 后才扫），保守按 30% 未开格变化
    v2_inc = v2 * 0.30 + 100 * 0.7 / 1000  # 若用增量，扫描量降到 30%
    v2_inc = v2 * 0.30
    # 3) 决策：删掉耗时模型落盘(11.5+5.2) + 截窗拟合(49.2→1.8)
    d2 = DECISION - FITK - RECORD - TBSAVE + 1.8
    # 4) 等待：点击 sleep 100ms → 15ms（可配置），moveTo 后抓屏不等
    w2 = 15
    opt = w2 + v2_inc + d2
    print(f"  视觉(批量18.3x,仅扫变化格30%) {v2_inc:7.1f} ms  {v2_inc/opt*100:5.1f}%")
    print(f"  决策(去掉耗时模型浪费)         {d2:7.1f} ms  {d2/opt*100:5.1f}%")
    print(f"  点击等待(100→15ms)             {w2:7.1f} ms  {w2/opt*100:5.1f}%")
    print(f"  ── 合计                        {opt:7.1f} ms")
    print(f"  → 相对现状提速 {cur/opt:5.1f}x")

    print("\n【激进方案】额外项（需要验证，标为推断）")
    # 视觉再砍：预筛+批量实测 21.6x；增量扫描降到 15%
    v3 = VISION_FULL_SCAN / 21.6 * 0.15
    # 决策：C++ 接管分组/概率组装（"其余"35ms 里大头），保守砍到 10ms
    d3 = 10 + 1.8
    w3 = 8
    aggr = w3 + v3 + d3
    print(f"  视觉(预筛+增量15%)   {v3:7.1f} ms")
    print(f"  决策(C++ 接管)       {d3:7.1f} ms")
    print(f"  等待                 {w3:7.1f} ms")
    print(f"  ── 合计              {aggr:7.1f} ms  → 提速 {cur/aggr:5.1f}x  【推断，需实测验证】")

    print("\n【天花板】不可压缩的固定成本")
    floor = g * 1 + 8 + 5   # 至少 1 次抓屏 + 点击 + 渲染
    print(f"  抓屏 {g:.1f} + 点击/渲染 ~13ms ≈ {floor:.1f} ms/步")
    print(f"  → 理论极限提速 ≈ {cur/floor:.1f}x（再多就没有物理依据了）")


if __name__ == "__main__":
    main()
