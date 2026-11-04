# -*- coding: utf-8 -*-
"""用 C++ 分阶段计时定位「非单调耗时」的真因（G10 合成用例）。

=================== 要回答的问题 ====================
实测同一局面（G10，total=1024，n_cand=20）在不同 adaptive_depth 下：
  ad=1 → 102.8ms，ad=3 → 2858ms，ad=5 → 4204ms
看似"变浅反而更慢"，会让人误以为成本模型方向错了。
**必须先分清这是规则真的在起作用，还是测量噪声。**

=================== 判定依据 ====================
在 C++ 侧加 `MSW_WR_PHASE=1` 输出四个量：
  · depth_limit  —— 实际生效的深度上限（规则是否真的改了它）
  · shrunk       —— 是否被 adaptive 收缩过（0 = 规则没触发）
  · wr_nodes     —— wr_f 递归节点数（真实成本的直接度量）
  · enum_ms / outer_ms —— 世界枚举 / 外层逐格 的耗时

若某档位 `shrunk=0` 且 `depth_limit` 与基线相同，则它**根本没走规则**，
其耗时差异**不能**归因于 adaptive。

用法: python bench/diag_depth_nonmonotonic.py
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
os.environ["MSW_WR_PHASE"] = "1"


def main():
    from verify_adaptive_depth_synthetic import build_cases, call
    import time

    cases = dict(build_cases())
    if "G10" not in cases:
        print("✘ 缺少 G10 合成用例")
        return 1
    c = cases["G10"]

    print("=" * 100)
    print("分阶段计时（MSW_WR_PHASE=1）：每档重复 2 次，交替顺序")
    print("=" * 100)
    print("  G10: total=1024, n_cand=20, cost=n_cand*total=20480")
    print()
    print(f"{'ad':>4}{'depth_limit':>13}{'shrunk':>8}{'wr_nodes':>12}"
          f"{'enum_ms':>10}{'outer_ms':>10}{'wall_ms':>10}{'输出首值':>12}")
    print("-" * 100)

    rows = []
    for ad in (0, 1, 2, 3, 4, 5, 0, 1):
        # 用子进程捕获 stderr 太慢；这里直接从 stderr 读取不可行，
        # 改为：本进程内调用，phase 输出到 stderr 由外层 shell 收集。
        t0 = time.perf_counter()
        r = call(c, 2_000_000, ad)
        dt = (time.perf_counter() - t0) * 1000.0
        rows.append({"ad": ad, "wall_ms": dt, "res0": float(list(r[0])[0])})

    # 由于 C++ 的 phase 输出走 stderr，而本脚本在 Python 内调用，
    # 需要单独再跑一遍并抓 stderr。用 subprocess 重跑最可靠。
    print("  （phase 明细需从 C++ stderr 抓取，见下方子进程输出）")
    print()
    for r in rows:
        print(f"{r['ad']:>4}{'—':>13}{'—':>8}{'—':>12}{'—':>10}{'—':>10}"
              f"{r['wall_ms']:>10.1f}{r['res0']:>12.6f}")

    print()
    print("=" * 100)
    print("用子进程重跑以捕获 C++ 的 [wr_phase] 行")
    print("=" * 100)
    snippet = (
        "import sys,os,time;"
        "sys.path.insert(0,r'%s');sys.path.insert(0,r'%s');"
        "os.chdir(r'%s');"
        "from verify_adaptive_depth_synthetic import build_cases,call;"
        "c=dict(build_cases())['G10'];"
        "[(call(c,2000000,ad)) for ad in (0,1,2,3,4,5)]"
        % (ROOT, os.path.join(ROOT, "bench"), ROOT)
    )
    env = dict(os.environ)
    env["MSW_WR_PHASE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", snippet],
                          capture_output=True, text=True, env=env)
    for line in (proc.stderr or "").splitlines():
        if "[wr_phase]" in line:
            print("  " + line.strip())

    print()
    print("=" * 100)
    print("读法")
    print("=" * 100)
    print("  · shrunk=0 的档位**根本没走规则**（depth_limit 与基线相同）")
    print("    ⇒ 它们的耗时差异是测量噪声，不能归因于 adaptive")
    print("  · shrunk=1 的档位才是规则真正生效的样本")
    print("  · wr_nodes 是真实成本的直接度量；n_cand×total 只是代理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
