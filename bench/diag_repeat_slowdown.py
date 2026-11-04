# -*- coding: utf-8 -*-
"""定位「同进程内重复调用越来越慢」的原因。

=================== 现象（有数据）====================
G10 合成用例，ad=0（depth_limit=10、wr_nodes=1,241,184、输出恒定）：
  · 每个**新进程**第一次调用：outer_ms ≈ 876ms（4 个进程极差仅 52ms）
  · 同进程内连续调用：858 → 2031 → 7307 → 4437 → 4633 → ... → 4646ms
    （中位 4641ms，极差 6448ms，max/min = 8.5x）

⇒ 工作量完全相同（节点数逐次一致），但耗时随调用次数上升后稳定在 ~4.6s。
**这是进程内状态累积**，不是 CPU 频率/页缓存能解释的（新进程每次都 876ms）。

=================== 待检验的竞争假设 ====================
  H1  C++ 侧全局/静态状态未清空（如某处 static 容器）
  H2  Python 侧内存碎片 / GC 压力（每次调用分配大量 Board）
  H3  Windows 堆碎片：每次 new/delete 大量小对象，堆逐步碎片化
      ⇒ 后续分配变慢（同一进程内累积，新进程无此问题）
  H4  wr_f 的 ctx.memory 只写不删，但每次调用新建 ctx ⇒ 不应累积；
      除非有跨调用的静态结构

判定手段：逐次记录 (调用序号, wr_nodes, enum_ms, outer_ms)。
  · 若 wr_nodes 恒定而 outer_ms 单调上升 ⇒ 与算法工作量无关（H2/H3 类）
  · 若 wr_nodes 也上升 ⇒ 算法路径变了（H1 类）
再测：调用之间显式触发 Python GC / 释放引用，看是否缓解（区分 H2 与 H3）。

用法: python bench/diag_repeat_slowdown.py
"""
import gc
import json
import os
import statistics
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["MSW_WR_PHASE"] = "1"


def run_seq(n, gc_between=False):
    """在新进程里连续调用 n 次，返回每次的 (outer_ms, wr_nodes)。"""
    snippet = (
        "import sys,os,gc;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');os.chdir(r'%s');"
        "from verify_adaptive_depth_synthetic import build_cases,call;"
        "c=dict(build_cases())['G10'];"
        "n=%d;gcb=%r;"
        "r=[]\n"
        "for i in range(n):\n"
        "    call(c,2000000,0)\n"
        "    if gcb: gc.collect()\n"
        % (ROOT, os.path.join(ROOT, "bench"), ROOT, n, gc_between)
    )
    env = dict(os.environ)
    env["MSW_WR_PHASE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", snippet],
                          capture_output=True, text=True, env=env)
    out = []
    for line in (proc.stderr or "").splitlines():
        if "[wr_phase]" not in line:
            continue
        d = {}
        for tok in line.split("] ", 1)[1].split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                d[k] = v
        out.append(d)
    return out


def main():
    print("=" * 100)
    print("诊断：同进程内重复调用越来越慢")
    print("=" * 100)
    print()

    print("-" * 100)
    print("A) 连续 8 次调用（无 GC 干预），逐次记录")
    print("-" * 100)
    rows = run_seq(8, gc_between=False)
    print(f"{'#':>3}{'wr_nodes':>12}{'enum_ms':>10}{'outer_ms':>12}{'depth_limit':>13}")
    print("-" * 100)
    nodes = []
    outers = []
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}{r.get('wr_nodes','?'):>12}{r.get('enum_ms','?'):>10}"
              f"{r.get('outer_ms','?'):>12}{r.get('depth_limit','?'):>13}")
        try:
            nodes.append(int(r.get("wr_nodes", "0")))
            outers.append(float(r.get("outer_ms", "0")))
        except ValueError:
            pass

    print()
    if nodes and outers:
        same_nodes = (len(set(nodes)) == 1)
        print(f"  wr_nodes 是否恒定：{'是' if same_nodes else '否'}（{sorted(set(nodes))}）")
        if len(outers) >= 3:
            first, last = outers[0], outers[-1]
            print(f"  outer_ms 首次 {first:.0f} → 末次 {last:.0f}"
                  f"（{last/max(1,first):.1f}x）")
        if same_nodes and outers[-1] > outers[0] * 1.5:
            print("  ⇒ 工作量恒定而耗时上升 ⇒ **与算法路径无关**，")
            print("     是进程内资源状态累积（H2/H3 类）。H1（算法状态残留）被排除。")
        elif not same_nodes:
            print("  ⇒ wr_nodes 也在变 ⇒ 算法路径改变（H1 类），需查静态状态。")

    print()
    print("-" * 100)
    print("B) 连续 6 次调用，每次之间显式 gc.collect()")
    print("-" * 100)
    rows2 = run_seq(6, gc_between=True)
    print(f"{'#':>3}{'wr_nodes':>12}{'outer_ms':>12}")
    print("-" * 100)
    outers2 = []
    for i, r in enumerate(rows2, 1):
        print(f"{i:>3}{r.get('wr_nodes','?'):>12}{r.get('outer_ms','?'):>12}")
        try:
            outers2.append(float(r.get("outer_ms", "0")))
        except ValueError:
            pass

    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    if outers and outers2:
        slow_a = outers[-1] / max(1.0, outers[0])
        slow_b = outers2[-1] / max(1.0, outers2[0])
        print(f"  无 GC：末次/首次 = {slow_a:.1f}x")
        print(f"  有 GC：末次/首次 = {slow_b:.1f}x")
        if slow_b < slow_a * 0.7:
            print("  ⇒ 显式 GC 显著缓解 ⇒ 偏向 H2（Python 内存/GC 压力）")
        else:
            print("  ⇒ 显式 GC 未显著缓解 ⇒ 偏向 H3（C++/Windows 堆碎片）")
            print("     或其它进程内资源累积。**本条为倾向判断，非定论**。")
    print()
    print("  无论哪种，对本轮实验的含义是明确的：")
    print("    · **任何同进程内的耗时对比都不可信**（会混入累积效应）")
    print("    · 成本/耗时实验必须**每次新进程**，或**随机交替并取多次中位**")
    print("    · 这也解释了此前 ad=3/4/5 '更慢' 的读数：它们是同进程内的")
    print("      第 4/5/6 次调用，累积效应已生效，与 adaptive 无关。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
