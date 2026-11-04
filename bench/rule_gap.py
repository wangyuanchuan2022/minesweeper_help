# -*- coding: utf-8 -*-
"""首点规则对胜率的影响：Arbiter（延迟布雷，只保 1 格）vs 现代规则（3x3 安全）。

=================== 为什么这是当前最关键的一块 ====================
2000 局逐局数据显示（bench/analyze_death_stage.py）：
  · 1291 局踩雷，其中 **295 局在 opened=1 时死亡**
  · 即「首点只开出 1 格 → 下一步就踩雷」占全部对局的 **14.75%**
这与 Arbiter 的延迟布雷规则完全吻合：首点只保证本格安全，
P(首格数字为 0) 仅约 15%，其余 85% 的开局只有 1 个已知数字格。

外部参照（MinesweeperHQ，170 万局模拟，
https://minesweeperhq.com/minesweeper-odds/）：
  在现代规则（首点及 8 邻域无雷）下，完美逻辑 + 总是猜最安全格的玩家
  在 Expert（30x16, 99 雷）上胜率 **50.7%**，平均需要 2.65 次猜测。

⇒ 必须回答的问题：**我们当前 35.45% 的差距，有多少来自"规则更苛刻"、
有多少来自"算法不够好"？** 若主要来自规则，则 50% 在 Arbiter 上结构性不可达。

=================== 做法（同 seed 配对，只改一个变量）====================
用**同一条生产流水线**（ab_policy.play_one，含真实 number5_1/part_solve/
win_rate/pbs），只把 Board.generate 的首点规则换掉：
  rule="deferred"  只把首点本格排除在布雷池外（Arbiter 真实规则）
  rule="safe3x3"   首点及其 8 邻域排除（现代 Windows/网页版规则）
同 seed 配对比较，因此棋盘差异被消掉，只剩规则效应。

用法:
  python bench/rule_gap.py --games 500 --workers 12
"""
import argparse
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def sign_test(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def install_rule(rule):
    """把 sim.minesweeper_sim.Board.generate 换成指定首点规则。返回 restore。"""
    from sim.minesweeper_sim import Board
    orig = Board.generate

    def patched(self, first_x, first_y):
        w, h, a = self.w, self.h, self.a
        if rule == "deferred":
            safe = {(first_x, first_y)}
        elif rule == "safe3x3":
            safe = {(first_x + dx, first_y + dy)
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                    if 0 <= first_x + dx < w and 0 <= first_y + dy < h}
        else:
            raise ValueError(rule)
        pool = [(x, y) for y in range(h) for x in range(w)
                if (x, y) not in safe]
        if len(pool) < a:
            raise ValueError("雷数过多")
        self.mines = set(self.rng.sample(pool, a))
        self.generated = True

    Board.generate = patched

    def restore():
        Board.generate = orig
    return restore


def first_click_stats(rule, n=4000, seed=0):
    """纯几何统计：首点会开出几格、首格数字分布。不跑求解器。"""
    import random
    from sim.minesweeper_sim import Board
    restore = install_rule(rule)
    rng = random.Random(seed)
    w, h, a = 30, 16, 99
    sizes, nums = [], []
    try:
        for _ in range(n):
            b = Board(w, h, a, rng)
            b.open(w // 2, h // 2)
            sizes.append(b.opened_count)
            nums.append(b._count(w // 2, h // 2))
    finally:
        restore()
    return sizes, nums


def run_rule(rule, seeds, cfg):
    """跑一个规则臂，返回 {seed: {...}}。"""
    from sim.eval_harness import install_stubs
    install_stubs(cfg["state_dir"], True)
    import ab_policy as ab
    restore = install_rule(rule)
    out = {}
    try:
        for sd in seeds:
            try:
                r = ab.play_one(sd, cfg["w"], cfg["h"], cfg["a"], "baseline",
                                cfg["limit"], cfg["world_cap"], cfg["node_cap"])
                out[sd] = {"won": bool(r.get("won")),
                           "died": bool(r.get("died")),
                           "opened": r.get("opened"),
                           "steps": r.get("steps"),
                           "error": r.get("error")}
            except Exception as e:                      # noqa: BLE001
                out[sd] = {"won": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        restore()
    return out



def _atomic_json_dump(obj, path):
    """原子写 JSON：先写 .tmp，fsync 后再 replace。

    本次实跑出现过"日志含 KeyboardInterrupt、但正式 JSON 已存在"的
    并存状态。文件存在不能证明实验完整，因此写入必须原子并带
    complete 标记供核对。
    """
    import json as _json
    import os as _os
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(obj, f, ensure_ascii=False)
        f.flush()
        try:
            _os.fsync(f.fileno())
        except OSError:
            pass
    _os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    state_dir = os.path.join(ROOT, "state", "ab", "rulegap")
    os.makedirs(state_dir, exist_ok=True)
    cfg = {"w": args.w, "h": args.h, "a": args.a, "limit": args.limit,
           "world_cap": args.world_cap, "node_cap": args.node_cap,
           "state_dir": state_dir}
    seeds = [args.seed + i for i in range(args.games)]

    print("=" * 98)
    print(f"首点规则对胜率的影响：同 seed 配对，{args.games} 局 × 2 规则")
    print("=" * 98)

    # ---- 第一步：纯几何统计首点形态（不跑求解器，先建立机制理解）----
    print("\n【几何统计】首点开出格数分布（每规则 4000 局）")
    geo = {}
    for rule in ("deferred", "safe3x3"):
        sizes, nums = first_click_stats(rule)
        geo[rule] = {"sizes": sizes, "nums": nums}
        n1 = sum(1 for s in sizes if s == 1)
        avg = sum(sizes) / len(sizes)
        print(f"  {rule:<10} 平均展开 {avg:>6.1f} 格   "
              f"只开出 1 格的比例 {n1/len(sizes)*100:>6.2f}%   "
              f"首格数字=0 的比例 {sum(1 for x in nums if x==0)/len(nums)*100:>6.2f}%")

    # ---- 第二步：跑求解器 ----
    print("\n【求解器】生产流水线跑两种规则（同 seed 配对）")
    import time
    got = {}
    for rule in ("deferred", "safe3x3"):
        t0 = time.time()
        got[rule] = run_rule(rule, seeds, cfg)
        ok = [v for v in got[rule].values() if not v.get("error")]
        w = sum(1 for v in ok if v["won"])
        print(f"  {rule:<10} {w}/{len(ok)} = {w/max(1,len(ok))*100:6.2f}%   "
              f"耗时 {time.time()-t0:.0f}s", flush=True)

    print("\n" + "=" * 98)
    print("胜率（Wilson 95%CI）")
    print("=" * 98)
    summary = {}
    for rule in ("deferred", "safe3x3"):
        ok = {s: v for s, v in got[rule].items() if not v.get("error")}
        w = sum(1 for v in ok.values() if v["won"])
        lo, hi = wilson(w, len(ok))
        summary[rule] = {"wins": w, "n": len(ok), "ci": [lo, hi]}
        print(f"  {rule:<10} {w}/{len(ok)} = {w/len(ok)*100:6.2f}%  "
              f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")

    d = summary["safe3x3"]
    f = summary["deferred"]
    gap = (d["wins"] - f["wins"]) / max(1, f["n"]) * 100
    print(f"\n  规则效应 = {gap:+.2f}pp（现代规则 − Arbiter 规则）")
    print(f"  ⇒ 这是**不可通过算法挽回**的部分：Arbiter 规则本身就要求多冒一次险。")

    # ---- 配对检验 ----
    b = c = 0
    n = 0
    for s in seeds:
        x, y = got["deferred"].get(s), got["safe3x3"].get(s)
        if not x or not y or x.get("error") or y.get("error"):
            continue
        n += 1
        if y["won"] and not x["won"]:
            b += 1
        elif x["won"] and not y["won"]:
            c += 1
    p = sign_test(b, c)
    print(f"\n  配对 McNemar：现代规则赢/Arbiter 输 = {b}，反向 = {c}，"
          f"n={n}，p={p:.4f}")

    # ---- 死亡位置对比 ----
    print("\n" + "=" * 98)
    print("死亡位置对比：踩雷局『死亡时已开格数』")
    print("=" * 98)
    for rule in ("deferred", "safe3x3"):
        died = [v for v in got[rule].values()
                if v.get("died") and v.get("opened") is not None]
        if not died:
            continue
        op = sorted(v["opened"] for v in died)
        imm = sum(1 for v in died if v["opened"] <= 1)
        print(f"  {rule:<10} 踩雷 {len(died):>4} 局  "
              f"中位 opened {op[len(op)//2]:>3}  "
              f"opened<=1 占 {imm/len(died)*100:>5.2f}%  "
              f"opened>=370 占 "
              f"{sum(1 for v in died if v['opened']>=370)/len(died)*100:>5.2f}%")

    # ---- 上限推算 ----
    print("\n" + "=" * 98)
    print("Arbiter 规则下的胜率上限推算")
    print("=" * 98)
    fd = got["deferred"]
    okd = [v for v in fd.values() if not v.get("error")]
    imm_dead = sum(1 for v in okd if v.get("died") and (v.get("opened") or 0) <= 1)
    print(f"  实测：{len(okd)} 局中 {imm_dead} 局在 opened<=1 时死"
          f"（{imm_dead/len(okd)*100:.2f}%）")
    print(f"  ⇒ 这部分是『首点只开 1 格 → 下一步被迫盲猜』的结构性代价。")
    print(f"  ⇒ 即使其余全胜，Arbiter 规则下胜率上界 ≈ "
          f"{(1 - imm_dead/len(okd))*100:.2f}%"
          f"（还需再扣后续强制猜测的损失）")

    out = args.out or os.path.join(ROOT, "state", "ab", "rule_gap.json")
    _atomic_json_dump({"games": args.games, "seed": args.seed,
                       "summary": summary, "gap_pp": gap,
                       "mcnemar": {"modern_only": b, "arbiter_only": c,
                                   "n": n, "p": p},
                       "results": got,
                       "complete": True,
                       "n_done": {arm: len(v) for arm, v in got.items()}},
                      out)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
