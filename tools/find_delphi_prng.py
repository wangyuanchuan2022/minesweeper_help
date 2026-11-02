# -*- coding: utf-8 -*-
"""在脱壳后的 ms_arbiter.exe 中定位 Delphi 伪随机数生成器（PRNG）。

背景：已确认程序是 Borland Delphi 编译。Delphi 的 System.Random 使用
经典 LCG：
    RandSeed := RandSeed * 0x08088405 + 1
    Random(n) := (RandSeed * n) shr 32   （Delphi 7+）
或旧式：
    Random(n) := HiWord(RandSeed) * n shr 16

因此只要在代码段里找到常数 0x08088405 的立即数引用，就能精确定位 PRNG，
再顺着它的调用者往上找，就是布雷循环。

同时搜索 CryptGenRandom 的调用链，确认种子是否只初始化一次。

用法: python tools/find_delphi_prng.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")


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

    def f2va(self, f):
        for s in self.segs:
            if s["ro"] <= f < s["ro"] + s["rs"]:
                return self.image_base + s["va"] + (f - s["ro"])
        return None

    @property
    def code(self):
        for s in self.segs:
            if s["name"] == "CODE":
                return s
        return None


def scan_imm32(pe, value, label, seg_only="CODE"):
    """扫描所有把 value 作为 32 位立即数出现的指令（多种寻址形式）。"""
    cs = pe.code
    hay = pe.d[cs["ro"]: cs["ro"] + cs["rs"]]
    pat = struct.pack("<I", value)
    hits = []
    pos = 0
    while True:
        i = hay.find(pat, pos)
        if i < 0:
            break
        hits.append(i)
        pos = i + 1
    print(f"\n[{label}] 立即数 0x{value:08x} 出现 {len(hits)} 次")
    for h in hits[:12]:
        foff = cs["ro"] + h
        va = pe.f2va(foff)
        # 看前后几个字节判断指令形式
        ctx = pe.d[max(0, foff - 3): foff + 7]
        print(f"  VA 0x{va:08x}  前置字节 {pe.d[foff-3:foff].hex()}  "
              f"上下文 {ctx.hex()}")
    return hits


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    pe = PE(EXE)
    print(f"目标: {EXE}")
    print(f"ImageBase 0x{pe.image_base:x}, CODE RVA 0x{pe.code['va']:x} "
          f"raw 0x{pe.code['ro']:x} size 0x{pe.code['rs']:x}")

    # Delphi System.Random 的 LCG 常数
    scan_imm32(pe, 0x08088405, "Delphi LCG multiplier")

    # 备选：老版 Delphi 用的常数
    scan_imm32(pe, 0x08088405 & 0xFFFFFFFF, "dup")

    # Delphi Random 里常见的 0x7FFF 掩码 / 0xFFFF
    scan_imm32(pe, 0x00007FFF, "0x7FFF mask")

    print("\n" + "=" * 74)
    print("说明：若 Delphi LCG 常数存在，说明程序用 System.Random 生成序列；")
    print("      若不存在，则可能直接用 CryptGenRandom 逐格取雷位。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
