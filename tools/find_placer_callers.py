# -*- coding: utf-8 -*-
"""确认 0x4d9228（布雷函数）的调用方，并检查是否存在首点保护。

已还原的布雷函数 0x4d9228(mineCount)：
    count = 1
    while count <= mineCount:
        x = Random(width  + 1)      ; 0..width
        y = Random(height + 1)      ; 0..height
        if GetCell(x,y) == UNOPENED:
            SetCell(x, y, MINE)
            count++
    => 纯拒绝采样（uniform rejection sampling），**无任何排除规则**

本脚本找它的调用点，并在调用点附近找"首点"相关逻辑
（例如：若首点在雷上则重排 / 排除首点 3x3）。

用法: python tools/find_placer_callers.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000
PLACER_VA = 0x4D9228


def va2f(v):
    return 0x400 + (v - IMG - 0x1000)


def f2va(f):
    return IMG + 0x1000 + (f - 0x400)


def find_calls(d, target_va):
    out = []
    for f in range(0x400, 0x400 + 0x14299C - 5):
        if d[f] != 0xE8:
            continue
        rel = struct.unpack_from("<i", d, f + 1)[0]
        va = f2va(f)
        if va + 5 + rel == target_va:
            out.append((f, va))
    return out


def dump(d, lo_va, hi_va, label):
    print(f"\n--- {label}  0x{lo_va:08x} .. 0x{hi_va:08x} ---")
    f0 = va2f(lo_va)
    for i in range(0, hi_va - lo_va, 16):
        chunk = d[f0 + i: f0 + i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        print(f"0x{lo_va+i:08x}  {hexs}")


def main():
    d = open(EXE, "rb").read()
    calls = find_calls(d, PLACER_VA)
    print(f"布雷函数 0x{PLACER_VA:08x} 的调用点：{len(calls)} 处")
    for f, va in calls:
        print(f"  call @ VA 0x{va:08x}")
        # 打印调用点前后 0x80 字节
        dump(d, max(0x401000, va - 0x60), va + 0x40,
             f"caller context @ 0x{va:08x}")
    if not calls:
        print("（未找到直接调用，可能通过虚表/间接调用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
