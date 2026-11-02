# -*- coding: utf-8 -*-
"""纯静态分析 ms_arbiter.exe（脱壳版）：定位布雷算法。

不依赖 REA/反编译器的冗长输出，直接做：
  1. 解析 PE 段表，建立 VA <-> file offset 映射
  2. 扫描所有对关键字符串的 mov/push 引用，反推函数范围
  3. 对候选函数做轻量 x86 指令模式扫描，找"随机+去重+填充"循环特征

目标：回答"Arbiter 的雷生成算法是否有排除规则"。

用法: python tools/re_arbiter_gen.py
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

    def va2f(self, va):
        rva = va - self.image_base
        for s in self.segs:
            if s["va"] <= rva < s["va"] + s["vs"]:
                return s["ro"] + (rva - s["va"])
        return None

    def f2va(self, f):
        for s in self.segs:
            if s["ro"] <= f < s["ro"] + s["rs"]:
                return self.image_base + s["va"] + (f - s["ro"])
        return None

    def find_str(self, text):
        b = text.encode("latin1")
        i = self.d.find(b)
        return i, (self.f2va(i) if i >= 0 else None)

    def refs_to(self, va):
        """找所有把该 VA 作为立即数装入寄存器/压栈的位置。"""
        out = []
        pat_full = struct.pack("<I", va)
        for opc, name in ((0xB8, "mov eax"), (0x68, "push"), (0xBA, "mov edx"),
                          (0xB9, "mov ecx"), (0xBB, "mov ebx"), (0xA1, "mov [m],eax")):
            pat = bytes([opc]) + pat_full
            pos = 0
            while True:
                m = self.d.find(pat, pos)
                if m < 0:
                    break
                out.append((name, m, self.f2va(m)))
                pos = m + 1
        return out


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    pe = PE(EXE)
    print(f"文件 {EXE}")
    print(f"ImageBase = 0x{pe.image_base:x}")
    print("段表:")
    for s in pe.segs:
        print(f"  {s['name']:<8} RVA 0x{s['va']:08x}  vsz 0x{s['vs']:08x}  "
              f"raw 0x{s['ro']:08x}  rawsz 0x{s['rs']:08x}")
    print("=" * 74)

    keys = [
        ("fatal error: can not generate board", "布雷失败分支"),
        ("fatal error. can not randomize - 2", "RNG 失败分支"),
        ("board restarted in upk cheat mode", "cheat mode"),
        ("Board Must Have 7", "MustHave7 选项"),
        ("Board Must have 8", "MustHave8 选项"),
    ]
    for text, label in keys:
        f, va = pe.find_str(text)
        if f < 0:
            print(f"[{label}] 未找到字符串")
            continue
        refs = pe.refs_to(va) if va else []
        print(f"[{label}]")
        print(f"  字符串: file 0x{f:x}  VA 0x{va:x}  引用数 {len(refs)}")
        for name, rf, rva in refs[:6]:
            print(f"    {name:<12} file 0x{rf:x}  VA 0x{rva:x}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
