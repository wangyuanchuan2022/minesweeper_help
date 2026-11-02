# -*- coding: utf-8 -*-
"""确认 Arbiter 首点保护是否真实存在：查布雷函数的调用上下文。

已还原的布雷函数 @0x4d9228（纯拒绝采样）：
    count = 1
    while count <= mineCount:
        x = Random(width  + 1)
        y = Random(height + 1)
        if GetCell(x, y) == UNOPENED:      ; 仅判"未开"
            SetCell(x, y, MINE)
            count++

这个循环**本身没有任何排除规则**。因此"首点必安全"若要成立，
只能来自两种机制之一：
  (A) 调用前先把首点 3x3 标记成非 UNOPENED（拒绝采样自然跳过）
  (B) 布雷后检测首点是雷，再把它挪走

本脚本 dump 调用点 0x531990 所在函数，检查 (A)/(B) 是否存在。

用法: python tools/check_first_click.py
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


def find_prologue(d, va, back=0x800):
    """从 va 向前找最近的函数序言 55 8B EC。"""
    f = va2f(va)
    lo = max(0x400, f - back)
    for i in range(f, lo, -1):
        if d[i] == 0x55 and d[i + 1] == 0x8B and d[i + 2] == 0xEC:
            return f2va(i)
    return None


def dump(d, lo_va, hi_va):
    f0 = va2f(lo_va)
    for i in range(0, hi_va - lo_va, 16):
        chunk = d[f0 + i: f0 + i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        print(f"0x{lo_va+i:08x}  {hexs}")


def main():
    d = open(EXE, "rb").read()
    print("=== 布雷函数 0x4d9228 完整字节 ===")
    dump(d, 0x4D9228, 0x4D92D8)

    print("\n=== 调用点 0x531990 所在函数 ===")
    fs = find_prologue(d, 0x531990)
    print(f"函数起点 ~0x{fs:08x}" if fs else "未找到序言")
    if fs:
        dump(d, fs, min(fs + 0x180, 0x531990 + 0x80))

    print("\n" + "=" * 74)
    print("分析结论（基于上面字节）：")
    print("  · 布雷循环只判 `GetCell(x,y)==UNOPENED`，无任何坐标排除")
    print("  · 若存在首点保护，必须在调用前标记首点区域，或调用后搬移雷")
    print("  · 需要看调用前是否对首点 3x3 写入非 UNOPENED 值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
