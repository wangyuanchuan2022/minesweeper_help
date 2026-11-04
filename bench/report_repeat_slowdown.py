# -*- coding: utf-8 -*-
"""诊断报告：同进程内重复调用 win_rate 的耗时累积效应。

=================== 现象（全部有数据，可复现）====================
合成用例（bench/verify_adaptive_depth_synthetic.py 的 G6~G11），
ad=0（不触发自适应规则），同进程内连续调用 4~8 次：

| 用例 | worlds | wr_nodes | outer_ms 序列 | 末/首 |
|---|---:|---:|---|---:|
| G6  | 64   | 4,626     | 3,3,3,2,3            | 0.93x |
| G8  | 256  | 78,282    | 46,46,44,46          | 0.99x |
| G9  | 512  | 313,940   | 196,190,197,194      | 0.99x |
| G10 | 1024 | 1,241,184 | 837,839,1620,**4417** | **5.28x** |
| G11 | 2048 | 4,081,650 | 7074,16459,17238,17748 | 2.51x |

**wr_nodes 逐次完全一致**（同一用例内恒定），但 outer_ms 随调用次数上升。

=================== 已排除的解释（有对照）====================
  · **不是 Python GC**：每次调用间显式 gc.collect()，末/首 = 5.3x（无 GC 是 5.4x），
    几乎无差别。
  · **不是算法路径改变**：wr_nodes 恒定 ⇒ 搜索工作量相同。
  · **不是无差别的进程级堆碎片**：G6/G8/G9 在同一进程内连续调用完全稳定
    （0.93~0.99x），只有 G10/G11 累积 ⇒ 与**单次调用的规模**相关。
  · **不是后台负载**：实测 python 进程数 = 1。
  · **不是 CPU 频率/页缓存**：每个新进程第一次调用稳定在 876ms
    （4 个独立进程极差仅 52ms），而同进程第 4 次起稳定在 ~4.4s。

=================== 结论的性质 ====================
**阈值已定位**：在 wr_nodes ≈ 314K（稳定）与 1.24M（累积 5.3x）之间。

**根因未明，本报告不作因果结论。**

已**验证**的事实只有两条：
  1. 同进程重复调用存在**可复现的耗时漂移**（G10: 5.28~5.44x，多次独立实验一致）
  2. **新进程测量稳定**（4 个独立进程首次调用 876ms，极差 52ms）

已**排除**的解释（每条都有对照实验）：
  · Python GC —— 显式 collect 后 5.3x vs 无 GC 5.4x，无差别
  · 算法路径改变 —— wr_nodes 逐次恒定
  · 无差别进程级堆碎片 —— 小用例（G6/G8/G9）同进程完全稳定
  · 后台负载 —— python 进程数 = 1
  · CPU 频率/页缓存 —— 新进程首次调用稳定

**未验证、不得作为结论的推测**（明确标注为推测）：
  · C++ 侧小对象分配触发 Windows 堆阈值行为
  · ctx.memory 条目量导致分配路径变化
  · 具体阈值在 314K~1.24M 节点之间（这只是**观测区间**，不是机制）
要定论需：C++ 内观测进程堆大小曲线（HeapAlloc / Private Bytes），
或用 Application Verifier / ETW 追踪分配路径。**该排查另案进行，
不阻塞 B 线（整局策略评测）。**

=================== 对本轮实验的硬约束（最重要）====================
1. **任何同进程内的耗时对比都不可信** —— 会混入累积效应。
   此前 `ad=3/4/5 比 ad=0 慢 3~5 倍` 的读数，正是因为它们是同进程内
   的第 4/5/6 次调用，累积已生效，**与 adaptive 无关**。
2. 耗时实验必须：
   - 每个配置**独立新进程**，或
   - **随机交替**并取多次**中位数/p90**，且报告极差
3. 本轮 G10 的用途边界（收窄）：
   - 只能证明 adaptive 参数**确实作用到了 depth_limit**
     （10→4.883、wr_nodes 1.24M→47K、输出改变）
   - **不能**用于标定成本模型（n_cand×total 的系数）
   - **不能**推出「变浅更省时」或「变浅降低胜率」

用法: python bench/report_repeat_slowdown.py
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def measure(case_name, n=4, adaptive=0):
    """在新进程里连续调用 n 次，返回 [(wr_nodes, outer_ms), ...]。"""
    snippet = (
        "import sys,os;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');os.chdir(r'%s');"
        "from verify_adaptive_depth_synthetic import build_cases,call;"
        "cs=dict(build_cases());"
        "[call(cs['%s'],20000000,%d) for _ in range(%d)]"
        % (ROOT, os.path.join(ROOT, "bench"), ROOT, case_name, adaptive, n)
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
        out.append((int(d.get("wr_nodes", "0")), float(d.get("outer_ms", "0"))))
    return out


def main():
    print("=" * 100)
    print("win_rate 同进程重复调用的耗时累积效应 —— 诊断报告")
    print("=" * 100)
    print()
    print(f"{'用例':<8}{'wr_nodes':>12}{'outer_ms 序列':>34}{'末/首':>9}{'判定':>12}")
    print("-" * 100)
    verdicts = {}
    for name in ("G6", "G8", "G9", "G10", "G11"):
        rows = measure(name, n=4)
        if len(rows) < 2:
            print(f"{name:<8}{'—':>12}{'数据不足':>34}{'—':>9}{'—':>12}")
            continue
        nodes = rows[0][0]
        times = [r[1] for r in rows]
        same = len(set(r[0] for r in rows)) == 1
        ratio = times[-1] / max(1.0, times[0])
        if ratio < 1.2:
            verdict = "稳定"
        else:
            verdict = "累积"
        verdicts[name] = (nodes, ratio, verdict)
        seq = ",".join(str(round(x)) for x in times)
        print(f"{name:<8}{nodes:>12,}{seq:>34}{ratio:>8.2f}x{verdict:>12}")

    print()
    print("=" * 100)
    print("判定")
    print("=" * 100)
    stable = [(n, v) for n, (nd, v, vd) in verdicts.items() if vd == "稳定"]
    accum = [(n, v) for n, (nd, v, vd) in verdicts.items() if vd == "累积"]
    if stable and accum:
        max_stable = max(verdicts[n][0] for n, _ in stable)
        min_accum = min(verdicts[n][0] for n, _ in accum)
        print(f"  稳定用例的最大节点数：{max_stable:,}")
        print(f"  累积用例的最小节点数：{min_accum:,}")
        print(f"  ⇒ 累积阈值在 {max_stable:,} ~ {min_accum:,} 节点之间。")
        print()
        print("  **根因未明，不作因果结论。** 已排除（有对照）：")
        print("    · Python GC（显式 collect 后 5.3x，无 GC 5.4x）")
        print("    · 算法路径改变（wr_nodes 逐次恒定）")
        print("    · 无差别进程级堆碎片（小用例完全稳定）")
        print("    · 后台负载（python 进程数=1）")
        print("    · CPU 频率/页缓存（新进程首次调用稳定 876ms，极差 52ms）")
        print("  未验证的推测（不得作为结论）：C++ 小对象分配触发堆阈值、")
        print("  ctx.memory 条目量影响分配路径。要定论需在 C++ 内观测堆曲线。")
        print("  该排查另案进行，不阻塞 B 线。")
    print()
    print("  对本轮实验的硬约束：")
    print("    1. 任何同进程内的耗时对比都不可信，**不得用于策略排序**")
    print("    2. 耗时实验必须每配置独立新进程，或随机交替取中位/p90 并报极差")
    print("    3. G10 只能证明 adaptive 接线生效（10→4.883、nodes 1.24M→47K）")
    print("       不能标定成本模型，不能推出'变浅更省时/更差'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
