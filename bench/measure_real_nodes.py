# -*- coding: utf-8 -*-
"""测量生产 win_rate 每次调用的真实节点数分布（解释 10万 cap 为何零触发）。

=================== 背景 ====================
  · 早前 bench 用例实测：最重用例需要 **424,114 节点**
  · 但生产整局扫描：`node_cap=1000` 仍零触发
两者需要对齐。可能：
  A) 生产整局里的 win_rate 局面都很小（节点数 << 1000）
  B) 最重用例那类局面在生产整局中很少出现，本次样本没碰到

本脚本用「不限制 cap」跑整局，逐次记录 win_rate 的**真实节点数**
（通过 C++ 的 visited 计数：让 cap 极大，然后读 nodes_visited）。
若无法直接读，则用二分法反推每次调用的最小可完成 cap。

用法: python bench/measure_real_nodes.py --games 15 --arm baseline
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=15)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "realnodes"), True)

    import ab_policy as ab
    import utils.native as nat
    import utils.probability as prob

    print("=" * 96)
    print("测量生产 win_rate 每次调用的真实节点数")
    print("=" * 96)

    records = []
    orig_cpp = nat.mscore.win_rate

    def hooked(*a, **k):
        """先用极大 cap 跑一次拿到结果，再用二分反推最小可完成 cap。"""
        # 参数位置：clicks, clicks9, res_list, cell_value, ck, num10, a, w, h,
        #           is_play, progress, node_cap
        pos = list(a)
        if len(pos) >= 12:
            pos[11] = 10 ** 12
        else:
            k["node_cap"] = 10 ** 12
        try:
            out = orig_cpp(*pos, **k)
        except Exception as e:                         # noqa: BLE001
            records.append({"nodes": None, "err": str(e)[:50]})
            raise
        # 二分找最小可完成 cap
        lo, hi = 1, 10 ** 12
        # 先确认 1 会失败（否则节点数 <= 1）
        def works(cap):
            p2 = list(a)
            if len(p2) >= 12:
                p2[11] = cap
            else:
                k2 = dict(k)
                k2["node_cap"] = cap
            try:
                orig_cpp(*p2, **k)
                return True
            except Exception:                          # noqa: BLE001
                return False
        if works(1):
            records.append({"nodes": 1})
            return out
        while lo < hi:
            mid = (lo + hi) // 2
            if works(mid):
                hi = mid
            else:
                lo = mid + 1
        records.append({"nodes": lo})
        return out

    nat.mscore.win_rate = hooked
    try:
        for gi in range(args.games):
            sd = args.seed + gi
            n0 = len(records)
            try:
                ab.play_one(sd, 30, 16, 99, args.arm, 80, 200000, None)
            except Exception:                          # noqa: BLE001
                pass
            got = [r["nodes"] for r in records[n0:] if r.get("nodes")]
            print(f"  [{gi + 1}/{args.games}] seed={sd} "
                  f"win_rate {len(got)} 次，节点数 {got}", flush=True)
    finally:
        nat.mscore.win_rate = orig_cpp

    nodes = [r["nodes"] for r in records if r.get("nodes")]
    print()
    print("=" * 96)
    print("汇总")
    print("=" * 96)
    if not nodes:
        print("未采到节点数样本")
        return
    import statistics
    print(f"调用次数 {len(nodes)}")
    print(f"节点数：min {min(nodes)}  中位 {statistics.median(nodes):.0f}  "
          f"max {max(nodes)}")
    print(f"分布：{sorted(nodes)}")
    print()
    print("对照早前 bench 用例读数：最重用例需要 424,114 节点")
    print(f"本次生产整局最大仅 {max(nodes)} 节点")
    if max(nodes) < 100000:
        print()
        print("⇒ 解释：**生产整局里的 win_rate 局面都远小于 bench 采集用例**。")
        print("  bench 用例是按『能进 win_rate』筛出来的极端样本，")
        print("  而生产整局受 wr_threshold=8 限制，进 win_rate 的局面本就不大。")
        print("  ⇒ node_cap=10万 在生产上确实有充足余量（本次样本内）。")
    else:
        print()
        print("⇒ 存在与 bench 用例同量级的局面，10万 cap 应当触发，需复查。")


if __name__ == "__main__":
    main()
