# -*- coding: utf-8 -*-
"""反汇编布雷函数（VA 0x4d9290 附近）：还原 Arbiter 的雷生成算法。

已知事实（REA 反编译确认）：
  · Delphi System.Random @ 0x4033c8:
        RandSeed = RandSeed*0x08088405 + 1
        Random(n) = (u64)n * RandSeed >> 32
  · RandSeed @ 0x544008，启动时由 CryptGenRandom 播种（FUN_004d47ac）
  · 全局 0x547108 = 棋盘宽, 0x546fe8 = 棋盘高
  · 在 0x4d929e / 0x4d92af 处连续两次 call Random，参数分别是宽和高
    => 这就是"随机挑一个格子"的循环

本脚本用纯 Python 做轻量 x86 反汇编（只覆盖常用指令），
把 0x4d9200..0x4d9360 这段打出来，人工判读循环与排除规则。

用法: python tools/disasm_mine_loop.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000
CODE_RVA, CODE_RAW, CODE_VSZ = 0x1000, 0x400, 0x14299C

# 关注区间
LO_VA, HI_VA = 0x4D9150, 0x4D9360

REG32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]


def f2va(f):
    return IMG + CODE_RVA + (f - CODE_RAW)


def va2f(v):
    return CODE_RAW + (v - IMG - CODE_RVA)


class Disasm:
    """极简 x86 反汇编器：仅覆盖本区间会出现的指令。"""

    def __init__(self, d):
        self.d = d
        self.lines = []

    def decode(self, va, f):
        d = self.d
        b = d[f]
        out = None
        size = 1

        def i32(off):
            return struct.unpack_from("<i", d, f + off)[0]

        def u32(off):
            return struct.unpack_from("<I", d, f + off)[0]

        def i8(off):
            return struct.unpack_from("<b", d, f + off)[0]

        if b == 0xE8:
            rel = i32(1)
            out = f"call 0x{va + 5 + rel:08x}"
            size = 5
        elif b == 0xE9:
            rel = i32(1)
            out = f"jmp 0x{va + 5 + rel:08x}"
            size = 5
        elif b == 0xEB:
            rel = i8(1)
            out = f"jmp short 0x{va + 2 + rel:08x}"
            size = 2
        elif 0x70 <= b <= 0x7F:
            rel = i8(1)
            cc = {0x74: "jz", 0x75: "jnz", 0x7C: "jl", 0x7D: "jge",
                  0x7E: "jle", 0x7F: "jg", 0x72: "jb", 0x73: "jae",
                  0x76: "jbe", 0x77: "ja"}.get(b, f"jcc{b:02x}")
            out = f"{cc} short 0x{va + 2 + rel:08x}"
            size = 2
        elif b == 0x0F and d[f + 1] in (0x84, 0x85, 0x8C, 0x8D, 0x8E, 0x8F):
            rel = i32(2)
            cc = {0x84: "jz", 0x85: "jnz", 0x8C: "jl", 0x8D: "jge",
                  0x8E: "jle", 0x8F: "jg"}[d[f + 1]]
            out = f"{cc} near 0x{va + 6 + rel:08x}"
            size = 6
        elif b == 0xA1:
            out = f"mov eax, [0x{u32(1):08x}]"
            size = 5
        elif b == 0xA3:
            out = f"mov [0x{u32(1):08x}], eax"
            size = 5
        elif b in (0xB8, 0xB9, 0xBA, 0xBB, 0xBE, 0xBF):
            out = f"mov {REG32[b - 0xB8]}, 0x{u32(1):x}"
            size = 5
        elif b == 0x68:
            out = f"push 0x{u32(1):x}"
            size = 5
        elif b == 0x6A:
            out = f"push {i8(1)}"
            size = 2
        elif 0x50 <= b <= 0x57:
            out = f"push {REG32[b - 0x50]}"
            size = 1
        elif 0x58 <= b <= 0x5F:
            out = f"pop {REG32[b - 0x58]}"
            size = 1
        elif b == 0x8B:
            m = d[f + 1]
            reg = REG32[(m >> 3) & 7]
            rm = m & 7
            if rm == 5:
                out = f"mov {reg}, [0x{u32(2):08x}]"
                size = 6
            elif rm == 4:
                sib = d[f + 2]
                base = REG32[sib & 7]
                out = f"mov {reg}, [{base}]"
                size = 3
            else:
                out = f"mov {reg}, [{REG32[rm]}]"
                size = 2
        elif b == 0x89:
            m = d[f + 1]
            reg = REG32[(m >> 3) & 7]
            rm = m & 7
            if rm == 5:
                out = f"mov [0x{u32(2):08x}], {reg}"
                size = 6
            elif rm == 4:
                sib = d[f + 2]
                base = REG32[sib & 7]
                out = f"mov [{base}], {reg}"
                size = 3
            else:
                out = f"mov [{REG32[rm]}], {reg}"
                size = 2
        elif b == 0x83:
            m = d[f + 1]
            reg = REG32[(m >> 3) & 7]
            rm = m & 7
            op = {0: "add", 5: "sub", 7: "cmp", 1: "or", 4: "and"}.get((m >> 3) & 7, "op")
            out = f"{op} {REG32[rm]}, {i8(2)}"
            size = 3
        elif b == 0x3D:
            out = f"cmp eax, 0x{u32(1):x}"
            size = 5
        elif b == 0x3B:
            m = d[f + 1]
            out = f"cmp {REG32[(m>>3)&7]}, {REG32[m&7]}"
            size = 2
        elif b == 0x85:
            m = d[f + 1]
            out = f"test {REG32[(m>>3)&7]}, {REG32[m&7]}"
            size = 2
        elif b == 0x39:
            m = d[f + 1]
            out = f"cmp [{REG32[m&7]}], {REG32[(m>>3)&7]}" if (m & 7) != 5 else "cmp [mem],reg"
            size = 2
        elif b == 0x31:
            m = d[f + 1]
            out = f"xor {REG32[(m>>3)&7]}, {REG32[m&7]}"
            size = 2
        elif b == 0x33:
            m = d[f + 1]
            out = f"xor {REG32[(m>>3)&7]}, {REG32[m&7]}"
            size = 2
        elif b == 0x69:
            m = d[f + 1]
            out = f"imul {REG32[(m>>3)&7]}, {REG32[m&7]}, 0x{u32(2):x}"
            size = 6
        elif b == 0x0F and d[f + 1] == 0xAF:
            m = d[f + 2]
            out = f"imul {REG32[(m>>3)&7]}, {REG32[m&7]}"
            size = 3
        elif b == 0xF7:
            m = d[f + 1]
            sub = (m >> 3) & 7
            if sub == 2:
                out = "not r/m"
            elif sub == 3:
                out = "neg r/m"
            elif sub == 4:
                out = "mul"
            elif sub == 6:
                out = "div"
            else:
                out = "f7 ?"
            size = 2
        elif b == 0xC3:
            out = "ret"
            size = 1
        elif b == 0xC2:
            out = f"ret 0x{struct.unpack_from('<H', d, f+1)[0]:x}"
            size = 3
        elif b == 0xCC:
            out = "int3"
            size = 1
        elif b == 0x90:
            out = "nop"
            size = 1
        elif b == 0x55:
            out = "push ebp"
            size = 1
        elif b == 0x5D:
            out = "pop ebp"
            size = 1
        elif b == 0x8D:
            m = d[f + 1]
            size = 2
            if m & 7 == 5:
                out = f"lea {REG32[(m>>3)&7]}, [0x{u32(2):08x}]"
                size = 6
            elif m & 7 == 4:
                out = f"lea {REG32[(m>>3)&7]}, [mem+sib]"
                size = 3
            else:
                out = f"lea {REG32[(m>>3)&7]}, [{REG32[m&7]}]"
        elif b == 0xC7:
            m = d[f + 1]
            size = 6
            out = f"mov dword [{REG32[m&7] if m&7 != 5 else 'mem'}], 0x{u32(2):x}"
        elif b == 0x8A or b == 0x88:
            out = "mov r8"
            size = 2
        elif b == 0x84:
            m = d[f + 1]
            out = f"test {REG32[(m>>3)&7]}|8, {REG32[m&7]}|8"
            size = 2
        else:
            out = f"db 0x{b:02x}"
            size = 1
        return out, size


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    d = open(EXE, "rb").read()
    dis = Disasm(d)
    print(f"反汇编区间 0x{LO_VA:08x} .. 0x{HI_VA:08x}")
    print("=" * 78)
    va = LO_VA
    while va < HI_VA:
        f = va2f(va)
        if f is None or f >= len(d):
            break
        text, size = dis.decode(va, f)
        raw = d[f:f + size].hex()
        mark = ""
        if "call 0x004033c8" in text:
            mark = "   <== Random"
        if "0x547108" in text:
            mark = "   <== board WIDTH"
        if "0x546fe8" in text:
            mark = "   <== board HEIGHT"
        print(f"0x{va:08x}  {raw:<14} {text}{mark}")
        va += size
    return 0


if __name__ == "__main__":
    sys.exit(main())
