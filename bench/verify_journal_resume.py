# -*- coding: utf-8 -*-
"""验证 merge_and_eval_cases.py 的断点续跑机制真的有效。

=================== 为什么必须验证 ====================
Advisor 指出：单点可跑数分钟，进程异常会浪费整批计算。
我加了 journal 逐点落盘 + 续跑，但**写了代码不等于它能工作**。
本项目已踩过多次"参数没接到生产路径"的坑（node_cap 扫描轴曾因没接线
而三档零触发，是假象）。

=================== 判定手段（可证伪）====================
用**受控的两阶段实验**：
  阶段 1：跑到一半人为中断（SIGKILL / 提前退出）
  阶段 2：重启，检查它是否**跳过已完成点**并继续

判据：
  · 阶段 1 结束时 journal 里应有 N>0 条 ok 记录
  · 阶段 2 启动时应打印「已完成 N 条」，且**不重算**这 N 个点
  · 阶段 2 完成后的总 row 数 = 阶段1 的 N + 阶段2 新算的 M

阴性对照：删除 journal 后重跑，应从头算（resumed=0）。

用法: python bench/verify_journal_resume.py
"""
import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OUT = os.path.join(ROOT, "state", "ab", "_verify_resume.json")
JOURNAL = OUT + ".journal.jsonl"


def clean():
    for p in (OUT, JOURNAL):
        if os.path.exists(p):
            os.remove(p)


def run_stage(limit, timeout=300, tag=""):
    """跑一个阶段，返回 (stdout+stderr, 是否超时)。"""
    cmd = [sys.executable, "-u", "bench/merge_and_eval_cases.py",
           "--max-worlds", "20000", "--limit", str(limit),
           "--out", OUT]
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # ⚠️ 不能依赖 text=True：Windows 默认用 GBK 解码子进程输出，
    # 而子进程输出含 UTF-8 中文 ⇒ UnicodeDecodeError（实测踩到）。
    # 改为捕获 bytes 后自己按 utf-8 + errors=replace 解码。
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              env=env, cwd=ROOT, timeout=timeout)
        so = (proc.stdout or b"").decode("utf-8", "replace")
        se = (proc.stderr or b"").decode("utf-8", "replace")
        return so + se, False
    except subprocess.TimeoutExpired as e:
        so = (e.stdout or b"")
        se = (e.stderr or b"")
        if isinstance(so, str):
            so = so.encode("utf-8", "replace")
        if isinstance(se, str):
            se = se.encode("utf-8", "replace")
        return so.decode("utf-8", "replace") + se.decode("utf-8", "replace"), True


def count_journal():
    if not os.path.exists(JOURNAL):
        return 0, 0
    ok = skip = 0
    with open(JOURNAL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("status") == "ok":
                ok += 1
            else:
                skip += 1
    return ok, skip


def main():
    print("=" * 96)
    print("验证断点续跑机制")
    print("=" * 96)
    print()

    clean()

    # ---- 阶段 1：跑少量点（限制 timeout 制造"中断"效果）----
    print("-" * 96)
    print("阶段 1：跑 --limit 12（timeout 90s，可能被中断）")
    print("-" * 96)
    out1, timeout1 = run_stage(12, timeout=90)
    ok1, skip1 = count_journal()
    print("  是否超时（= 中断）：%s" % timeout1)
    print("  journal：ok=%d skip=%d" % (ok1, skip1))
    for line in out1.splitlines():
        if "断点续跑" in line or "成功" in line or "合格点" in line:
            print("  | " + line.strip())

    if ok1 == 0:
        print()
        print("  ✘ 阶段 1 未产生任何 ok 记录 ⇒ 无法验证续跑。")
        print("    （可能是 timeout 太短，或脚本在枚举阶段就卡住）")
        return 1

    # ---- 阶段 2：重启，应跳过已完成点 ----
    print()
    print("-" * 96)
    print("阶段 2：重启（--limit 24，应复用阶段 1 的 %d 个点）" % ok1)
    print("-" * 96)
    out2, timeout2 = run_stage(24, timeout=600)
    ok2, skip2 = count_journal()
    print("  journal：ok=%d skip=%d" % (ok2, skip2))
    resumed_line = None
    for line in out2.splitlines():
        if "断点续跑" in line:
            resumed_line = line.strip()
            print("  | " + resumed_line)
        elif "成功" in line or "合格点" in line:
            print("  | " + line.strip())

    print()
    print("=" * 96)
    print("判定")
    print("=" * 96)
    # ⚠️ 断言口径（第一版写错，留痕）：
    # journal 里记的是**所有**完成过的点，含 ok 与 skip 两类。
    # 所以重启时报的「已有 N 条」= ok1 + skip1，不是 ok1。
    # 第一版断言用 str(ok1) 去匹配，必然失败（实测 ok1=8 但报 11 条）。
    total1 = ok1 + skip1
    ok_resume = (resumed_line is not None and str(total1) in resumed_line)
    # 更硬的判据：阶段 2 的 resumed 计数应等于 total1
    m = re.search(r"'resumed':\s*(\d+)", out2)
    resumed_n = int(m.group(1)) if m else None
    print("  阶段 1：ok=%d skip=%d 合计=%d" % (ok1, skip1, total1))
    print("  阶段 2：总 ok=%d，resumed=%s" % (ok2, resumed_n))
    print("  重启时报告「已有 %d 条」：%s" % (total1, ok_resume))
    if resumed_n is not None and resumed_n == total1 and ok2 >= ok1:
        print()
        print("  ✔ 断点续跑有效：resumed=%d 与阶段 1 完成数一致，"
              "总 ok 只增不减（%d -> %d）。" % (resumed_n, ok1, ok2))
        verdict = True
    elif ok2 >= ok1 and ok_resume:
        print()
        print("  ✔ 断点续跑有效（弱判据）：重启报告了已有记录数，总 ok 只增不减。")
        print("     注：resumed 计数未解析到，仅凭日志文本判定。")
        verdict = True
    else:
        print()
        print("  ✘ 断点续跑未按预期工作：")
        if not ok_resume:
            print("     重启时报告的记录数与阶段 1 合计不符")
        if ok2 < ok1:
            print("     journal 记录数下降 ⇒ 可能被覆盖而非追加")
        verdict = False

    # ---- 阴性对照：删 journal 应从头算 ----
    print()
    print("-" * 96)
    print("阴性对照：删除 journal 后重跑，应从头算（resumed 不存在或为 0）")
    print("-" * 96)
    if os.path.exists(JOURNAL):
        os.remove(JOURNAL)
    out3, _ = run_stage(4, timeout=300)
    resumed3 = any("断点续跑" in l for l in out3.splitlines())
    print("  是否出现「断点续跑」提示：%s（应为 False）" % resumed3)
    if not resumed3:
        print("  ✔ 阴性对照通过：无 journal 时不误报续跑。")
    else:
        print("  ✘ 阴性对照失败：无 journal 仍报续跑，说明判断有误。")
        verdict = False

    print()
    print("=" * 96)
    print("最终判定：%s" % ("✔ 通过" if verdict else "✘ 未通过"))
    print("=" * 96)
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
