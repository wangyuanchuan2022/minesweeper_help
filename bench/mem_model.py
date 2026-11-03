# -*- coding: utf-8 -*-
"""内存容量模型：位集重构的收益上限与"仍会爆炸"的边界在哪。

=================== 这个脚本回答什么 ===================
位集把每世界内存从 2304B 降到 64B（36x），但**这不足以防止内存爆炸**：
win_rate 的成本是乘积结构

    Peak ≈ nworlds × per_world_bytes × live_nodes

位集只压 per_world_bytes。若 nworlds 与 live_nodes 仍组合增长，总内存照样失控。
本脚本用实测参数算出「在给定内存预算下，位集版能撑到多大的局面」，
以及**超过该边界后必须靠什么兜底**（世界数上限 / 节点数上限 / 回退）。

=================== 口径 ===================
per_world_bytes：
    原实现 Board = (h+2)*(w+2) * 4 字节 = 32*18*4 = **2304 B**（30x16）
    位集版 WorldBits = ceil(w*h/64)*8 = ceil(480/64)*8 = **64 B**
live_nodes：memo 里同时存活的条目数。wr_f 是深度优先 + 全量 memo，
    **memo 从不清理**（见 cpp/mscore.cpp:702 只写不删）⇒ live_nodes == 累计访问节点数。
    这是关键：DFS 的峰值内存不是"深度×每层"，而是**全部已访问节点**因为缓存不清。
每节点额外开销：memo key（字符串 32B + unordered_map 节点约 64B）+ res_list 等，
    实测按 200B/节点估算（含 std::string 与哈希桶）。

用法: python bench/mem_model.py
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A = 30, 16, 99
N_CELLS = W * H
ORIG_PER_WORLD = (W + 2) * (H + 2) * 4          # 2304
BITSET_PER_WORLD = ((N_CELLS + 63) // 64) * 8    # 64
PER_NODE_EXTRA = 200                             # memo key + map 节点估

MEM_BUDGET_MB = 1500.0                           # 与 _wr_memory_ok 的 1.5GB 上限一致


def peak_mb(nworlds, nodes, per_world):
    return (nworlds * per_world + nodes * PER_NODE_EXTRA) / 1048576.0


def max_nworlds(nodes, per_world, budget_mb):
    """在给定节点数下，内存预算允许的最大世界数。"""
    remain = budget_mb * 1048576.0 - nodes * PER_NODE_EXTRA
    if remain <= 0:
        return 0
    return int(remain // per_world)


def main():
    print("=" * 92)
    print(f"内存容量模型（{W}x{H}/{A}雷，预算 {MEM_BUDGET_MB:.0f} MB）")
    print("=" * 92)
    print(f"每世界：原实现 {ORIG_PER_WORLD} B → 位集 {BITSET_PER_WORLD} B "
          f"（{ORIG_PER_WORLD/BITSET_PER_WORLD:.0f}x）")
    print(f"每节点额外开销（memo key + map）：约 {PER_NODE_EXTRA} B")
    print()

    # 实测参数（来自 review/算法更新评估.md 的 12 局生产测量）
    scenarios = [
        ("生产中位（nworlds=4, nodes≈40）", 4, 40),
        ("生产 p90（nworlds=32, nodes≈300）", 32, 300),
        ("生产最大（nworlds=64, nodes≈1562*64）", 64, 1562 * 64),
        ("单用例实测 [3]（nworlds=64, nodes≈1e5）", 64, 99970),
        ("假设放大 10x（nworlds=640）", 640, 100000),
        ("假设放大 100x（nworlds=6400）", 6400, 1000000),
    ]

    print(f"{'场景':<40} {'原 MB':>10} {'位集 MB':>10} {'省':>7} {'位集是否安全':>14}")
    print("-" * 92)
    for name, nw, nodes in scenarios:
        o = peak_mb(nw, nodes, ORIG_PER_WORLD)
        b = peak_mb(nw, nodes, BITSET_PER_WORLD)
        safe = "是" if b <= MEM_BUDGET_MB else "否 → 必须回退"
        print(f"{name:<40} {o:>10.1f} {b:>10.1f} {o/b:>6.1f}x {safe:>14}")

    print()
    print("=" * 92)
    print("关键结论：位集把「安全世界数上限」提高了多少？")
    print("=" * 92)
    print(f"{'活跃节点数':>12} {'原实现可容世界数':>18} {'位集可容世界数':>16} {'提升':>8}")
    print("-" * 92)
    for nodes in (100, 1000, 10000, 100000, 1000000):
        a_o = max_nworlds(nodes, ORIG_PER_WORLD, MEM_BUDGET_MB)
        a_b = max_nworlds(nodes, BITSET_PER_WORLD, MEM_BUDGET_MB)
        r = (a_b / a_o) if a_o > 0 else float("inf")
        print(f"{nodes:>12} {a_o:>18} {a_b:>16} {r:>7.1f}x")

    print()
    print("=" * 92)
    print("为什么位集不足以防爆炸 —— 决定性读数")
    print("=" * 92)
    print("memo 只写不删（cpp/mscore.cpp:702），所以 **live_nodes == 累计访问节点数**。")
    print("而节点数本身随 (候选数 × 世界分叉 × 深度) 组合增长。举例：")
    print()
    print(f"{'n_cand':>8} {'世界数':>8} {'最坏节点量级':>14} {'原内存':>12} {'位集内存':>12}")
    print("-" * 92)
    for nc in (10, 20, 40, 80):
        for nw in (16, 64, 256):
            # 最坏：每层对每个候选都展开，节点 ≈ nc^(depth)，depth 取小值估量级
            # 用实测标定：case[3] n_cand≈18, nodes≈1e5，即约 nc^3 量级
            nodes = min(nc ** 3, 10 ** 8)
            o = peak_mb(nw, nodes, ORIG_PER_WORLD)
            b = peak_mb(nw, nodes, BITSET_PER_WORLD)
            print(f"{nc:>8} {nw:>8} {nodes:>14} {o:>10.1f}MB {b:>10.1f}MB")

    print()
    print("⇒ 即使每世界降到 64B，只要节点数到 1e7 量级，单是 memo 键就吃掉")
    print(f"  {10**7 * PER_NODE_EXTRA / 1024**3:.1f} GB，位集救不了。")
    print("  所以**必须**同时设：node_cap（硬上限，超限抛异常→回退）、")
    print("  world_cap（超限不进 win_rate）、以及把 memo 改为有界 LRU 或按深度回收。")
    print()
    print("建议阈值（按 1.5GB 预算、留 3x 余量反推）：")
    node_cap = int(MEM_BUDGET_MB * 1048576.0 / PER_NODE_EXTRA / 3)
    # world_cap：在 node_cap 个节点的前提下，剩余预算还能装多少世界
    remain = MEM_BUDGET_MB * 1048576.0 / 3.0 - node_cap * PER_NODE_EXTRA
    world_cap = int(remain // BITSET_PER_WORLD) if remain > 0 else 0
    print(f"  node_cap  ≤ {node_cap:,}（约 {node_cap/1e5:.1f}e5）")
    print(f"  world_cap ≤ {world_cap:,}"
          + ("（注意：该值取决于 node_cap 取值，二者共享同一预算）"
             if world_cap < 1000 else ""))
    print("  memo 条目数上限与 node_cap 同量级；超出即抛异常并回退原路径。")
    print()
    print("⚠️ 更要紧的一条：memo 键开销（每节点约 200B）在节点数大时**完全主导**")
    print("   内存，此时压缩世界表示几乎没有收益。要真正压住内存，优先级应是：")
    print("   ① node_cap 硬上限 + 超限回退（最有效，直接封顶）")
    print("   ② memo 有界 LRU / 按深度回收（把 live_nodes 从累计数降到可控量）")
    print("   ③ 位集压缩每世界（只在世界数大、节点数相对小时才有意义）")


if __name__ == "__main__":
    main()
