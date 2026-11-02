# -*- coding: utf-8 -*-
"""导出布雷函数原始字节 + 对齐反汇编（从正确边界开始）。

已知锚点：
  · 0x4d929e / 0x4d92af 处连续 call Random，参数为 [0x547108]=宽、[0x546fe8]=高
  · 该函数起点由 0x4d9228 的 push ebp; mov ebp,esp 标定
  · Delphi System.Random @ 0x4033c8

用法: python tools/dump_mine_bytes.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000


def va2f(v):
    return 0x400 + (v - IMG - 0x1000)


def f2va(f):
    return IMG + 0x1000 + (f - 0x400)


def main():
    d = open(EXE, "rb").read()
    print("=== 原始字节 0x4d9220 .. 0x4d9340 ===")
    lo, hi = 0x4d9220, 0x4d9340
    f0 = va2f(lo)
    for i in range(0, hi - lo, 16):
        chunk = d[f0 + i: f0 + i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        print(f"0x{lo+i:08x}  {hexs}")
    print()
    print("=== 关键位置单字节核对 ===")
    for va in (0x4d929e, 0x4d92af, 0x4d9228):
        f = va2f(va)
        print(f"VA 0x{va:08x}  bytes {d[f:f+8].hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
