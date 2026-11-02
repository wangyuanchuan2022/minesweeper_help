# -*- coding: utf-8 -*-
"""反汇编真正的布雷函数：区块 0x4d2000（同时引用雷数/棋盘/宽，含 9 处 Random）。

前次判定的纠正：
  · 0x4d9228 只引用 宽(0x547108)/高(0x546fe8)，**不引用雷数**，
    且调用点后紧跟像素读取(0x52c7ac) => 那是"随机读一格像素"，不是布雷。
  · 本脚本聚焦 0x4d2000 区块，它同时引用雷数(0x546d08)、棋盘数组(0x546d14)、
    宽(0x547108)，并有 9 处 call Random。

用法: python tools/disasm_real_placer.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000

G_W = 0x547108
G_H = 0x546FE8
G_M = 0x546D08
G_B = 0x546D14


def va2f(v):
    return 0x400 + (v - IMG - 0x1000)


def f2va(f):
    return IMG + 0x1000 + (f - 0x400)


def annot(text):
    if "0x00547108" in text:
        return "  ; W"
    if "0x00546fe8" in text:
        return "  ; H"
    if "0x00546d08" in text:
        return "  ; MINES"
    if "0x00546d14" in text:
        return "  ; BOARD"
    if "0x004033c8" in text:
        return "  ; Random"
    return ""


def disasm_range(d, lo, hi):
    """极简指令解码（够用即可，遇到不认识就按 1 字节跳过并标注）。"""
    va = lo
    out = []
    while va < hi:
        f = va2f(va)
        b = d[f]
        text = None
        size = 1
        if b == 0xE8:
            rel = struct.unpack_from("<i", d, f + 1)[0]
            text = f"call 0x{va+5+rel:08x}"
            size = 5
        elif b == 0xE9:
            rel = struct.unpack_from("<i", d, f + 1)[0]
            text = f"jmp 0x{va+5+rel:08x}"
            size = 5
        elif b == 0xEB:
            rel = struct.unpack_from("<b", d, f + 1)[0]
            text = f"jmp short 0x{va+2+rel:08x}"
            size = 2
        elif 0x70 <= b <= 0x7F:
            rel = struct.unpack_from("<b", d, f + 1)[0]
            cc = {0x74: "jz", 0x75: "jnz", 0x7C: "jl", 0x7D: "jge",
                  0x7E: "jle", 0x7F: "jg", 0x72: "jb", 0x73: "jae"}.get(b, "j?")
            text = f"{cc} 0x{va+2+rel:08x}"
            size = 2
        elif b == 0x0F and d[f + 1] in (0x84, 0x85, 0x8C, 0x8D, 0x8E, 0x8F):
            rel = struct.unpack_from("<i", d, f + 2)[0]
            cc = {0x84: "jz", 0x85: "jnz", 0x8C: "jl",
                  0x8D: "jge", 0x8E: "jle", 0x8F: "jg"}[d[f + 1]]
            text = f"{cc} near 0x{va+6+rel:08x}"
            size = 6
        elif b == 0xA1:
            text = f"mov eax, [0x{struct.unpack_from('<I',d,f+1)[0]:08x}]"
            size = 5
        elif b == 0xA3:
            text = f"mov [0x{struct.unpack_from('<I',d,f+1)[0]:08x}], eax"
            size = 5
        elif b in (0xB8, 0xB9, 0xBA, 0xBB):
            r = ["eax", "ecx", "edx", "ebx"][b - 0xB8]
            text = f"mov {r}, 0x{struct.unpack_from('<I',d,f+1)[0]:x}"
            size = 5
        elif b == 0x68:
            text = f"push 0x{struct.unpack_from('<I',d,f+1)[0]:x}"
            size = 5
        elif b == 0x6A:
            text = f"push {struct.unpack_from('<b',d,f+1)[0]}"
            size = 2
        elif 0x50 <= b <= 0x57:
            text = f"push {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][b-0x50]}"
            size = 1
        elif 0x58 <= b <= 0x5F:
            text = f"pop {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][b-0x58]}"
            size = 1
        elif b == 0x8B:
            m = d[f + 1]
            reg = ["eax","ecx","edx","ebx","esp","ebp","esi","edi"][(m>>3)&7]
            rm = m & 7
            if rm == 5:
                text = f"mov {reg}, [0x{struct.unpack_from('<I',d,f+2)[0]:08x}]"
                size = 6
            else:
                base = ["eax","ecx","edx","ebx","esp","ebp","esi","edi"][rm]
                text = f"mov {reg}, [{base}]"
                size = 2
        elif b == 0x89:
            m = d[f + 1]
            reg = ["eax","ecx","edx","ebx","esp","ebp","esi","edi"][(m>>3)&7]
            rm = m & 7
            if rm == 5:
                text = f"mov [0x{struct.unpack_from('<I',d,f+2)[0]:08x}], {reg}"
                size = 6
            else:
                base = ["eax","ecx","edx","ebx","esp","ebp","esi","edi"][rm]
                text = f"mov [{base}], {reg}"
                size = 2
        elif b == 0x83:
            m = d[f + 1]
            op = {(m>>3)&7: ""}
            sub = (m >> 3) & 7
            o = {0: "add", 5: "sub", 7: "cmp", 1: "or", 4: "and"}.get(sub, "op")
            text = f"{o} {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][m&7]}, {struct.unpack_from('<b',d,f+2)[0]}"
            size = 3
        elif b == 0x3D:
            text = f"cmp eax, 0x{struct.unpack_from('<I',d,f+1)[0]:x}"
            size = 5
        elif b == 0x81:
            m = d[f + 1]
            sub = (m >> 3) & 7
            o = {0: "add", 5: "sub", 7: "cmp", 1: "or", 4: "and"}.get(sub, "op")
            rm = m & 7
            if rm == 5:
                text = f"{o} [mem], 0x{struct.unpack_from('<I',d,f+2)[0]:x}"
                size = 6
            else:
                text = f"{o} {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][rm]}, 0x{struct.unpack_from('<I',d,f+2)[0]:x}"
                size = 6
        elif b == 0x85:
            m = d[f + 1]
            text = f"test {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][(m>>3)&7]}, {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][m&7]}"
            size = 2
        elif b in (0x31, 0x33):
            m = d[f + 1]
            text = f"xor {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][(m>>3)&7]}, {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][m&7]}"
            size = 2
        elif b == 0x0F and d[f + 1] == 0xB6:
            m = d[f + 2]
            text = "movzx"
            size = 3
        elif b == 0x0F and d[f + 1] == 0xBF:
            text = "movsx"
            size = 3
        elif b == 0x8A:
            text = "mov r8, r/m8"
            size = 2
        elif b == 0x88:
            text = "mov r/m8, r8"
            size = 2
        elif b == 0xC6:
            text = "mov r/m8, imm8"
            size = 3
        elif b == 0xC3:
            text = "ret"
            size = 1
        elif b == 0xC2:
            text = f"ret 0x{struct.unpack_from('<H',d,f+1)[0]:x}"
            size = 3
        elif b == 0x55:
            text = "push ebp"
            size = 1
        elif b == 0x5D:
            text = "pop ebp"
            size = 1
        elif b == 0x8D:
            m = d[f + 1]
            size = 2
            if m & 7 == 5:
                text = f"lea {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][(m>>3)&7]}, [0x{struct.unpack_from('<I',d,f+2)[0]:08x}]"
                size = 6
            else:
                text = f"lea {['eax','ecx','edx','ebx','esp','ebp','esi','edi'][(m>>3)&7]}, [mem]"
        elif b == 0x40 <= b <= 0x47 if False else False:
            pass
        else:
            text = f"db 0x{b:02x}"
            size = 1
        out.append((va, d[f:f + size].hex(), text, annot(text)))
        va += size
    return out


def main():
    d = open(EXE, "rb").read()
    print("=== 0x4d21a0 .. 0x4d22e0（区块 0x4d2000 的 Random 密集区）===")
    lines = disasm_range(d, 0x4D21A0, 0x4D22E0)
    for va, raw, text, an in lines:
        print(f"0x{va:08x}  {raw:<14} {text}{an}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
