# -*- coding: utf-8 -*-
"""验证 ab_first_click 的仪器：first 参数真的生效了吗？

=================== 必须查的三件事 ====================
1. `play_one(first=...)` 是否真的把首点点在指定坐标
2. 不同 first 下棋盘是否真的不同（延迟布雷池不同 ⇒ 雷位不同）
3. 首点后的 `opened` 分布是否与理论一致：
     理论 P(首格数字=0)：corner 49.85% / edge 31.25% / center 15.45%
   ⇒ 对应「首点开出 >1 格」的比例应接近这三个数

⚠️ 若 opened 分布与理论不符，说明 first 没生效或统计错位，实验作废。

用法: python bench/verify_first_click_wiring.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99
FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✔' if cond else '✘'} {name}"
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "verify_fc"), True)
    import ab_policy as ab
    from sim.minesweeper_sim import Board

    print("=" * 96)
    print("仪器验证：first 参数是否生效")
    print("=" * 96)

    # ---- T1: 首点坐标是否真的被采纳 ----
    print("\n[T1] Board.open 的首个坐标")
    cur = {"first": None}
    orig_open = Board.open

    def h_open(self, x, y):
        if cur["first"] is None:
            cur["first"] = (x, y)
        return orig_open(self, x, y)

    Board.open = h_open
    got = {}
    try:
        for arm, (fx, fy) in (("center", (15, 8)), ("corner00", (0, 0)),
                              ("corner01", (29, 0)), ("edge_top", (15, 0))):
            cur["first"] = None
            ab.play_one(20269000, W, H, A, "baseline", 80, 200000, 2000000,
                        first=(fx, fy))
            got[arm] = cur["first"]
    finally:
        Board.open = orig_open

    for arm, want in (("center", (15, 8)), ("corner00", (0, 0)),
                      ("corner01", (29, 0)), ("edge_top", (15, 0))):
        check(f"T1 {arm} 首点 = {want}", got.get(arm) == want,
              f"实得 {got.get(arm)}")

    # ---- T2: 不同 first 是否产生不同棋盘 ----
    print("\n[T2] 不同首点 ⇒ 棋盘不同（延迟布雷池不同）")
    import hashlib
    import random
    from sim.minesweeper_sim import Board as B2

    def board_hash(fx, fy, seed=20269000):
        rng = random.Random(seed)
        b = B2(W, H, A, rng)
        b.open(fx, fy)
        s = ",".join(f"{x},{y}" for x, y in sorted(b.mines))
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    hs = {}
    for arm, (fx, fy) in (("center", (15, 8)), ("corner00", (0, 0)),
                          ("corner01", (29, 0))):
        hs[arm] = board_hash(fx, fy)
        print(f"    {arm:<10} 雷位哈希 {hs[arm]}")
    check("T2 不同首点棋盘不同",
          len(set(hs.values())) == len(hs),
          f"{len(set(hs.values()))} 个不同哈希 / {len(hs)} 臂")

    # ---- T3: 首点开出面积分布 vs 理论 ----
    print("\n[T3] 首点开出 >1 格的比例 vs 理论 P(首格数字=0)")
    import math
    N = W * H - 1
    for arm, (fx, fy) in (("center", (15, 8)), ("corner00", (0, 0)),
                          ("edge_top", (15, 0))):
        nbx = len([1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   if (dx or dy) and 0 <= fx + dx < W and 0 <= fy + dy < H])
        p_theory = math.comb(N - nbx, A) / math.comb(N, A)
        # 实测：跑 300 局，统计首点后 opened_count
        rng = random.Random(4242)
        n_gt1 = 0
        n_tot = 0
        for _ in range(300):
            b = B2(W, H, A, rng)
            b.open(fx, fy)
            n_tot += 1
            if b.opened_count > 1:
                n_gt1 += 1
        p_obs = n_gt1 / n_tot
        se = math.sqrt(p_obs * (1 - p_obs) / n_tot)
        z = (p_obs - p_theory) / se if se > 0 else 0
        ok = abs(z) < 3.0
        print(f"    {arm:<10} 邻域{nbx}格  理论 {p_theory*100:5.2f}%  "
              f"实测 {p_obs*100:5.2f}% (n={n_tot})  z={z:+.2f}  "
              f"{'✔' if ok else '✘'}")
        if not ok:
            FAILS.append(f"T3 {arm} 分布不符")

    print("\n" + "=" * 96)
    if FAILS:
        print(f"✘ 仪器验证失败 {len(FAILS)} 项：{FAILS}")
        print("   ⇒ ab_first_click 的读数作废，先修仪器。")
        return 1
    print("✅ 仪器验证通过：first 生效、棋盘按首点不同、开局分布符合理论")
    print("   ⇒ ab_first_click 的配对比较可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
