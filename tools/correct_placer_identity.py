# -*- coding: utf-8 -*-
"""纠正：确认 0x4d9228 到底是不是"布雷"。

新证据（推翻前判定）：
  · 6 个调用点 @0x531xxx 调用 0x4d9228 后，紧接着调用 0x52c7ac，
    而 0x52c7ac = 「用坐标读像素」(FUN_0052c790 得到像素索引，
    再查 DAT_00546724 颜色表) => 这是在**读取屏幕**，不是布雷。
  · 因此 0x4d9228 的入参不是"雷数"，而是"要处理的格子数/循环次数"，
    且它内部调用的 GetCell/SetCell 也是像素层面的操作。

真正的布雷函数应当在"棋盘数组 0x546d14 上写雷"，而 0x4d9228 的
写操作目标需重新核对。

本脚本：
  1. 核对 0x4d9228 内部写操作的目标地址
  2. 列出所有"对棋盘数组 0x546d14 写入"的函数，找布雷候选

用法: python tools/correct_placer_identity.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000

BOARD_PTR = 0x546D14
WIDTH = 0x547108
HEIGHT = 0x546FE8
MINES = 0x546D08


def va2f(v):
    return 0x400 + (v - IMG - 0x1000)


def f2va(f):
    return IMG + 0x1000 + (f - 0x400)


def refs_to(d, addr):
    pat = struct.pack("<I", addr)
    out = []
    pos = 0x400
    end = 0x400 + 0x14299C
    while True:
        i = d.find(pat, pos, end)
        if i < 0:
            break
        out.append(f2va(i))
        pos = i + 1
    return out


def main():
    d = open(EXE, "rb").read()
    print("=== 0x4d9228 内部关键操作核对 ===")
    print("字节（从 0x4d9228 起）：")
    f = va2f(0x4D9228)
    for i in range(0, 0xB0, 16):
        chunk = d[f + i: f + i + 16]
        print(f"  0x{0x4D9228+i:08x}  " + " ".join(f"{b:02x}" for b in chunk))

    print("\n=== 各全局量被引用的函数（按地址段聚合）===")
    for addr, name in ((BOARD_PTR, "棋盘数组"), (MINES, "雷数"),
                       (WIDTH, "宽"), (HEIGHT, "高")):
        r = refs_to(d, addr)
        funcs = sorted(set((x // 0x400) * 0x400 for x in r))
        print(f"\n{name} 0x{addr:08x}: {len(r)} 处引用")
        print(f"  落在 {len(funcs)} 个 1KB 区块，前 10: "
              f"{[hex(x) for x in funcs[:10]]}")

    print("\n" + "=" * 74)
    print("判读：布雷函数应【同时】引用 棋盘数组 + 雷数 + 宽 + 高")
    print("      把四者的引用地址取交集即可锁定候选")
    sets = []
    names = ["棋盘", "雷数", "宽", "高"]
    for addr in (BOARD_PTR, MINES, WIDTH, HEIGHT):
        s = set((x // 0x400) * 0x400 for x in refs_to(d, addr))
        sets.append(s)
    common = set.intersection(*sets)
    print(f"  四者共同区块: {[hex(x) for x in sorted(common)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
