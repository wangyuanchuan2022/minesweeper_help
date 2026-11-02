# -*- coding: utf-8 -*-
"""定位 ms_arbiter.exe 的布雷函数：从 Delphi Random 的调用点反查。

已确认（REA 反编译 0x4033c8）：
    RandSeed = RandSeed * 0x08088405 + 1        (DAT_00544008)
    Random(n) = (uint64)n * RandSeed >> 32

本脚本：
  1. 找出所有 `call 0x4033c8`（E8 rel32）的调用点
  2. 把调用点按函数聚类（用简单的 prologue 回溯）
  3. 统计每个候选函数里 Random 的调用次数与参数

布雷函数的典型特征：Random 调用次数与"雷数"相关，且附近有循环。

用法: python tools/find_mine_placer.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")

RANDOM_VA = 0x4033C8
RANDSEED_VA = 0x544008
LCG_MULT = 0x08088405


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


def find_calls_to(pe, target_va):
    """找所有 call target_va（E8 rel32）。"""
    out = []
    start = pe.code["ro"]
    end = start + pe.code["rs"]
    for f in range(start, end - 4):
        if pe.d[f] != 0xE8:
            continue
        rel = struct.unpack_from("<i", pe.d, f + 1)[0]
        call_va = pe.f2va(f)
        if call_va is None:
            continue
        if call_va + 5 + rel == target_va:
            out.append(f)
    return out


def scan_randseed_refs(pe):
    """找所有直接引用 RandSeed (0x544008) 的指令。"""
    pat = struct.pack("<I", RANDSEED_VA)
    hits = []
    pos = pe.code["ro"]
    while True:
        i = pe.d.find(pat, pos, pe.code["ro"] + pe.code["rs"])
        if i < 0:
            break
        hits.append((i, pe.f2va(i)))
        pos = i + 1
    return hits


def approx_func_start(pe, va, back=0x600):
    """从调用点向前回溯最近的函数序言（push ebp; mov ebp,esp 或 push 系列）。"""
    f = pe.va2f(va)
    if f is None:
        return None
    lo = max(pe.code["ro"], f - back)
    best = None
    for i in range(f, lo, -1):
        # Delphi 常见序言: 55 8B EC (push ebp; mov ebp,esp)
        if pe.d[i] == 0x55 and pe.d[i + 1] == 0x8B and pe.d[i + 2] == 0xEC:
            best = i
            break
        # 或 ret 之后的下一条指令
        if pe.d[i] == 0xC3 or pe.d[i] == 0xC2:
            best = i + 1 if pe.d[i] == 0xC3 else i + 3
            break
    return pe.f2va(best) if best else None


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    pe = PE(EXE)
    print(f"目标 {EXE}")
    print(f"Random 函数 VA = 0x{RANDOM_VA:x}")
    print("=" * 74)

    calls = find_calls_to(pe, RANDOM_VA)
    print(f"[call Random] 共 {len(calls)} 处")
    groups = {}
    for f in calls:
        va = pe.f2va(f)
        fs = approx_func_start(pe, va)
        groups.setdefault(fs, []).append(va)
    print(f"归并到 {len(groups)} 个候选函数：")
    rows = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    for fs, sites in rows[:15]:
        print(f"  函数 ~0x{fs:08x}  Random 调用 {len(sites):3d} 次   "
              f"首址 0x{min(sites):08x}")

    print("\n" + "=" * 74)
    seed_refs = scan_randseed_refs(pe)
    print(f"[RandSeed 0x{RANDSEED_VA:x} 直接引用] 共 {len(seed_refs)} 处")
    for f, va in seed_refs[:10]:
        ctx = pe.d[f - 6:f + 6].hex()
        print(f"  VA 0x{va:08x}  上下文 {ctx}")

    print("\n" + "=" * 74)
    print("判读提示：")
    print("  · 布雷函数通常有'循环 + Random(宽)/Random(高) + 判重'结构")
    print("  · 若某个函数同时引用 RandSeed 与大量 call Random，极可能是布雷")
    print("  · Delphi 标准布雷无排除规则；Arbiter 可能额外排除首点 3x3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
