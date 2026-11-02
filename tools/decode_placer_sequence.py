# -*- coding: utf-8 -*-
"""还原布雷调用序列的完整语义，确认首点保护机制。

已知：
  · 布雷函数 @0x4d9228(mineCount)：纯拒绝采样
        while count<=mineCount:
            x=Random(W+1); y=Random(H+1)
            if cell(x,y)==9: cell(x,y)=MINE; count++
  · 6 个调用点全部在 0x5313b8 这个函数里（同一个函数的 6 个分支）
  · 调用前固定序列：mov ax,[0x546d08]（雷数）→ call 0x4d9228

本脚本解码每个调用点之后的固定后续：
  · [0x546d14]  = 棋盘数组指针
  · [0x546d08]  = 雷数
  · 紧接的 call 0x... -> 可能是"计算数字"或"首点展开"

用法: python tools/decode_placer_sequence.py
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
    # 对每个调用点，解码 call 之后的 call 目标
    sites = [0x531990, 0x531B40, 0x531D02, 0x531F4D, 0x532170, 0x53232E]
    print("=== 每个布雷调用点之后跟随的 call 目标 ===")
    targets = set()
    for site in sites:
        f = va2f(site)
        # 找 call 之后的第一个 E8
        j = f + 5
        for k in range(j, j + 24):
            if d[k] == 0xE8:
                rel = struct.unpack_from("<i", d, k + 1)[0]
                tgt = f2va(k) + 5 + rel
                targets.add(tgt)
                print(f"  call@0x{site:08x} -> 后继 call 0x{tgt:08x}")
                break
    print(f"\n唯一后继目标: {[hex(t) for t in sorted(targets)]}")

    print("\n" + "=" * 74)
    print("=== 布雷函数内部用到的全局量 ===")
    globals_used = {
        0x547108: "棋盘宽 W",
        0x546FE8: "棋盘高 H",
        0x546D08: "雷数 mineCount",
        0x546D14: "（棋盘数组指针候选）",
        0x546E6C: "（cell 值常量候选）",
    }
    for va, name in globals_used.items():
        # 找引用该地址的指令位置
        pat = struct.pack("<I", va)
        refs = []
        pos = 0x400
        while True:
            i = d.find(pat, pos, 0x400 + 0x14299C)
            if i < 0:
                break
            refs.append(f2va(i))
            pos = i + 1
        print(f"  0x{va:08x} {name}: {len(refs)} 处引用, 前5 {[hex(x) for x in refs[:5]]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
