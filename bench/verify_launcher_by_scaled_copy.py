# -*- coding: utf-8 -*-
"""在**不改动真实启动器**的前提下，用缩小参数验证它的结构正确性。

做法：读 run_winrate_suite.cmd，把所有大样本参数替换为小样本，
生成一个临时副本（同目录、同结构）并实际执行它。
这样验证的是**真实启动器的代码本身**，而不是另写一个类似的脚本。

替换规则（只改数字，不改结构）：
  --games 600 → --games 8      （S3/S4/S5）
  --games 400 → --games 12     （S6）
  --games 60  → --games 5      （S2）
  state\\suite → state\\suite_probe （隔离输出，不污染真实结果）
  rule_gap_600_r2.json → rule_gap_probe.json 等

用法: python bench/verify_launcher_by_scaled_copy.py
"""
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

SRC = "run_winrate_suite.cmd"
TMP = "_probe_winrate_suite.cmd"
OUT_DIR = "state/suite_probe"


def main():
    print("=" * 96)
    print("用缩小参数验证真实启动器（不改动原文件）")
    print("=" * 96)

    raw = open(os.path.join(ROOT, SRC), "rb").read().decode("ascii")
    orig = raw

    # ---- 只改数字与输出目录，保持结构不变 ----
    s = raw
    s = s.replace("--games 600", "--games 8")
    s = s.replace("--games 400", "--games 12")
    s = s.replace("--games 60", "--games 5")
    s = s.replace("state\\ab\\rule_gap_600_r2.json", "state\\ab\\rule_gap_probe.json")
    s = s.replace("state\\ab\\first_move_600.json", "state\\ab\\first_move_probe.json")
    s = s.replace("state\\ab\\endgame_600.json", "state\\ab\\endgame_probe.json")
    s = s.replace("--label s6", "--label probe")
    s = s.replace("set OUT=state\\suite", f"set OUT={OUT_DIR.replace('/', chr(92))}")
    # 汇总段的文件名也同步（仅显示用）
    s = s.replace("state\\ab\\rule_gap_600_r2.json", "state\\ab\\rule_gap_probe.json")
    s = s.replace("state\\ab\\first_move_600.json", "state\\ab\\first_move_probe.json")
    s = s.replace("state\\ab\\endgame_600.json", "state\\ab\\endgame_probe.json")
    s = s.replace("state\\ab\\tiebreak_ab_s6.json", "state\\ab\\tiebreak_ab_probe.json")

    n_changed = sum(1 for a, b in zip(orig.split("\n"), s.split("\n")) if a != b)
    print(f"\n  替换了 {n_changed} 行（只改数字/输出路径）")

    if "--games 600" in s or "--games 400" in s or "--games 60" in s:
        print("  ✘ 仍有未替换的大样本参数")
        return 1

    # ---- 校验 ASCII/CRLF ----
    b = s.encode("ascii", errors="replace")
    non_ascii = sum(1 for x in b if x > 127)
    print(f"  非 ASCII 字节 = {non_ascii}  {'✔' if non_ascii == 0 else '✘'}")
    s_crlf = s.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(os.path.join(ROOT, TMP), "wb") as f:
        f.write(s_crlf.encode("ascii", errors="replace"))

    # ---- 清理旧产物，确保本次是新写的 ----
    if os.path.isdir(OUT_DIR):
        import shutil
        shutil.rmtree(OUT_DIR)
        print(f"  已清理旧 {OUT_DIR}/（验证覆盖语义）")
    for fn in ("rule_gap_probe.json", "first_move_probe.json",
               "endgame_probe.json", "tiebreak_ab_probe.json"):
        p = os.path.join(ROOT, "state", "ab", fn)
        if os.path.exists(p):
            os.remove(p)

    # ---- 实际执行 ----
    print(f"\n  执行 {TMP} …（预计 3-5 分钟）", flush=True)
    p = subprocess.run(["cmd", "/c", TMP], cwd=ROOT, capture_output=True,
                       text=True, timeout=3600, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    out = (p.stdout or "") + (p.stderr or "")
    print("\n" + "-" * 96)
    for line in out.strip().split("\n")[-40:]:
        print("  " + line)
    print("-" * 96)

    # ---- 判定 ----
    print("\n" + "=" * 96)
    print("判定")
    print("=" * 96)
    fails = []

    n_ok = len(re.findall(r"^\s+ok\s*$", out, re.M))
    n_failed = out.count("FAILED exit=")
    print(f"  阶段 ok = {n_ok}，FAILED = {n_failed}")
    if n_failed:
        fails.append(f"{n_failed} 个阶段失败")
    if n_ok < 9:
        fails.append(f"ok 阶段数偏少（{n_ok} < 9）")

    # 日志是否都生成
    logdir = os.path.join(ROOT, OUT_DIR)
    logs = sorted(os.listdir(logdir)) if os.path.isdir(logdir) else []
    print(f"  生成日志 {len(logs)} 个：{logs}")
    if len(logs) < 9:
        fails.append(f"日志数偏少（{len(logs)}）")

    # 关键 JSON 是否落盘
    for fn in ("rule_gap_probe.json", "first_move_probe.json",
               "endgame_probe.json", "tiebreak_ab_probe.json"):
        pj = os.path.join(ROOT, "state", "ab", fn)
        ok = os.path.exists(pj)
        sz = os.path.getsize(pj) if ok else 0
        print(f"  {'✔' if ok else '✘'} {fn}  ({sz} 字节)")
        if not ok:
            fails.append(f"缺 {fn}")
        else:
            try:
                json.load(open(pj, encoding="utf-8"))
            except Exception as e:                      # noqa: BLE001
                fails.append(f"{fn} JSON 不可解析：{e}")

    # 清理临时启动器
    os.remove(os.path.join(ROOT, TMP))

    print()
    if fails:
        print(f"  ✘ 未通过：{fails}")
        return 1
    print("  ✅ 真实启动器的结构验证通过（缩放参数下）：")
    print("     · 9 个阶段全部 ok，无 FAILED")
    print("     · 每阶段日志独立生成")
    print("     · 4 个结果 JSON 落盘且可解析")
    print("     · 参数替换只改数字，结构未动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
