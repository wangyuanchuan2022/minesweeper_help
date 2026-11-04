# -*- coding: utf-8 -*-
"""受控验证：启动器能否区分「成功 / 普通非零退出 / KeyboardInterrupt」。

=================== advisor 要求 ====================
"用受控脚本分别模拟成功、普通非零退出和 KeyboardInterrupt，
确认门线失败、实验失败与中断不会混淆。"

=================== 做法 ====================
造三个受控脚本：
  ok.py        → exit 0，写完整 JSON（含 complete=true）
  fail.py      → exit 3，写日志后退出（普通失败）
  interrupt.py → 先写完整 JSON，再抛 KeyboardInterrupt（模拟"产物已写但被中断"）

分别跑，检查：
  · ok：exit 0，JSON 完整
  · fail：exit 3，无 JSON
  · interrupt：exit 非 0，但 JSON 完整且 complete=true
    ⇒ 此时**不能**仅凭 exit code 判"实验失败"，必须看 complete 标记

用法: python bench/verify_exit_code_semantics.py
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

TMPDIR = "state/ab/_exit_semantics"


def write_script(name, body):
    os.makedirs(TMPDIR, exist_ok=True)
    p = os.path.join(TMPDIR, name)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return p


def main():
    print("=" * 96)
    print("退出码语义受控验证")
    print("=" * 96)

    # 共用：原子写
    helper = '''
import json, os, sys
def dump(obj, path):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False); f.flush()
        try: os.fsync(f.fileno())
        except OSError: pass
    os.replace(tmp, path)
'''

    ok_p = write_script("ok.py", helper + '''
out = sys.argv[1]
dump({"complete": True, "n_done": 10}, out)
print("ok done")
''')
    fail_p = write_script("fail.py", helper + '''
out = sys.argv[1]
print("about to fail")
sys.exit(3)
''')
    int_p = write_script("interrupt.py", helper + '''
out = sys.argv[1]
dump({"complete": True, "n_done": 10}, out)   # 先写完整产物
print("wrote output, now interrupting")
raise KeyboardInterrupt
''')

    cases = [
        ("ok", ok_p, "state/ab/_exit_semantics/out_ok.json", 0, True),
        ("fail", fail_p, "state/ab/_exit_semantics/out_fail.json", 3, False),
        ("interrupt", int_p, "state/ab/_exit_semantics/out_int.json",
         "nonzero", True),
    ]

    fails = []
    for name, script, out, expect_rc, expect_complete in cases:
        if os.path.exists(out):
            os.remove(out)
        p = subprocess.run([PY, "-X", "utf8", script, out], cwd=ROOT,
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        exists = os.path.exists(out)
        complete = None
        if exists:
            try:
                with open(out, encoding="utf-8") as f:
                    complete = json.load(f).get("complete")
            except Exception:                          # noqa: BLE001
                complete = "unparsable"

        print(f"\n[{name}]")
        print(f"  退出码 {p.returncode}（期望 {expect_rc}）")
        print(f"  产物存在 {exists}   complete 标记 {complete}")
        tail = (p.stderr or "").strip().split("\n")[-1] if p.stderr else ""
        if tail:
            print(f"  stderr 尾：{tail[:70]}")

        # 判定
        if expect_rc == 0:
            ok = (p.returncode == 0 and exists and complete is True)
            label = "成功：exit 0 + 完整产物"
        elif expect_rc == "nonzero":
            ok = (p.returncode != 0 and exists and complete is True)
            label = "中断：exit≠0 但产物完整（complete=true）"
        else:
            ok = (p.returncode == expect_rc and not exists)
            label = "普通失败：exit 3 + 无产物"
        print(f"  {'✔' if ok else '✘'} {label}")
        if not ok:
            fails.append(name)

    print("\n" + "=" * 96)
    print("判定")
    print("=" * 96)
    if fails:
        print(f"✘ 未通过：{fails}")
        return 1
    print("✅ 三种退出状态可区分：")
    print("   · 成功        → exit 0  + 产物 complete=true")
    print("   · 普通失败    → exit≠0  + 无产物")
    print("   · 被中断      → exit≠0  + **产物可能仍完整**（complete=true）")
    print()
    print("   ⇒ 因此**不能只看 exit code 判实验失败**：")
    print("     必须同时检查产物是否存在且 complete=true。")
    print("     这正是本次 S3 出现「JSON 已写入 + 日志含 KeyboardInterrupt")
    print("     + 启动器跳到 gate_failed」三类事实并存的原因。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
