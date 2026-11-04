# -*- coding: utf-8 -*-
"""校验阶段产物：schema + complete 标记 + 记录数。

=================== 为什么需要 ====================
启动器不能用"文件存在"或"exit 0"单独判成功：
  · 文件可能是**上一次运行留下的**（实测 S3 产物 MD5 与旧运行相同）
  · exit 0 也可能什么都没写

因此必须校验：
  S1 文件存在且可解析为 JSON
  S2 顶层有 complete == True
  S3 记录数与期望一致（--expect-n）

退出码：0 = 通过；1 = 不通过（stderr 给原因）

用法:
  python bench/verify_stage_output.py --path <json> --expect-n 600
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


def fail(msg):
    print(f"[stage-output] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--expect-n", type=int, default=None)
    args = ap.parse_args()

    p = args.path
    if not os.path.exists(p):
        fail(f"missing: {p}")
    if os.path.getsize(p) == 0:
        fail(f"empty file: {p}")

    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:                              # noqa: BLE001
        fail(f"unparsable JSON: {e}")

    if not isinstance(d, dict):
        fail("top level is not an object")

    # S2: complete 标记
    if d.get("complete") is not True:
        fail(f"complete flag is not true (got {d.get('complete')!r})")

    # S3: 记录数
    if args.expect_n is not None:
        n = d.get("n_done")
        if n is None:
            # 兼容不同脚本：试着从 results/games 推断
            if isinstance(d.get("results"), dict):
                n = min(len(v) for v in d["results"].values())
            elif "games" in d:
                n = d["games"]
            else:
                fail("cannot determine record count (no n_done/results/games)")
        if isinstance(n, dict):
            n = min(n.values())
        if int(n) != args.expect_n:
            fail(f"record count {n} != expected {args.expect_n}")

    print(f"[stage-output] OK: {p} "
          f"(complete=true, n={args.expect_n if args.expect_n else 'n/a'})")


if __name__ == "__main__":
    main()
