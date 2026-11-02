# -*- coding: utf-8 -*-
"""提取 Random 调用点的参数，还原布雷循环结构。

已知（REA 反编译 0x4033c8 确认）：
    RandSeed = RandSeed * 0x08088405 + 1            ; Delphi System.Random
    Random(n) = (uint64)n * RandSeed >> 32
且 RandSeed(DAT_00544008) 在启动时由 CryptGenRandom 播种一次（FUN_004d47ac）。

本脚本对每个 `call Random` 调用点向前回溯，解码取参指令，
得到"这一处 Random 的参数是常数还是变量"。

用法: python tools/extract_random_args.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
RANDOM_VA = 0x4033C8


class PE:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        pe = struct.unpack_from("<I", self.d, 0x3C)[0]
        self.image_base = struct.unpack_from("<I", self.d, pe + 0x34)[0]
        nsec = struct.unpack_from("<H", self.d, pe + 6)[0]
        optsz = struct.unpack_from("<H", self.d, pe + 20)[0]
        sec = pe + 24 + optsz
        self.segs = []
        for k in range(nsec):
            o = sec + 40 * k
            name = self.d[o:o + 8].rstrip(b"\x00").decode("latin1")
            vs, va, rs, ro = struct.unpack_from("<IIII", self.d, o + 8)
            self.segs.append({"name": name, "va": va, "vs": vs, "ro": ro, "rs": rs})
        self.code = next(s for s in self.segs if s["name"] == "CODE")

    def f2va(self, f):
        for s in self.segs:
            if s["ro"] <= f < s["ro"] + s["rs"]:
                return self.image_base + s["va"] + (f - s["ro"])
        return None

    def va2f(self, va):
        rva = va - self.image_base
        for s in self.segs:
            if s["va"] <= rva < s["va"] + s["vs"]:
                return s["ro"] + (rva - s["va"])
        return None


def decode_call_arg(pe, call_f):
    """向调用点前回溯 <=16 字节，识别传参指令。返回 (kind, value, desc)。"""
    lo = max(pe.code["ro"], call_f - 16)
    win = pe.d[lo:call_f]
    # mov eax, imm32 (B8) / mov ecx,imm32(B9) / mov edx,imm32(BA) / push imm32(68)
    best = None
    for i in range(len(win) - 5, -1, -1):
        b = win[i]
        if b in (0xB8, 0xB9, 0xBA, 0x68):
            imm = struct.unpack_from("<I", win, i + 1)[0]
            reg = {0xB8: "eax", 0xB9: "ecx", 0xBA: "edx", 0x68: "push"}[b]
            best = ("imm", imm, f"{reg}, 0x{imm:x}")
            break
        if b == 0x8B:  # mov r32, r/m32
            modrm = win[i + 1] if i + 1 < len(win) else 0
            best = ("reg", None, f"mov (modrm {modrm:02x})")
            break
        if b in (0x50, 0x51, 0x52):  # push eax/ecx/edx
            best = ("pushreg", None, {0x50: "push eax", 0x51: "push ecx",
                                      0x52: "push edx"}[b])
            break
    return best


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    pe = PE(EXE)
    start, end = pe.code["ro"], pe.code["ro"] + pe.code["rs"]
    calls = []
    for f in range(start, end - 4):
        if pe.d[f] != 0xE8:
            continue
        rel = struct.unpack_from("<i", pe.d, f + 1)[0]
        va = pe.f2va(f)
        if va and va + 5 + rel == RANDOM_VA:
            calls.append((f, va))
    print(f"call Random 共 {len(calls)} 处")
    print("=" * 78)

    rows = []
    for f, va in calls:
        arg = decode_call_arg(pe, f)
        rows.append((va, arg))

    # 按"参数是常数"的分布统计
    consts = {}
    for va, arg in rows:
        if arg and arg[0] == "imm":
            consts[arg[1]] = consts.get(arg[1], 0) + 1
    print("Random 参数为常数的分布（常数 → 出现次数）：")
    for c, n in sorted(consts.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  0x{c:08x} ({c:6d})  ×{n}")

    print("\n" + "=" * 78)
    print("最可能是布雷循环的调用点（参数为棋盘尺寸量级 8..30 或 格数）：")
    for va, arg in rows:
        if arg and arg[0] == "imm" and 8 <= arg[1] <= 480:
            print(f"  VA 0x{va:08x}   Random({arg[1]})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
