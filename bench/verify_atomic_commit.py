# -*- coding: utf-8 -*-
"""验证原子提交：中断只留下 .tmp，正式 JSON 保持不变。

=================== 为什么必须验证 ====================
`_atomic_json_dump` 声称"先写 .tmp，fsync 后 replace"。
必须用受控中断证明：
  · 中断发生在 dump 过程中 → 正式文件**不被破坏**，只留 .tmp
  · 中断发生在 dump 之后、replace 之前 → 同上
  · 正常完成 → 正式文件被替换，无 .tmp 残留

做法：用 monkeypatch 在 json.dump 中途抛 KeyboardInterrupt，
然后检查正式文件内容是否仍是旧内容。
"""
import importlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))

TMP = "state/ab/_atomic_test"
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✔' if cond else '✘'} {name}"
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    print("=" * 96)
    print("原子提交验证：中断不得破坏正式文件")
    print("=" * 96)

    os.makedirs(TMP, exist_ok=True)
    target = os.path.join(TMP, "out.json")
    old_content = {"old": True, "complete": True, "n_done": 1}
    with open(target, "w", encoding="utf-8") as f:
        json.dump(old_content, f)

    # 从被测脚本导入真实的 _atomic_json_dump
    mod = importlib.import_module("measure_endgame_stratified")
    dump = mod._atomic_json_dump

    # ---------- 情形 1：正常完成 ----------
    print("\n[1] 正常完成")
    dump({"new": True, "complete": True}, target)
    with open(target, encoding="utf-8") as f:
        got = json.load(f)
    check("正式文件被替换为新内容", got.get("new") is True, str(got))
    check("无 .tmp 残留", not os.path.exists(target + ".tmp"))

    # ---------- 情形 2：dump 中途被中断 ----------
    print("\n[2] dump 中途 KeyboardInterrupt")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(old_content, f)

    real_dump = json.dump

    def boom(obj, fp, **kw):
        # 先写一半，再中断
        fp.write('{"partial": ')
        fp.flush()
        raise KeyboardInterrupt("simulated")

    json.dump = boom
    interrupted = False
    try:
        dump({"x": 1, "complete": True}, target)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        json.dump = real_dump

    check("中断确实发生", interrupted)
    with open(target, encoding="utf-8") as f:
        after = json.load(f)
    check("正式文件**未**被破坏（仍是旧内容）",
          after == old_content, str(after))
    check("中断留下了 .tmp", os.path.exists(target + ".tmp"))
    if os.path.exists(target + ".tmp"):
        sz = os.path.getsize(target + ".tmp")
        print(f"      .tmp 大小 {sz} 字节（半成品，不影响正式文件）")
        os.remove(target + ".tmp")

    # ---------- 情形 3：replace 之前中断 ----------
    print("\n[3] replace 之前 KeyboardInterrupt")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(old_content, f)
    real_replace = os.replace

    def boom_replace(src, dst):
        raise KeyboardInterrupt("simulated at replace")

    os.replace = boom_replace
    interrupted2 = False
    try:
        dump({"y": 2, "complete": True}, target)
    except KeyboardInterrupt:
        interrupted2 = True
    finally:
        os.replace = real_replace

    check("中断确实发生", interrupted2)
    with open(target, encoding="utf-8") as f:
        after2 = json.load(f)
    check("正式文件仍是旧内容", after2 == old_content, str(after2))
    if os.path.exists(target + ".tmp"):
        os.remove(target + ".tmp")

    print("\n" + "=" * 96)
    if FAILS:
        print(f"✘ 未通过 {len(FAILS)} 项：{FAILS}")
        return 1
    print("✅ 原子提交有效：")
    print("   · 正常完成 → 正式文件被替换，无 .tmp 残留")
    print("   · dump 中途中断 → 正式文件保持旧内容，只留 .tmp")
    print("   · replace 前中断 → 正式文件保持旧内容")
    print("   ⇒ 正式 JSON 永远不会是半成品。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
