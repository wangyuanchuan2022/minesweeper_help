# -*- coding: utf-8 -*-
"""残局质量：复用生产快照 + 按覆盖率分层 + 按局聚类。

=================== 数据来源（唯一可靠）====================
`bench/endgame_prod_snapshot.py` 的 `ProdSnapshotRecorder`：
在 `pyautogui.click` 被调用时，用
`inspect.currentframe().f_back.f_locals` 直接取
`_number5_1_core` 的**局部变量**（clicks / clicks9 / _prob / pos /
confidence / total）——不重算任何概率，与生产逐项一致。

已通过 W1–W5 自检：
  W1 clicks + clicks9 == 未开格数（守恒）
  W2 _prob.keys() == set(clicks)（生产组装方式）
  W3 pos[0] == 实际点击 + 1
  W4a 前沿被选中时 _prob[pos0] == confidence（float32 容差）
  W4b 内部被选中时 confidence >= 前沿 max（生产只在此条件下改选）
  W5 pos0 非前沿时 clicks9 非空

=================== 两个必须的分层 ====================
1. **覆盖率分层**：`pm` 只覆盖前沿 `clicks`。当 `pos0` 是内部格时，
   该格概率 = `confidence`（生产闭式值，所有内部格相同），可补全。
   但"最优格"未必在已知集合里 ⇒ 只有覆盖率达标才能谈最优性。
2. **按局聚类**：一局多个残局决策，逐决策 z 会高估显著性。

用法: python bench/measure_endgame_stratified.py --games 400
"""
import argparse
import json
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99
ENDGAME = 343
COVERAGE_MIN = 0.95


def _atomic_json_dump(obj, path):
    """原子写 JSON：先写 .tmp，fsync 后再 replace。"""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def cluster_bootstrap_ci(per_game, n_boot=5000, seed=0):
    """按局 bootstrap 求均值的 95%CI。"""
    if not per_game:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    n = len(per_game)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += per_game[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return (sum(per_game) / n, means[int(0.025 * n_boot)],
            means[int(0.975 * n_boot)])


def build_full_probs(snap):
    """把生产快照补成完整候选概率表。

    前沿格：直接用生产的 `_prob`（`res` 原始值）。
    内部格：生产闭式 —— 所有内部格安全率恒等于 `confidence`
            （源码：`_confidence = 1 - (mine9/len(clicks9))`）。
    """
    probs = dict(snap["prob_vals"] or {})
    front = set(map(tuple, snap["clicks"]))
    if snap["pos0"] and tuple(snap["pos0"]) not in front \
            and snap["n_clicks9"] > 0:
        for (x, y) in snap["clicks9"]:
            probs[f"{x},{y}"] = snap["confidence"]
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20267000)
    ap.add_argument("--out", default="state/ab/endgame_stratified.json")
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "egstrat"), True)
    import ab_policy as ab
    from endgame_prod_snapshot import ProdSnapshotRecorder

    per_game = []
    n_games = 0
    for gi in range(args.games):
        rec = ProdSnapshotRecorder()
        rec.install()
        try:
            ab.play_one(args.seed + gi, W, H, A, "baseline", 80,
                        200000, 2000000)
        except Exception:                              # noqa: BLE001
            pass
        finally:
            rec.uninstall()
        n_games += 1

        g_events = []
        for e in rec.events:
            s = e.get("snap")
            if not s or not s.get("pos0"):
                continue
            if e["n_opened"] < ENDGAME:
                continue
            made = [c for c in s.get("clicks_made") or [] if c[3]]
            if not made:
                continue
            probs = build_full_probs(s)
            key = f"{s['pos0'][0]},{s['pos0'][1]}"
            actual = probs.get(key)
            bestv = max(probs.values()) if probs else None
            n_un = e["n_unopened"]
            rem = A - e["n_flagged"]
            g_events.append({
                "n_opened": e["n_opened"],
                "n_unopened": n_un,
                "n_front": s["n_clicks"],
                "n_interior": s["n_clicks9"],
                "uniform_rate": (rem / n_un) if n_un > 0 else 1.0,
                "pick_is_mine": bool(made[0][2]),
                "coverage": len(probs) / max(1, n_un),
                "actual_safety": actual,
                "best_safety": bestv,
                "regret": ((bestv - actual)
                           if (bestv is not None and actual is not None)
                           else None),
            })
        per_game.append({"seed": args.seed + gi, "events": g_events})
        if (gi + 1) % 100 == 0:
            tot = sum(len(g["events"]) for g in per_game)
            print(f"  ... {gi+1}/{args.games} 残局事件 {tot}", flush=True)

    all_ev = [e for g in per_game for e in g["events"]]
    print("\n" + "=" * 100)
    print(f"残局质量（生产快照 + 分层 + 按局聚类；{n_games} 局）")
    print("=" * 100)
    print(f"\n  残局决策事件 {len(all_ev)}")

    if not all_ev:
        print("  未采到残局样本")
        return

    covs = sorted(e["coverage"] for e in all_ev)
    rate_cov = sum(1 for c in covs if c >= 0.95) / len(covs)
    print(f"  候选概率覆盖率：中位 {covs[len(covs)//2]:.4f}  "
          f"min {covs[0]:.4f}  max {covs[-1]:.4f}")
    print(f"  ≥95% 的事件比例：{rate_cov*100:.2f}%")

    print("\n" + "=" * 100)
    print("覆盖率门线")
    print("=" * 100)
    if rate_cov < COVERAGE_MIN:
        print(f"  ⚠️ 低于门线 {COVERAGE_MIN*100:.0f}% ⇒")
        print("     regret 结论不能外推到全体残局")
    else:
        print(f"  ✔ 达到门线 {COVERAGE_MIN*100:.0f}%")

    regs = [e["regret"] for e in all_ev if e["regret"] is not None]
    if regs:
        regs.sort()
        n = len(regs)
        n_opt = sum(1 for x in regs if x <= 1e-9)
        print("\n" + "=" * 100)
        print(f"最优性与后悔度（n={n}）")
        print("=" * 100)
        print(f"  后悔度：中位 {regs[n//2]:.6f}  p90 {regs[int(0.9*n)]:.6f} "
              f"max {regs[-1]:.6f}")
        print(f"  完全选对 {n_opt}/{n} = {n_opt/n*100:.2f}%")
        print(f"  平均后悔 {sum(regs)/n:.6f}")

        g_means = []
        for g in per_game:
            rs = [e["regret"] for e in g["events"]
                  if e["regret"] is not None]
            if rs:
                g_means.append(sum(rs) / len(rs))
        if g_means:
            m, lo, hi = cluster_bootstrap_ci(g_means, seed=42)
            print(f"\n  按局聚类（{len(g_means)} 局）：")
            print(f"    局级平均 regret 均值 {m:.6f}  "
                  f"95%CI [{lo:.6f}, {hi:.6f}]")
            if lo > 0:
                print("    ⇒ CI 不含 0 ⇒ 后悔度**显著大于 0** ⇒ 有改进空间")
            elif hi < 0:
                print("    ⇒ CI 不含 0 但为负 ⇒ 异常，需复查")
            else:
                print("    ⇒ CI 含 0 ⇒ 后悔度**未显著大于 0**")

    print("\n" + "=" * 100)
    print("实测雷率 vs 均匀先验（按局聚类）")
    print("=" * 100)
    g_rates, g_unif = [], []
    for g in per_game:
        ev = g["events"]
        if not ev:
            continue
        g_rates.append(sum(1 for e in ev if e["pick_is_mine"]) / len(ev))
        g_unif.append(sum(e["uniform_rate"] for e in ev) / len(ev))
    if g_rates:
        m_r, lo_r, hi_r = cluster_bootstrap_ci(g_rates, seed=7)
        m_u, lo_u, hi_u = cluster_bootstrap_ci(g_unif, seed=8)
        diff_g = [a - b for a, b in zip(g_rates, g_unif)]
        m_d, lo_d, hi_d = cluster_bootstrap_ci(diff_g, seed=9)
        print(f"  有残局样本的局数 {len(g_rates)}")
        print(f"  局级踩雷率 均值 {m_r*100:.2f}%  "
              f"95%CI [{lo_r*100:.2f}%, {hi_r*100:.2f}%]")
        print(f"  局级均匀先验 均值 {m_u*100:.2f}%  "
              f"95%CI [{lo_u*100:.2f}%, {hi_u*100:.2f}%]")
        print(f"  局级差 均值 {m_d*100:+.2f}pp  "
              f"95%CI [{lo_d*100:+.2f}, {hi_d*100:+.2f}]pp")
        print()
        if hi_d < 0:
            print("  ⇒ 按局 CI 全为负 ⇒ 显著优于均匀选格")
        elif lo_d > 0:
            print("  ⇒ 按局 CI 全为正 ⇒ 显著差于均匀选格")
        else:
            print("  ⇒ 按局 CI 含 0 ⇒ **未检出**显著差异")
        print("\n  ⚠️ 均匀先验是很弱的基线（相当于闭眼随机）。")

    _atomic_json_dump({
        "games": n_games, "seed": args.seed,
        "coverage_ge95_rate": rate_cov, "coverage_min": COVERAGE_MIN,
        "per_game": per_game, "complete": True,
    }, args.out)
    print(f"\n明细已写 {args.out}")


if __name__ == "__main__":
    main()
