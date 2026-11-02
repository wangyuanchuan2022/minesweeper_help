# -*- coding: utf-8 -*-
"""判定 0x4d9228 到底是"布雷"还是"随机点开"：对比两种假设。

从字节还原，0x4d9228 的循环体是：
    count = 1
    while count <= arg:                      ; arg = 要放的雷数
        x = Random([0x547108] + 1)           ; [0x547108] = 宽
        y = Random([0x546fe8] + 1)           ; [0x546fe8] = 高
        if GetCell(x, y) == 9:               ; 未开
            SetCell(x, y, MINE)              ; 写雷
            count++

这是标准的"随机布雷（拒绝采样）"，无任何排除规则。

而调用点 0x531990 之后紧接着：
    x = Random(width+1); y = Random(height+1); Random(2) ...
这是"随机找一个格子点击"（开局自动点）。

本脚本把两段都反汇编出来，确认二者语义不同。

用法: python tools/compare_placer_vs_click.py
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


def dump(d, lo_va, hi_va, label):
    print(f"\n=== {label} ===")
    f0 = va2f(lo_va)
    for i in range(0, hi_va - lo_va, 16):
        chunk = d[f0 + i: f0 + i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        print(f"0x{lo_va+i:08x}  {hexs}")


def main():
    d = open(EXE, "rb").read()
    # 布雷函数
    dump(d, 0x4D9228, 0x4D92D0, "布雷函数 0x4d9228（拒绝采样放雷）")
    # 调用点（随机点开）
    dump(d, 0x531980, 0x5319D0, "调用点 0x531990 之后（随机点开）")
    print()
    print("=" * 74)
    print("判读要点：")
    print("  · 布雷：x=Random(W+1), y=Random(H+1)，判 ==9 才写雷，count 计到雷数")
    print("  · 点开：x=Random(W+1), y=Random(H+1)，然后 Random(2) 决定左右键")
    print("  · 两者都用 Random(W+1)/Random(H+1)，但布雷有'判未开+写雷+计数'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
