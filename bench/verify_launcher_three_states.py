# -*- coding: utf-8 -*-
"""受控验证启动器：成功 / 普通失败 / 中断 三态必须区分正确。

=================== 为什么必须做 ====================
上一轮实跑暴露：S3 的 JSON 已写入、日志含 KeyboardInterrupt、
启动器却跳到 gate_failed。三类事实并存说明判定逻辑不可靠。

=================== 做法 ====================
用**缩放副本**（把 --games 改小、输出目录隔离），分别注入：
  T1 正常：所有阶段应 ok，退出码 0
  T2 普通失败：让 S5 指向一个必失败脚本 → 应记 FAILED，但**不跳** interrupted
  T3 中断：让 S5 指向一个抛 KeyboardInterrupt 的脚本
         → 应识别 INTERRUPTED、**终止整套**（后续 S6/S7 不执行）、退出码 2

检查项：
  · exit_codes.txt 记录真实退出码
  · stage_done.txt 有对应标记
  · 中断时后续阶段日志**不存在**

用法: python bench/verify_launcher_three_states.py
"""
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
SRC = "run_winrate_suite.cmd"
PROBE_DIR = "bench/_probe_scripts"
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✔' if cond else '✘'} {name}"
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_probes():
    os.makedirs(PROBE_DIR, exist_ok=True)
    with open(os.path.join(PROBE_DIR, "always_fail.py"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("import sys\nprint('intentional fail')\nsys.exit(3)\n")
    with open(os.path.join(PROBE_DIR, "interrupt.py"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("import sys\nprint('about to interrupt')\n"
                "sys.stdout.flush()\nraise KeyboardInterrupt\n")


def build_variant(tag, inject=None):
    """生成缩放副本；inject=('S5', 'bench/_probe_scripts/xxx.py') 替换某阶段。"""
    s = open(SRC, "rb").read().decode("ascii")
    s = s.replace("--games 600", "--games 3")
    s = s.replace("--games 1000", "--games 3")
    s = s.replace("--games 400", "--games 3")
    s = s.replace("--games 500", "--games 3")
    s = s.replace("--games 60", "--games 2")
    s = s.replace("set RUN=state\\ab\\runs\\!STAMP!",
                  f"set RUN=state\\ab\\_3s_{tag}\\!STAMP!")
    s = s.replace("set OUT=state\\suite\\!STAMP!",
                  f"set OUT=state\\suite\\_3s_{tag}\\!STAMP!")
    if inject:
        sid, newscript = inject
        # 找到 call :stage <sid> 那行，替换脚本名并清空参数与期望产物
        lines = s.split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith(f"call :stage {sid} "):
                parts = ln.split()
                # 保留 id，替换脚本，去掉 extra/expect 参数
                lines[i] = f"call :stage {sid} {newscript} \"\" {sid}_probe.log"
                break
        s = "\n".join(lines)
    path = f"_3s_{tag}.cmd"
    with open(path, "wb") as f:
        f.write(s.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
    return path


def run_variant(path):
    p = subprocess.run(["cmd", "/c", path], cwd=ROOT, capture_output=True,
                       text=True, timeout=7200, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    return (p.stdout or "") + (p.stderr or ""), p.returncode


def collect(tag):
    base = os.path.join(ROOT, "state", "suite", f"_3s_{tag}")
    if not os.path.isdir(base):
        return None, [], [], []
    dirs = [os.path.join(base, d) for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))]
    if not dirs:
        return None, [], [], []
    d = dirs[0]
    logs = sorted(os.listdir(d))
    ec = os.path.join(d, "exit_codes.txt")
    sd = os.path.join(d, "stage_done.txt")
    exit_codes = (open(ec, encoding="utf-8").read().split()
                  if os.path.exists(ec) else [])
    stage_marks = (open(sd, encoding="utf-8").read().split()
                   if os.path.exists(sd) else [])
    return d, logs, exit_codes, stage_marks


def cleanup(tag, cmd_path):
    for p in (cmd_path,
              os.path.join("state", "suite", f"_3s_{tag}"),
              os.path.join("state", "ab", f"_3s_{tag}")):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)


def main():
    print("=" * 100)
    print("启动器三态受控验证（成功 / 普通失败 / 中断）")
    print("=" * 100)
    make_probes()

    # ---------- T1 正常 ----------
    print("\n[T1] 正常：期望全部 ok、退出码 0")
    v = build_variant("ok")
    out, rc = run_variant(v)
    d, logs, ecs, marks = collect("ok")
    print(f"    退出码 {rc}，日志 {len(logs)} 个")
    check("T1 退出码 0", rc == 0, f"实得 {rc}")
    check("T1 无 INTERRUPTED 提示", "SUITE INTERRUPTED" not in out)
    check("T1 exit_codes.txt 存在", bool(ecs), f"{len(ecs)} 条")
    check("T1 stage_done.txt 有 OK 标记",
          any("_OK" in m for m in marks), f"{marks[:4]}")
    cleanup("ok", v)

    # ---------- T2 普通失败 ----------
    print("\n[T2] 普通失败：S5 换成 exit 3 的脚本")
    print("     期望：记 FAILED、不跳 interrupted、后续阶段仍执行")
    v = build_variant("fail", inject=("S5", "bench\\_probe_scripts\\always_fail.py"))
    out, rc = run_variant(v)
    d, logs, ecs, marks = collect("fail")
    print(f"    退出码 {rc}，日志 {len(logs)} 个")
    check("T2 未误报 INTERRUPTED", "SUITE INTERRUPTED" not in out)
    check("T2 记录了失败", "FAILED exit=" in out or "FAILED" in out)
    check("T2 后续阶段仍执行（S6 日志存在）",
          any(x.startswith("S6") for x in logs), f"{logs[-4:]}")
    check("T2 exit_codes 记录了非零码",
          any(not x.endswith("=0") for x in ecs), f"{ecs[-3:]}")
    cleanup("fail", v)

    # ---------- T3 中断 ----------
    print("\n[T3] 中断：S5 换成抛 KeyboardInterrupt 的脚本")
    print("     期望：识别 INTERRUPTED、终止整套、S6/S7 不执行、退出码 2")
    v = build_variant("int",
                      inject=("S5", "bench\\_probe_scripts\\interrupt.py"))
    out, rc = run_variant(v)
    d, logs, ecs, marks = collect("int")
    print(f"    退出码 {rc}，日志 {len(logs)} 个")
    check("T3 识别为中断", "SUITE INTERRUPTED" in out)
    check("T3 退出码 2", rc == 2, f"实得 {rc}")
    check("T3 后续 S6 未执行",
          not any(x.startswith("S6") for x in logs), f"{logs[-4:]}")
    check("T3 stage_done 有 INTERRUPTED 标记",
          any("INTERRUPTED" in m for m in marks), f"{marks[-3:]}")
    cleanup("int", v)

    # 清理探针
    shutil.rmtree(PROBE_DIR, ignore_errors=True)

    print("\n" + "=" * 100)
    if FAILS:
        print(f"✘ 未通过 {len(FAILS)} 项：{FAILS}")
        return 1
    print("✅ 三态区分正确：")
    print("   · 成功     → 全部 ok、exit 0、stage_done 有 _OK")
    print("   · 普通失败 → 记 FAILED、不跳 interrupted、后续阶段继续")
    print("   · 中断     → 识别 INTERRUPTED、终止整套、后续不执行、exit 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
