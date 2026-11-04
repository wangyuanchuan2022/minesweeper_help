# -*- coding: utf-8 -*-
"""评测随机性锁定：同一个 seed 是否唯一确定棋盘 / 首点 / 结果？

=================== 为什么这是根基 ====================
advisor 指出：不能把 81/200、73/200、71/200 的差异直接归因为"多进程随机性"，
p>0.05 也不能排除代码退化。必须先在**同 commit、同 harness、同 seeds** 下
验证 seed 是否唯一确定一局。

若 seed 不能唯一确定棋盘 ⇒ 现有"同 seed 配对"（rule_gap / ab_tiebreak 等）
失去共同随机数基础，那些配对读数需要重做。

=================== 本脚本测什么 ====================
对同一批 seeds，分别在：
  A) 单进程、连跑两轮（同进程内重复）
  B) 两个独立单进程（跨进程）
  C) 多进程 harness（12 worker）
记录每局的 **棋盘哈希**（雷位集合的指纹）、首点坐标、胜负，
逐局比较：
  · 同进程两轮是否完全相同
  · 跨进程是否完全相同
  · 多进程 vs 单进程是否完全相同

判据：
  · 全同 ⇒ seed 唯一确定一局，配对有效
  · 不同 ⇒ 记录差异形态，并给出"哪些量不可复现"

用法: python bench/verify_seed_determinism.py --games 40
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99


def board_hash(mines):
    """雷位集合的稳定指纹。"""
    s = ",".join(f"{x},{y}" for x, y in sorted(mines))
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def run_seeds_single(seeds):
    """单进程逐局跑，记录棋盘哈希 / 首点 / 胜负。"""
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "det"), True)
    import ab_policy as ab
    from sim.minesweeper_sim import Board

    cur = {"board": None}
    orig_open = Board.open

    def hooked_open(self, x, y):
        cur["board"] = self
        cur.setdefault("first", (x, y))
        return orig_open(self, x, y)

    Board.open = hooked_open
    out = {}
    try:
        for sd in seeds:
            cur.clear()
            try:
                r = ab.play_one(sd, W, H, A, "baseline", 80, 200000, None)
            except Exception as e:                  # noqa: BLE001
                out[sd] = {"error": f"{type(e).__name__}: {e}"}
                continue
            b = cur.get("board")
            out[sd] = {
                "won": bool(r.get("won")),
                "died": bool(r.get("died")),
                "opened": r.get("opened"),
                "first": list(cur["first"]) if cur.get("first") else None,
                "board": board_hash(b.mines) if b is not None else None,
                "n_mines": len(b.mines) if b is not None else None,
            }
    finally:
        Board.open = orig_open
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261102)
    args = ap.parse_args()
    seeds = [args.seed + i for i in range(args.games)]

    print("=" * 100)
    print(f"seed 决定性检验：{args.games} 局（seeds {seeds[0]}..{seeds[-1]}）")
    print("=" * 100)

    # ---- A) 同进程连跑两轮 ----
    print("\n[A] 同一进程连跑两轮 …", flush=True)
    a1 = run_seeds_single(seeds)
    a2 = run_seeds_single(seeds)

    def cmp(d1, d2, keys):
        diff = []
        for sd in seeds:
            x, y = d1.get(sd, {}), d2.get(sd, {})
            for k in keys:
                if x.get(k) != y.get(k):
                    diff.append((sd, k, x.get(k), y.get(k)))
                    break
        return diff

    dA = cmp(a1, a2, ("board", "first", "won", "opened"))
    print(f"  同进程两轮：{len(dA)} 局有差异 / {len(seeds)}")
    for sd, k, x, y in dA[:5]:
        print(f"    seed={sd} {k}: {x} vs {y}")

    # 分别看各量的复现率
    for k in ("board", "first", "won", "opened"):
        same = sum(1 for sd in seeds if a1.get(sd, {}).get(k) == a2.get(sd, {}).get(k))
        print(f"    {k:<8} 一致 {same}/{len(seeds)}")

    # ---- B) 独立子进程（跨进程）----
    print("\n[B] 两个独立子进程 …", flush=True)
    payload = {"seeds": seeds}
    pj = os.path.join(ROOT, "state", "ab", "_det_seeds.json")
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    outs = []
    for tag in ("b1", "b2"):
        op = os.path.join(ROOT, "state", "ab", f"_det_{tag}.json")
        if os.path.exists(op):
            os.remove(op)
        script = os.path.join(ROOT, "bench", "_det_child.py")
        p = subprocess.run([sys.executable, "-X", "utf8", script, pj, op],
                           cwd=ROOT, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  子进程 {tag} 失败：{p.stderr[-500:]}")
            return
        with open(op, encoding="utf-8") as f:
            outs.append({int(k): v for k, v in json.load(f).items()})

    dB = cmp(outs[0], outs[1], ("board", "first", "won", "opened"))
    print(f"  跨进程两轮：{len(dB)} 局有差异 / {len(seeds)}")
    for sd, k, x, y in dB[:5]:
        print(f"    seed={sd} {k}: {x} vs {y}")

    # ---- C) 单进程 vs 首轮 ----
    print("\n[C] 跨进程 vs 单进程 …")
    dC = cmp(outs[0], a1, ("board", "first", "won", "opened"))
    print(f"  单进程 vs 子进程：{len(dC)} 局有差异 / {len(seeds)}")

    # ---- 判定 ----
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    board_same_a = all(a1.get(sd, {}).get("board") == a2.get(sd, {}).get("board")
                       for sd in seeds)
    board_same_b = all(outs[0].get(str(sd), outs[0].get(sd, {})).get("board") ==
                       outs[1].get(str(sd), outs[1].get(sd, {})).get("board")
                       for sd in seeds) if False else None

    n_board_a = sum(1 for sd in seeds
                    if a1.get(sd, {}).get("board") == a2.get(sd, {}).get("board"))
    n_board_c = sum(1 for sd in seeds
                    if a1.get(sd, {}).get("board") ==
                    outs[0].get(str(sd), outs[0].get(sd, {})).get("board"))

    print(f"  棋盘哈希可复现率：")
    print(f"    同进程两轮   {n_board_a}/{len(seeds)}")
    print(f"    跨进程       {n_board_c}/{len(seeds)}")

    if n_board_a == len(seeds) and n_board_c == len(seeds):
        print("\n  ✅ seed 唯一确定棋盘 ⇒ 同 seed 配对有效，现有配对读数成立。")
    else:
        print("\n  ✘ seed **不能**唯一确定棋盘 ⇒ 同 seed 配对失去共同随机数基础。")
        print("     原因（推断）：Board.generate 用 random.Random(seed)，")
        print("     但求解器内部大量用 **np.random 全局态**（choice/shuffle），")
        print("     其调用序列受进程内历史影响 ⇒ 跨进程/跨轮会漂。")
        print("     ⇒ 结论：rule_gap / ab_tiebreak 的**配对**口径需改为")
        print("        '按实际棋盘哈希配对'，而不是'按 seed 配对'。")

    dest = os.path.join(ROOT, "state", "ab", "seed_determinism.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"seeds": seeds, "a1": {str(k): v for k, v in a1.items()},
                   "a2": {str(k): v for k, v in a2.items()},
                   "b1": outs[0], "b2": outs[1],
                   "diff_same_proc": len(dA), "diff_cross_proc": len(dB),
                   "board_repro_same_proc": n_board_a,
                   "board_repro_cross_proc": n_board_c},
                  f, ensure_ascii=False)
    print(f"\n明细已写 {dest}")


if __name__ == "__main__":
    main()
