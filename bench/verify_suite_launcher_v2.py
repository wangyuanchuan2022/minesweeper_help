# -*- coding: utf-8 -*-
"""校验 v2 启动器：硬门线、独立运行目录、不覆盖、参数齐全。

检查项：
  G1 编码/换行：纯 ASCII + CRLF
  G2 引用的脚本都存在（排除 rem 注释）
  G3 硬门线存在：S0/S1/S2 失败会 goto :gate_failed
  G4 下游实验失败**不**跳门（S3-S7 无 gate 跳转）
  G5 LAST_RC 确实在 :stage 里被赋值（否则门线恒真/恒假）
  G6 运行目录带时间戳（不覆盖旧结果）
  G7 manifest 记录被调用
  G8 各脚本 --help 参数可用

用法: python bench/verify_suite_launcher_v2.py
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
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✔' if cond else '✘'} {name}"
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    print("=" * 96)
    print(f"校验 v2 启动器：{LAUNCHER}")
    print("=" * 96)

    raw = open(os.path.join(ROOT, LAUNCHER), "rb").read()
    s = raw.decode("ascii", errors="replace")
    # 去掉 rem 注释行，只看可执行行
    exec_text = "\n".join(ln for ln in s.split("\n")
                          if not ln.strip().lower().startswith("rem"))

    # ---- G1 编码 ----
    print("\n[G1] 编码/换行")
    non_ascii = sum(1 for b in raw if b > 127)
    lf_only = len(re.findall(r"(?<!\r)\n", s))
    check("纯 ASCII", non_ascii == 0, f"{non_ascii} 个非 ASCII 字节")
    check("全 CRLF", lf_only == 0, f"{lf_only} 个 LF-only")

    # ---- G2 脚本存在 ----
    print("\n[G2] 引用的脚本")
    refs = sorted(set(re.findall(r"bench\\+([A-Za-z0-9_]+\.py)", exec_text)))
    for r in refs:
        rel = os.path.join("bench", r)
        ok = os.path.exists(rel)
        print(f"    {'✔' if ok else '✘'} {rel}")
        if not ok:
            FAILS.append(f"缺 {rel}")
    check("全部脚本存在", all(os.path.exists(os.path.join("bench", r))
                              for r in refs), f"{len(refs)} 个")

    # ---- G3 硬门线 ----
    print("\n[G3] 硬门线（S0/S1/S2 失败必须停）")
    gate_jumps = re.findall(r"if not \"!LAST_RC!\"==\"0\" goto :gate_failed",
                            s)
    # 门线数量 = S0(1) + S1(6 个脚本) + S2(2 个) = 9，逐条对应
    gate_stages = ["S0", "S1a", "S1b", "S1c", "S1d", "S1e", "S1f",
                   "S1g", "S2a", "S2b"]
    check("每个门线阶段都有跳转", len(gate_jumps) == len(gate_stages),
          f"{len(gate_jumps)} 处，期望 {len(gate_stages)}")
    for gs in gate_stages:
        # 该阶段的 call 之后必须紧跟 gate 跳转
        pat = re.compile(r"call :stage " + gs + r"[^\n]*\n[^\n]*LAST_RC[^\n]*"
                         r"goto :gate_failed")
        ok = bool(pat.search(s))
        print(f"    {'✔' if ok else '✘'} {gs} 有门线跳转")
        if not ok:
            FAILS.append(f"{gs} 缺门线")
    check("gate_failed 标签存在", ":gate_failed" in s)

    # 门线只能出现在 S3 之前
    idx_s3 = s.find("[S3]")
    idx_first_exp = s.find('call :stage S3')
    gate_after_s3 = s[idx_first_exp:].count("goto :gate_failed") \
        if idx_first_exp > 0 else 99
    check("S3 之后无 gate 跳转（下游独立）", gate_after_s3 == 0,
          f"S3 之后有 {gate_after_s3} 处")

    # ---- G4 下游失败不跳门 ----
    print("\n[G4] 下游实验 S3-S7")
    for sid in ("S3", "S4", "S5", "S6", "S7"):
        present = f"call :stage {sid} " in s
        check(f"{sid} 存在", present)

    # ---- G5 LAST_RC 赋值 ----
    print("\n[G5] 门线变量确实被赋值")
    check(":stage 里赋值 LAST_RC", "set LAST_RC=!ERRORLEVEL!" in s)
    check("初始化为 0", "set LAST_RC=0" in s)

    # ---- G6 运行目录时间戳 ----
    print("\n[G6] 结果目录不覆盖")
    check("使用时间戳目录", "set RUN=state\\ab\\runs\\!STAMP!" in s)
    check("日志也用时间戳", "set OUT=state\\suite\\!STAMP!" in s)
    check("mkdir 运行目录", 'mkdir "!RUN!"' in s)

    # ---- G7 manifest ----
    print("\n[G7] 代码版本清单")
    check("调用 record_run_manifest", "record_run_manifest.py" in s)
    check("manifest 脚本存在",
          os.path.exists("bench/record_run_manifest.py"))

    # ---- G8 参数可用 ----
    print("\n[G8] 脚本参数可用性")
    expects = {
        "bench/smoke_all_checks.py": [],
        "bench/verify_first_click_wiring.py": [],
        "bench/verify_flood_chain.py": [],
        "bench/verify_probe_chain.py": [],
        "bench/verify_endgame_safety.py": [],
        "bench/verify_expand_score_degenerate.py": [],
        "bench/verify_seed_determinism.py": ["--games"],
        "bench/verify_workers_determinism.py": ["--games"],
        "bench/rule_gap.py": ["--games", "--seed", "--out"],
        "bench/analyze_first_move_quality.py": ["--games", "--out"],
        "bench/measure_endgame_stratified.py": ["--games", "--seed",
                                          "--out"],
        "bench/diag_pm_coverage.py": ["--games"],
        "bench/atomic_decision_events.py": ["--selftest"],
        "bench/ab_first_click.py": ["--games", "--seed", "--out"],
        "bench/ab_tiebreak.py": ["--games", "--seed", "--arms", "--label"],
        "bench/record_run_manifest.py": ["--out"],
    }
    for script, flags in expects.items():
        if not os.path.exists(script):
            check(f"{script} 存在", False)
            continue
        try:
            p = subprocess.run([PY, "-X", "utf8", script, "--help"],
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=120, encoding="utf-8",
                               errors="replace")
        except subprocess.TimeoutExpired:
            check(f"{script} --help", False, "超时")
            continue
        if p.returncode != 0:
            check(f"{script} --help", False, f"exit={p.returncode}")
            continue
        missing = [f for f in flags if f not in (p.stdout or "")]
        check(f"{os.path.basename(script)} 参数", not missing,
              f"缺 {missing}" if missing else "")

    print("\n" + "=" * 96)
    if FAILS:
        print(f"✘ {len(FAILS)} 项未通过：{FAILS}")
        return 1
    print("✅ v2 启动器校验通过：")
    print("   · 纯 ASCII + CRLF")
    print("   · 脚本齐全、参数可用")
    print("   · S0/S1/S2 为硬门线，失败即停")
    print("   · S3-S7 下游独立，单个失败不阻断")
    print("   · 结果写入带时间戳目录，不覆盖历史")
    print("   · 记录 git 版本与关键文件指纹")
    return 0


if __name__ == "__main__":
    sys.exit(main())
