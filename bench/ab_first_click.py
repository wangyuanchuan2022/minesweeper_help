# -*- coding: utf-8 -*-
"""首点位置实验：Arbiter（延迟布雷）规则下，首点该点哪里？

=================== 为什么这是本轮唯一还没试过的高杠杆变量 ====================
Arbiter 是**延迟布雷**：只保证首点**本格**安全，不保证 8 邻域。
所以首格数字 = 该格真实邻域雷数，邻域格数取决于位置：
    角落 3 格 / 边缘 5 格 / 中心 8 格
精确组合数 C(N-nb, a) / C(N, a)，N = 479：
    角落 **49.85%** / 边缘 31.25% / 中心 **15.45%** 的概率首格数字为 0
⇒ 在 Arbiter 规则下，角落开局**最容易** flood-fill 开出一大片。

而生产代码 `Solver.play` 与 bench 的 `play_one` 都**固定点中心**
`(w//2, h//2)` —— 恰好是三者中最不容易开局的。

⚠️ 与现代规则结论相反（不可混用）：
   现代规则（首点及 8 邻域无雷）下角落只保护 4 格、开局小、胜率低约 5pp
   （MinesweeperHQ 170 万局实测：中心 50.7% vs 角落 45.6%）。

=================== 实验设计（单变量、同 seed 配对）====================
臂 = 首点坐标，其余（算法、limit、node_cap、棋盘参数）全部固定：
    center   (15, 8)   现行默认
    corner00 (0, 0)
    corner01 (29, 0)   镜像角，检验是否只是"某个角特别好"
    edge     (15, 0)   上边中点
同 seed 段 ⇒ 棋盘由 (seed, 首点) 共同决定；配对比较用 McNemar。

用法:
  python bench/ab_first_click.py --games 400 --seed 20269000
"""
import argparse
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

W, H, A = 30, 16, 99

ARMS = {
    "center": (W // 2, H // 2),
    "corner00": (0, 0),
    "corner01": (W - 1, 0),
    "edge_top": (W // 2, 0),
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(math.comb(n, i) * (0.5 ** n)
                              for i in range(k + 1)))


def paired_diff(b, c, n, z=1.96):
    """Δ = (b − c)/n，b = 甲赢乙输。配对口径方差。"""
    d = (b - c) / n
    var = ((b + c) - (b - c) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return d, se, (d - z * se, d + z * se)


def mde80(b, c, n):
    _d, se, _ci = paired_diff(b, c, n)
    return (1.959964 + 0.841621) * se


def run_arm(name, first, seeds, cfg):
    from sim.eval_harness import install_stubs
    install_stubs(cfg["state_dir"], True)
    import ab_policy as ab
    out = {}
    t0 = time.time()
    step = max(1, len(seeds) // 10)
    for i, sd in enumerate(seeds):
        try:
            r = ab.play_one(sd, W, H, A, "baseline", cfg["limit"],
                            cfg["world_cap"], cfg["node_cap"], first=first)
            out[sd] = {"won": bool(r.get("won")), "died": bool(r.get("died")),
                       "opened": r.get("opened"), "steps": r.get("steps"),
                       "error": r.get("error")}
        except Exception as e:                          # noqa: BLE001
            out[sd] = {"won": False, "error": f"{type(e).__name__}: {e}"}
        # 进度输出：长时间无输出时无法区分"在推进"与"已卡死"
        if (i + 1) % step == 0 or (i + 1) == len(seeds):
            w = sum(1 for v in out.values() if v.get("won"))
            el = time.time() - t0
            eta = el / (i + 1) * (len(seeds) - i - 1)
            print(f"       {i+1:>4}/{len(seeds)}  {w}/{i+1} = "
                  f"{w/(i+1)*100:5.2f}%   已用 {el:5.0f}s  "
                  f"预计还需 {eta:5.0f}s", flush=True)
    return out



def _atomic_json_dump(obj, path):
    """原子写 JSON：先写 .tmp，fsync 后再 replace。

    ⚠️ 为什么必须原子：本次实跑出现过"日志含 KeyboardInterrupt、
    但正式 JSON 已存在"的并存状态。文件存在不能证明实验完整，
    所以写入必须原子，并带 complete 标记供核对。
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
    ap.add_argument("--games", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20269000)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--arms", default="center,corner00,corner01,edge_top")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的 --out 文件（默认拒绝）")
    args = ap.parse_args()

    state_dir = os.path.join(ROOT, "state", "ab", "firstclick")
    os.makedirs(state_dir, exist_ok=True)
    cfg = {"limit": args.limit, "world_cap": args.world_cap,
           "node_cap": args.node_cap, "state_dir": state_dir}
    seeds = [args.seed + i for i in range(args.games)]
    arm_names = args.arms.split(",")

    print("=" * 100)
    print(f"首点位置实验（Arbiter 延迟布雷规则）：{args.games} 局 × "
          f"{len(arm_names)} 臂，同 seed 配对")
    print(f"seeds {seeds[0]}..{seeds[-1]}")
    print("=" * 100)

    # ---- 先给理论预测（可事后对照）----
    import math as _m
    N = W * H - 1
    print("\n理论：P(首格数字=0) = C(N-nb, a)/C(N, a)，N=479, a=99")
    for name, (fx, fy) in ((k, ARMS[k]) for k in arm_names):
        nbx = len([1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   if (dx or dy) and 0 <= fx + dx < W and 0 <= fy + dy < H])
        p0 = _m.comb(N - nbx, A) / _m.comb(N, A)
        print(f"  {name:<10} 首点=({fx},{fy})  邻域 {nbx} 格  "
              f"P(数字=0) = {p0*100:5.2f}%")

    # ---- 逐臂跑 ----
    got = {}
    for name in arm_names:
        print(f"\n  跑 {name} ({ARMS[name]}) …", flush=True)
        got[name] = run_arm(name, ARMS[name], seeds, cfg)
        ok = {k: v for k, v in got[name].items() if not v.get("error")}
        w = sum(1 for v in ok.values() if v["won"])
        print(f"     {w}/{len(ok)} = {w/max(1,len(ok))*100:6.2f}%", flush=True)

    # ---- 汇总 ----
    print("\n" + "=" * 100)
    print("各臂胜率（Wilson 95%CI）")
    print("=" * 100)
    summary = {}
    for name in arm_names:
        ok = {k: v for k, v in got[name].items() if not v.get("error")}
        w = sum(1 for v in ok.values() if v["won"])
        lo, hi = wilson(w, len(ok))
        summary[name] = {"wins": w, "n": len(ok), "ci": [lo, hi]}
        print(f"  {name:<10} {w}/{len(ok)} = {w/len(ok)*100:6.2f}%  "
              f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")

    # ---- 与 center 配对比较 ----
    base = "center"
    if base in got:
        print("\n" + "=" * 100)
        print(f"配对比较 vs {base}（同 seed；正 Δ = 该臂更好）")
        print("=" * 100)
        print(f"{'臂':<12} {'该臂赢/基线输':>13} {'基线赢/该臂输':>13} "
              f"{'n':>5} {'Δ':>9} {'95%CI':>22} {'MDE':>8} {'p':>8}")
        print("-" * 100)
        cmp_rows = []
        for name in arm_names:
            if name == base:
                continue
            b = c = n = 0
            for sd in seeds:
                x, y = got[base].get(sd), got[name].get(sd)
                if not x or not y or x.get("error") or y.get("error"):
                    continue
                n += 1
                if y["won"] and not x["won"]:
                    b += 1
                elif x["won"] and not y["won"]:
                    c += 1
            d, se, (lo, hi) = paired_diff(b, c, n)
            mde = mde80(b, c, n)
            p = mcnemar_exact(b, c)
            print(f"{name:<12} {b:>13} {c:>13} {n:>5} {d*100:>+8.2f}pp "
                  f"[{lo*100:>+7.2f},{hi*100:>+7.2f}]pp {mde*100:>7.2f}pp "
                  f"{p:>8.4f}")
            cmp_rows.append({"arm": name, "a_only": b, "b_only": c, "n": n,
                             "delta": d, "ci": [lo, hi], "mde": mde, "p": p})

        print("\n  判定口径：")
        for r in cmp_rows:
            sig = ("显著更好" if r["p"] < 0.05 and r["delta"] > 0 else
                   "显著更差" if r["p"] < 0.05 and r["delta"] < 0 else
                   "未检出差异")
            print(f"    {r['arm']:<10} Δ={r['delta']*100:+.2f}pp  "
                  f"p={r['p']:.4f}  MDE={r['mde']*100:.2f}pp  ⇒ {sig}")

    # ---- 开局信息量机制核对 ----
    print("\n" + "=" * 100)
    print("机制核对：各臂「首点只开 1 格」的比例（若理论对，角落应最低）")
    print("=" * 100)
    for name in arm_names:
        vals = [v for v in got[name].values() if v.get("opened") is not None]
        if not vals:
            continue
        # opened==1 意味着首点只开 1 格（首格数字非 0）
        n1 = sum(1 for v in vals if v["opened"] == 1)
        # 更好的代理：首点后立即死亡比例
        d1 = sum(1 for v in vals if v.get("died") and v["opened"] <= 1)
        print(f"  {name:<10} opened==1 占 {n1/len(vals)*100:5.2f}%  "
              f"（其中即时死亡 {d1}）")

    out = args.out or os.path.join(ROOT, "state", "ab", "first_click_ab.json")
    # 正式读数不应被静默覆盖（会丢失复跑前后的逐局对照）
    if os.path.exists(out) and not args.force:
        print(f"\n  拒绝覆盖已有结果：{out}")
        print("     正式读数不应被静默覆盖，会丢失复跑前后的逐局对照。")
        print("     改用 --out 指定新路径，或加 --force 显式覆盖。")
        return 2
    _atomic_json_dump({"games": args.games, "seed": args.seed,
                   "arms": {k: list(v) for k, v in ARMS.items()},
                   "config": cfg, "summary": summary,
                   "results": got, "complete": True}, out)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
