# -*- coding: utf-8 -*-
"""校验 run_winrate_suite.cmd 引用的脚本与参数是否都真实可用。

目的：用户双击前，先确认启动器不会因为"路径写错 / 参数不被支持"而中途失败。
不实际跑长任务，只做静态校验 + 参数 --help 探测。

用法: python bench/verify_suite_launcher.py
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

LAUNCHER = "run_winrate_suite.cmd"


def main():
    print("=" * 96)
    print(f"校验启动器：{LAUNCHER}")
    print("=" * 96)

    path = os.path.join(ROOT, LAUNCHER)
    if not os.path.exists(path):
        print(f"✘ 启动器不存在：{path}")
        return 1

    raw = open(path, "rb").read()
    fails = []

    # ---- 1) 编码与换行 ----
    non_ascii = sum(1 for b in raw if b > 127)
    s = raw.decode("ascii", errors="replace")
    crlf = s.count("\r\n")
    lf_only = len(re.findall(r"(?<!\r)\n", s))
    print(f"\n[1] 编码/换行")
    print(f"    非 ASCII 字节 {non_ascii}  {'✔' if non_ascii == 0 else '✘'}")
    print(f"    CRLF {crlf}  LF-only {lf_only}  "
          f"{'✔' if lf_only == 0 else '✘'}")
    if non_ascii or lf_only:
        fails.append("编码/换行")

    # ---- 2) 引用的脚本是否存在 ----
    print(f"\n[2] 引用的 python 脚本")
    # 只从**可执行行**里提取（跳过 rem 注释），否则注释里的示例路径
    # 如 "bench\x.py" 会被误当成真实引用（实测踩到）。
    exec_lines = [ln for ln in s.split("\n")
                  if not ln.strip().lower().startswith("rem")]
    exec_text = "\n".join(exec_lines)
    refs = re.findall(r"bench\\+([A-Za-z0-9_]+\.py)", exec_text)
    seen = []
    for r in refs:
        rel = os.path.join("bench", r)
        if rel in seen:
            continue
        seen.append(rel)
        ok = os.path.exists(rel)
        print(f"    {'✔' if ok else '✘'} {rel}")
        if not ok:
            fails.append(f"缺文件 {rel}")
    print(f"    共 {len(seen)} 个脚本")

    # ---- 3) 引用的输出目录可在脚本内自动创建？ ----
    print(f"\n[3] 输出目录")
    outdir = "state/suite"
    print(f"    {outdir}  {'存在' if os.path.isdir(outdir) else '不存在（启动器会 mkdir）'}")

    # ---- 4) 逐个脚本 --help 探测（确认能 import、参数可用）----
    print(f"\n[4] 脚本可导入性与参数支持（--help 探测）")
    expects = {
        "bench/smoke_all_checks.py": [],
        "bench/verify_flood_chain.py": [],
        "bench/verify_probe_chain.py": [],
        "bench/verify_endgame_safety.py": [],
        "bench/verify_expand_score_degenerate.py": [],
        "bench/verify_seed_determinism.py": ["--games"],
        "bench/verify_workers_determinism.py": ["--games"],
        "bench/rule_gap.py": ["--games", "--seed", "--out"],
        "bench/analyze_first_move_quality.py": ["--games", "--seed", "--out"],
        "bench/analyze_endgame_quality.py": ["--games", "--seed", "--out"],
        "bench/ab_tiebreak.py": ["--games", "--seed", "--arms", "--label"],
    }
    for script, flags in expects.items():
        if not os.path.exists(script):
            print(f"    ✘ {script} 不存在")
            fails.append(f"缺 {script}")
            continue
        try:
            p = subprocess.run([PY, "-X", "utf8", script, "--help"],
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            print(f"    ✘ {script} --help 超时（可能有副作用）")
            fails.append(f"{script} --help 超时")
            continue
        if p.returncode != 0:
            print(f"    ✘ {script} --help 失败 exit={p.returncode}")
            print(f"       {(p.stderr or '')[-300:]}")
            fails.append(f"{script} --help 失败")
            continue
        help_txt = p.stdout or ""
        missing = [f for f in flags if f not in help_txt]
        if missing:
            print(f"    ✘ {script} 缺参数 {missing}")
            fails.append(f"{script} 缺参数 {missing}")
        else:
            print(f"    ✔ {script}")

    # ---- 5) 汇总 ----
    print("\n" + "=" * 96)
    if fails:
        print(f"✘ 校验失败 {len(fails)} 项：")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("✅ 启动器校验通过：编码正确、脚本齐全、参数可用。")
    print("   ⇒ 可以双击 run_winrate_suite.cmd 放心跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
