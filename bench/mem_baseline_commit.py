# -*- coding: utf-8 -*-
"""基线内存口径校准：commit(Private Bytes) vs RSS 的真实读数。

为什么必须做：
  2026-11-09 我宣称"修复后峰值 50~58MB"——那是 **RSS** 读数。
  本仓 bench/stress_20k_production.py 的注释已记录实测教训：
  decide 深搜的大块内存被 Windows 换页后 RSS 只显示 60-80MB，而
  **commit（任务管理器"提交大小"）才是系统级"内存不足"的判据**（历史达 21GB）。
  因此 50~58MB 的 RSS 读数不能证明 OOM 已修复，必须用 commit 重测。

本脚本给出三段读数：
  A. 仅 import（numpy + native）后的 baseline commit
  B. 一次 decide 后的 commit（含游戏内逐决策增量峰值）
  C. 200 次重复 decide 的 commit 漂移（判是否累积不释放）

用法：
    <PY310_PYTHON> bench/mem_baseline_commit.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32


def main():
    import numpy as np
    import psutil
    from utils import native

    proc = psutil.Process()

    def snap():
        mi = proc.memory_info()
        return {"commit_mb": round(mi.private / (1024 * 1024), 1),
                "rss_mb": round(mi.rss / (1024 * 1024), 1)}

    print("A. import 后 baseline:", json.dumps(snap()))

    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    lay = d["layouts"][0]
    mine = [[False] * W2 for _ in range(H + 2)]
    for (mx, my) in lay:
        mine[my][mx] = True
    cv = np.zeros((H + 2, W2), dtype=np.int32)
    for y in range(1, H + 1):
        for x in range(1, W + 1):
            cv[y][x] = 9

    def number(x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= W and 1 <= ny <= H and mine[ny][nx]:
                    c += 1
        return c

    def flood(sx, sy, opened):
        stack = [(sx, sy)]
        while stack:
            x, y = stack.pop()
            if cv[y][x] != 9 or mine[y][x]:
                continue
            n = number(x, y)
            cv[y][x] = n
            opened[0] += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))

    opened = [0]
    flood(1, 1, opened)
    print("   开局后（未决策）:", json.dumps(snap()), "opened=", opened[0])

    r = native.mscore.decide(cv, a=A, w=W, h=H)
    print("B. 首次 decide 后:", json.dumps(snap()),
          "reason=", r["reason"], "cost_log2=", round(float(r["cost_log2"]), 2))

    # C. 重复同一盘面 decide 199 次，看 commit 是否累积
    worst = 0.0
    for i in range(199):
        native.mscore.decide(cv, a=A, w=W, h=H)
        worst = max(worst, proc.memory_info().private / (1024 * 1024))
    print("C. 再 199 次 decide 后:", json.dumps(snap()), "期间 commit 峰值=%.0fMB" % worst)
    import gc
    gc.collect()
    print("   gc.collect() 后:", json.dumps(snap()))


if __name__ == "__main__":
    main()
