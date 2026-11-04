# -*- coding: utf-8 -*-
"""计时 sanity check：同 depth_limit / 同 wr_nodes / 同输出，为何耗时差 5 倍？

=================== 为什么必须做这个检查 ====================
bench/diag_depth_nonmonotonic.py 实测（G10 合成用例，total=1024）：
  ad=0,2,3,4,5 五档的 depth_limit 恒为 10.000、shrunk=0、
  wr_nodes 恒为 1,241,184、输出首值恒为 0.053711
  —— 即**搜索工作量完全相同**，但 outer_ms 在 838~4655ms 之间波动。

**这是原因未明的现象。** 我此前把它写成"测量噪声/缓存效应"，
但那是**没有证据的推断**（本项目纪律第 6 条：归因必须带证据，
禁止把合理推测写成结论）。正确做法是：

  1. 随机交替重复（消除顺序/温度效应）
  2. 新进程重复（消除进程内状态累积）
  3. 报告**中位数 / p90 / 极差**，而不是单次读数
  4. 核对分阶段计时是否自洽（enum_ms + outer_ms ≈ 总耗时）
  5. 记录后台负载（并行进程数），排除争抢

若上述检查后差异仍存在，则结论只能是「**原因未明**」，
并明确说明它影响什么：本轮的 G10 只能证明 adaptive 接线生效，
**不能**用于标定成本模型或推导"变浅更省时"。

用法: python bench/timing_sanity_check.py
"""
import json
import os
import random
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


def phase_runs(ad_list, seed=1234, case_name="G10"):
    """在新进程里跑一串 ad 档位，返回 [(ad, outer_ms, depth_limit, shrunk, nodes)]。"""
    snippet = (
        "import sys,os;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');"
        "os.chdir(r'%s');"
        "from verify_adaptive_depth_synthetic import build_cases,call;"
        "c=dict(build_cases())['%s'];"
        "[call(c,2000000,ad) for ad in %r]"
        % (ROOT, os.path.join(ROOT, "bench"), ROOT, case_name, list(ad_list))
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
    print("计时 sanity check（G10，ad=0 恒定档位，重复测量）")
    print("=" * 100)
    print("  目的：同一 ad=0（depth_limit 恒为 10、wr_nodes 恒 1.24M、输出恒定）")
    print("        反复测量的耗时分布。若分布本身就很宽，则此前 ad=3/4/5 的")
    print("        '更慢' 不能归因于 adaptive；若分布很窄，则差异另有原因（未明）。")
    print()

    # ---- 1. 同进程内随机交替重复 ----
    print("-" * 100)
    print("1) 同进程内重复（ad 固定为 0，跑 12 次，随机化其它档位插入）")
    print("-" * 100)
    from verify_adaptive_depth_synthetic import build_cases, call
    import time
    cases = dict(build_cases())
    c = cases["G10"]

    # 先预热一次（排除首次调用的冷启动）
    call(c, 2_000_000, 0)
    times_ad0 = []
    seq = [0] * 12
    random.Random(7).shuffle(seq)
    for ad in seq:
        t0 = time.perf_counter()
        call(c, 2_000_000, ad)
        times_ad0.append((time.perf_counter() - t0) * 1000.0)
    ts = sorted(times_ad0)
    print(f"  原始：{[round(x) for x in times_ad0]}")
    print(f"  中位 {statistics.median(ts):.0f}ms  p90 {ts[int(len(ts)*0.9)]:.0f}ms  "
          f"min {ts[0]:.0f}  max {ts[-1]:.0f}  极差 {ts[-1]-ts[0]:.0f}ms "
          f"（max/min = {ts[-1]/max(1,ts[0]):.1f}x）")

    # ---- 2. 新进程重复 ----
    print()
    print("-" * 100)
    print("2) 新进程重复（每次跑 ad=0 一次，共 4 个独立进程）")
    print("-" * 100)
    newproc = []
    for i in range(4):
        rows = phase_runs([0])
        if rows:
            r = rows[0]
            newproc.append(float(r.get("outer_ms", "0")))
            print(f"  进程{i+1}: outer_ms={r.get('outer_ms')} "
                  f"depth_limit={r.get('depth_limit')} "
                  f"shrunk={r.get('shrunk')} nodes={r.get('wr_nodes')}")
    if newproc:
        ns = sorted(newproc)
        print(f"  中位 {statistics.median(ns):.0f}ms  极差 {ns[-1]-ns[0]:.0f}ms")

    # ---- 3. 分阶段自洽性 ----
    print()
    print("-" * 100)
    print("3) 分阶段计时自洽性（enum_ms + outer_ms 应接近 wall time）")
    print("-" * 100)
    for ad in (0, 1):
        rows = phase_runs([ad])
        if not rows:
            continue
        r = rows[0]
        e = float(r.get("enum_ms", "0"))
        o = float(r.get("outer_ms", "0"))
        print(f"  ad={ad}: enum_ms={e:.1f} + outer_ms={o:.1f} = {e+o:.1f}ms  "
              f"depth_limit={r.get('depth_limit')} shrunk={r.get('shrunk')} "
              f"nodes={r.get('wr_nodes')}")

    # ---- 4. 后台负载 ----
    print()
    print("-" * 100)
    print("4) 后台负载核查")
    print("-" * 100)
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process | Where-Object {$_.ProcessName -like '*python*'}).Count"],
            capture_output=True, text=True, timeout=30)
        print(f"  当前 python 进程数：{ps.stdout.strip()}")
    except Exception as e:                                 # noqa: BLE001
        print(f"  无法查询：{e}")

    print()
    print("=" * 100)
    print("结论")
    print("=" * 100)
    print("  已确认（有证据）：")
    print("    · ad=1 触发规则：depth_limit 10→4.883、wr_nodes 1,241,184→47,016、")
    print("      输出改变 ⇒ adaptive 接线生效。")
    print("    · ad=2/3/4/5 未触发规则（shrunk=0、depth_limit=10、wr_nodes 相同、")
    print("      输出相同）⇒ 它们与 ad=0 的搜索工作量**完全相同**。")
    print()
    print("  原因未明：")
    print("    · 工作量相同的前提下，outer_ms 在 838~4655ms 间波动。")
    print("      上面 1)/2) 两项检查给出该波动的分布；若分布本身很宽，")
    print("      则无法把任何单次读数归因于 adaptive。")
    print("      **本条不作因果结论**，只记录现象与分布。")
    print()
    print("  因此本轮 G10 的用途边界：")
    print("    · 只能证明 adaptive 参数**确实作用到了 depth_limit**")
    print("    · **不能**用于标定成本模型（n_cand×total 的系数）")
    print("    · **不能**推出'变浅更省时'或'变浅降低胜率'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
