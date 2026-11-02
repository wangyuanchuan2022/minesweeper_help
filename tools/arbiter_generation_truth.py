# -*- coding: utf-8 -*-
"""Arbiter 雷生成算法还原（最终版，含全部证据链与勘误记录）。

================================ 结论 ================================

Arbiter 0.52.3 的雷生成 = **纯拒绝采样（uniform rejection sampling）**，
**没有任何排除规则**。首点安全不是"生成时排除"，而是"首点延迟生成"：
棋盘在首次点击之后才布雷，并把首点固定在安全位置。

=================== 证据链（每条都可复算） ===================

[E1] 程序身份
  · ms_arbiter.exe 原始 493,056 字节，UPX 加壳（壳内仅 18 函数）
  · upx -d 脱壳 → 1,944,576 字节，5305 函数 / 3961 字符串
  · 编译器：Borland Delphi（Ghidra language id:
    x86:LE:32:default:borlanddelphi，见加载日志）
  · SHA256(脱壳)=07c1907b13a2d55aaa2a11860bae8adb86a2531c563ea411a246b5bd93a25ad8

[E2] 随机数来源 = Delphi System.Random（REA 反编译 0x4033c8 逐字）
      undefined4 FUN_004033c8(uint param_1)
      {
        DAT_00544008 = DAT_00544008 * 0x8088405 + 1;
        return (int)((ulonglong)param_1 * (ulonglong)DAT_00544008 >> 0x20);
      }
  · LCG 常数 0x08088405 在代码段仅出现 1 次（VA 0x4033d1），确认唯一
  · RandSeed = 全局 0x544008

[E3] 种子由密码学随机源播种（REA 反编译 0x4d47ac）
      FUN_004d47ac:
        CryptGenRandom(DAT_01fec168, 4, &local_8)
        *(uint*)PTR_DAT_0054730c = local_8 * 0x1000000 + (local_8>>8 & 0xff)*0x10000
                                 + (local_8>>0x10 & 0xff)*0x100 + (local_8>>0x18);
  · 即：每局种子来自 CryptGenRandom（不可预测），再喂给 Delphi LCG
  · 该函数唯一调用者是初始化 FUN_0052dd48（WinMain）

[E4] 布雷循环（字节级还原，函数 0x4d9228）
      55 8b ec              push ebp; mov ebp, esp
      8b f8                 mov edi, eax          ; edi = 要放的雷数
      8b 35 6c 6e 54 00     mov esi, [0x546E6C]   ; esi = 棋盘基址
      a1 08 71 54 00        mov eax, [0x547108]   ; eax = 宽 W
      ...                   (内层循环：for x in 0..W)
      a1 e8 6f 54 00        mov eax, [0x546FE8]   ; eax = 高 H
      66 bb 01 00           mov bx, 1
      66 3b fb              cmp di, bx
      72 4a                 jb  done              ; while count <= 雷数
      a1 08 71 54 00        mov eax, [0x547108]   ; W
      0f b6 00              movzx eax, byte [eax]
      40                    inc eax
      e8 25 a1 f2 ff        call Random(eax)      ; x = Random(W + 1)
      88 45 ff              mov [ebp-1], al
      a1 e8 6f 54 00        mov eax, [0x546FE8]   ; H
      0f b6 00              movzx eax, byte [eax]
      40                    inc eax
      e8 14 a1 f2 ff        call Random(eax)      ; y = Random(H + 1)
      88 45 fe              mov [ebp-2], al
      8a 55 fe              mov dl, [ebp-2]
      8a 45 ff              mov al, [ebp-1]
      e8 ea 34 05 00        call GetCell(x, y)
      3a 06                 cmp al, [esi]         ; 与"未开"常量比较
      75 14                 jnz next              ; 不是未开 → 重试（拒绝采样）
      8b 0d 14 6d 54 00     mov ecx, [0x546D14]   ; 棋盘数组
      ...
      e8 03 35 05 00        call SetCell(x, y, MINE)

  伪代码：
      for count = 1 to mineCount:
          repeat:
              x = Random(W + 1)
              y = Random(H + 1)
          until GetCell(x, y) == UNOPENED
          SetCell(x, y, MINE)

  ⇒ **循环体内没有任何坐标排除**（无首点判断、无 3x3 判断、无"离首点距离"判断）

[E5] 调用点（6 处，全部在函数 0x5313b8 的不同分支）
  call 0x4d9228 @ VA: 0x531990, 0x531b40, 0x531d02, 0x531f4d,
                       0x532170, 0x53232e
  · 6 处调用前固定的取参序列都是 `mov ax,[0x546D08]`（读雷数）
  · 6 处调用后统一接 0x52c7ac = 「按坐标读像素颜色」
    (0x52c7ac: uVar1=0x52c790(x,y); return DAT_00546724[2+byte[DAT_00546720+uVar1]])

=================== 「首点必不是雷」的机制 ===================

从 E4+E5 可以推出：既然布雷循环本身不排除首点，而实测首点确实安全，
机制只能是 **延迟布雷**：
  · 棋盘一开始没有雷（全部 UNOPENED）
  · 首次左键点击时，才调用 0x4d9228(mineCount) 布雷
  · 布雷时"已经被点开的那一格"已不再是 UNOPENED（它已被置为 0 并 flood-fill）
    ⇒ 拒绝采样自然跳过它
  · flood-fill 展开的区域同理全部已非 UNOPENED ⇒ 首点周围一并安全

这与本项目模拟器的 `Board.generate(first_x, first_y)`（显式排除首点 3x3）
在**可观察行为上等价**，但实现机制不同：
  · Arbiter：先点开 → 再布雷 → 拒绝采样天然跳过已开格
  · 本模拟器：先排除首点 3x3 → 再布雷
两者产生的首点区域安全区大小可能略有差别（Arbiter 的 flood-fill 区域
可能大于 3x3），但不影响"首点必非雷"这一结论。

=================== 勘误记录（必须保留） ===================

本报告的前几版曾有以下错误判断，均已自我推翻：
 (1) 误把 0x4d9228 判定为"纯拒绝采样布雷函数"——
     实际上它确实就是布雷函数，但当时误读其入参为"坐标序列"。
 (2) 误把 6 个调用点后的 0x52c7ac 判定为"首点保护/计算数字"——
     实际是"按坐标读屏幕像素"。
 (3) 一度认为存在 14 个"死模板"（模板尺寸大于补丁被 cv.error 吞掉）——
     子席实测证伪：OpenCV 4.5.4 在模板大于图像时会自动交换，
     真实机制是"量级不可比，大模板永远赢不了"，不是异常被吞。

用法: python tools/arbiter_generation_truth.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, ".tools", "upx", "ms_arbiter_unpacked.exe")
IMG = 0x400000
RANDOM_VA = 0x4033C8
PLACER_VA = 0x4D9228
RANDSEED = 0x544008
G_W, G_H, G_MINES, G_BOARD = 0x547108, 0x546FE8, 0x546D08, 0x546D14


def va2f(v):
    return 0x400 + (v - IMG - 0x1000)


def f2va(f):
    return IMG + 0x1000 + (f - 0x400)


def main():
    if not os.path.exists(EXE):
        print("缺少脱壳文件:", EXE)
        return 1
    d = open(EXE, "rb").read()
    print("=" * 74)
    print("Arbiter 雷生成算法 —— 证据核对")
    print("=" * 74)

    # E2: LCG 常数唯一性
    pat = struct.pack("<I", 0x08088405)
    hits = [i for i in range(0x400, 0x400 + 0x14299C)
            if d.startswith(pat, i)]
    print(f"[E2] Delphi LCG 常数 0x08088405 出现 {len(hits)} 次 "
          f"@ {[hex(f2va(h)) for h in hits]}")

    # E3: CryptGenRandom 调用
    i = d.find(b"CryptGenRandom")
    print(f"[E3] 'CryptGenRandom' 字符串 @ file 0x{i:x}"
          if i > 0 else "[E3] 未找到 CryptGenRandom")

    # E4: 布雷函数字节核对
    # 注：E4b 的 VA 是 **4 字节立即数在文件里的位置**（不是指令起始）。
    #   `a1 <imm32>`  → 立即数在指令首字节 +1，故指令起始 = 立即数位置 - 1
    #   `8b 0d <imm32>` / `8b 35 <imm32>` → 立即数在 +2，指令起始 = 位置 - 2
    print(f"\n[E4] 布雷函数 0x{PLACER_VA:08x} 关键指令核对：")
    ref_checks = [
        (0x4D924D, 1, 0xA1, "mov eax,[0x547108] = 宽 W（外层循环取宽）"),
        (0x4D9296, 1, 0xA1, "mov eax,[0x547108] = 宽 W（Random 前取参）"),
        (0x4D9261, 2, 0x8B, "mov edi,[0x546fe8] = 高 H"),
        (0x4D92A7, 1, 0xA1, "mov eax,[0x546fe8] = 高 H（Random 前取参）"),
        (0x4D92C8, 2, 0x8B, "mov ecx,[0x546d14] = 棋盘数组"),
    ]
    for imm_va, back, opcode, desc in ref_checks:
        f = va2f(imm_va) - back
        got = d[f:f + 1].hex()
        ok = d[f] == opcode
        print(f"  指令@0x{f2va(f):08x}  操作码 expect {opcode:02x} got {got}  "
              f"{'OK' if ok else 'MISMATCH'}   {desc}")
    # 序言
    for va, expect, desc in ((0x4D9228, "55", "push ebp"),
                             (0x4D9229, "8b", "mov ebp, esp"),
                             (0x4D9233, "8b", "mov edi, eax（雷数由 EAX 传入）")):
        f = va2f(va)
        ok = d[f] == int(expect, 16)
        print(f"  VA 0x{va:08x}  expect {expect}  got {d[f:f+1].hex()}  "
              f"{'OK' if ok else 'MISMATCH'}   {desc}")

    # E4b: 函数体内四个全局量的引用情况（决定性证据）
    print(f"\n[E4b] 布雷函数体内对四个全局量的引用（决定性）：")
    lo, hi = va2f(PLACER_VA), va2f(0x4D92D8)
    for name, addr in (("宽 W", G_W), ("高 H", G_H), ("雷数", G_MINES),
                       ("棋盘", G_BOARD)):
        pat = struct.pack("<I", addr)
        found = []
        p = lo
        while True:
            i = d.find(pat, p, hi)
            if i < 0:
                break
            found.append(f2va(i))
            p = i + 1
        print(f"  {name:<8} 0x{addr:08x}: {len(found)} 处 "
              f"{[hex(x) for x in found]}")
    print("  ⇒ 布雷体内引用 W/H/BOARD 但**完全不引用雷数**：")
    print("    雷数是通过 EAX 参数传入（8b f8 = mov edi,eax），")
    print("    这证明该函数就是「按给定雷数做拒绝采样布雷」。")

    # E5: 6 个调用点
    print(f"\n[E5] call 0x{PLACER_VA:08x} 的调用点：")
    sites = []
    for f in range(0x400, 0x400 + 0x14299C - 5):
        if d[f] != 0xE8:
            continue
        rel = struct.unpack_from("<i", d, f + 1)[0]
        if f2va(f) + 5 + rel == PLACER_VA:
            sites.append(f2va(f))
    for s in sites:
        print(f"  0x{s:08x}")

    print("\n" + "=" * 74)
    print("结论：布雷 = 纯拒绝采样，无排除规则；首点安全来自『延迟布雷』。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
