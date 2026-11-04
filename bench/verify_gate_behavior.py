# -*- coding: utf-8 -*-
"""验证 v2 启动器的**硬门线真的会停**（这是 v2 最关键的行为变更）。

做法：复制启动器为临时文件，把其中一个门线阶段（S1b verify_flood_chain）
换成一个必定失败的脚本，然后实跑。期望：
  · 打印 "HARD GATE FAILED"
  · **不**执行 S3–S7（日志里没有 S3_*.log 等）
  · 退出码非 0
  · 门线失败的日志被保留

再跑一次**正常**的缩放副本，确认门线全过时下游会执行。
两次都要清理产物。

用法: python bench/verify_gate_behavior.py
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
SRC = "run_winrate_suite.cmd"

FAKE_FAIL = "_gate_fail_probe.py"


def make_scaled(orig, out_dir_marker, break_gate):
    """生成缩放副本（小样本 + 隔离输出目录）。"""
    s = orig
    s = s.replace("--games 600", "--games 5")
    s = s.replace("--games 1000", "--games 5")
    s = s.replace("--games 400", "--games 5")
    s = s.replace("--games 60", "--games 3")
    s = s.replace("set RUN=state\\ab\\runs\\!STAMP!",
                  f"set RUN=state\\ab\\_gatetest_{out_dir_marker}\\!STAMP!")
    s = s.replace("set OUT=state\\suite\\!STAMP!",
                  f"set OUT=state\\suite\\_gatetest_{out_dir_marker}\\!STAMP!")
    if break_gate:
        # 把 S1b 的脚本换成一个必然失败的探针
        s = s.replace(
            "call :stage S1b bench\\verify_flood_chain.py \"\" S1b_flood_chain.log",
            f"call :stage S1b bench\\{FAKE_FAIL} \"\" S1b_flood_chain.log")
    return s


def run_variant(s, tag):
    path = f"_gatetest_{tag}.cmd"
    s_crlf = s.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(os.path.join(ROOT, path), "wb") as f:
        f.write(s_crlf.encode("ascii", errors="replace"))
    p = subprocess.run(["cmd", "/c", path], cwd=ROOT, capture_output=True,
                       text=True, timeout=3600, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    out = (p.stdout or "") + (p.stderr or "")
    os.remove(os.path.join(ROOT, path))
    return out, p.returncode


def find_log_dirs(marker):
    base = os.path.join(ROOT, "state", "suite", f"_gatetest_{marker}")
    if not os.path.isdir(base):
        return []
    dirs = [os.path.join(base, d) for d in os.listdir(base)]
    return [d for d in dirs if os.path.isdir(d)]


def main():
    print("=" * 96)
    print("验证 v2 硬门线行为")
    print("=" * 96)
    orig = open(os.path.join(ROOT, SRC), "rb").read().decode("ascii")
    fails = []

    # 创建必失败的探针
    probe = os.path.join(ROOT, "bench", FAKE_FAIL)
    with open(probe, "w", encoding="utf-8", newline="\n") as f:
        f.write('import sys\nprint("intentional failure")\nsys.exit(7)\n')

    try:
        # ---------- 情形 1：门线失败 → 必须停 ----------
        print("\n[1] 故意让 S1b（门线）失败，期望停在门线、不跑 S3-S7")
        s = make_scaled(orig, "gated", break_gate=True)
        out, rc = run_variant(s, "gated")
        logs = find_log_dirs("gated")
        names = sorted(os.listdir(logs[0])) if logs else []
        print(f"    退出码 {rc}")
        print(f"    日志 {len(names)} 个: {names}")

        gate_msg = "HARD GATE FAILED" in out
        ran_s3 = any(n.startswith("S3") for n in names)
        ran_s7 = any(n.startswith("S7") for n in names)
        kept_fail = any(n.startswith("S1b") for n in names)
        rc_nonzero = rc != 0

        print(f"    {'✔' if gate_msg else '✘'} 打印 HARD GATE FAILED")
        print(f"    {'✔' if not ran_s3 else '✘'} 未执行 S3（下游被跳过）")
        print(f"    {'✔' if not ran_s7 else '✘'} 未执行 S7")
        print(f"    {'✔' if kept_fail else '✘'} 门线失败日志被保留")
        print(f"    {'✔' if rc_nonzero else '✘'} 退出码非 0")
        for cond, label in ((gate_msg, "门线提示"),
                            (not ran_s3, "S3 被跳过"),
                            (not ran_s7, "S7 被跳过"),
                            (kept_fail, "失败日志保留"),
                            (rc_nonzero, "退出码非0")):
            if not cond:
                fails.append(f"[gated] {label}")

        # ---------- 情形 2：门线全过 → 下游要执行 ----------
        print("\n[2] 门线全过，期望 S1-S7 都执行")
        s2 = make_scaled(orig, "ok", break_gate=False)
        out2, rc2 = run_variant(s2, "ok")
        logs2 = find_log_dirs("ok")
        names2 = sorted(os.listdir(logs2[0])) if logs2 else []
        print(f"    退出码 {rc2}")
        print(f"    日志 {len(names2)} 个: {names2}")
        has_s3 = any(n.startswith("S3") for n in names2)
        has_s6 = any(n.startswith("S6") for n in names2)
        has_s7 = any(n.startswith("S7") for n in names2)
        print(f"    {'✔' if has_s3 else '✘'} 执行了 S3")
        print(f"    {'✔' if has_s6 else '✘'} 执行了 S6（首点位置实验）")
        print(f"    {'✔' if has_s7 else '✘'} 执行了 S7")
        for cond, label in ((has_s3, "S3 执行"), (has_s6, "S6 执行"),
                            (has_s7, "S7 执行")):
            if not cond:
                fails.append(f"[ok] {label}")

        # 情形 2 的产物里有 JSON 吗
        run_base = os.path.join(ROOT, "state", "ab", "_gatetest_ok")
        rdirs = []
        if os.path.isdir(run_base):
            rdirs = [os.path.join(run_base, d) for d in os.listdir(run_base)
                     if os.path.isdir(os.path.join(run_base, d))]
        if rdirs:
            jsons = sorted(f for f in os.listdir(rdirs[0])
                           if f.endswith(".json"))
            print(f"    结果 JSON {len(jsons)} 个: {jsons}")
            if "manifest.json" not in jsons:
                fails.append("[ok] 缺 manifest.json")
    finally:
        if os.path.exists(probe):
            os.remove(probe)
        # 清理测试目录
        for d in ("state/suite/_gatetest_gated", "state/suite/_gatetest_ok",
                  "state/ab/_gatetest_gated", "state/ab/_gatetest_ok"):
            p = os.path.join(ROOT, d)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)

    print("\n" + "=" * 96)
    if fails:
        print(f"✘ 未通过 {len(fails)} 项：{fails}")
        return 1
    print("✅ 硬门线行为验证通过：")
    print("   · 门线失败 → 打印 HARD GATE FAILED、退出码非 0、S3-S7 全部跳过")
    print("   · 门线失败日志保留，便于诊断")
    print("   · 门线全过 → S3-S7 全部执行，结果 JSON 写入时间戳目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
