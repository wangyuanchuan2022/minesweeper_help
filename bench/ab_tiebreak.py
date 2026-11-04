# -*- coding: utf-8 -*-
"""破平局准则的**整局配对 A/B**：只替换 `open_num5x5` 一个函数。

=================== 为什么选这一个函数 ====================
`bench/analyze_tiebreak_room.py` 用 400 个真实中局决策点实测：
  · 有破平局自由度的决策里，生产最终在 ≥2 格里**均匀随机选**的比例极高
  · 破平局打分 `open_num5x5` 把**棋盘外**计为"已开"（63% 的选中格贴边；
    80% 的决策里平局集存在更靠中心的格而生产选了更贴边的）
⇒ 只换这个打分函数，就能在不改安全率、不改枚举成本、不改控制流的前提下
   改变选格。这是**最小可 A/B 的改动**。

=================== 三个臂（同 seed、同棋盘、逐局配对）====================
  baseline       生产原样（不改任何东西）
  fix_nobounds   越界位置不再计为已开（真正的"5x5 内已开格数"）
  center_bias    open_num5x5 → 到边界最小距离（越大越靠中心）
                 （等价于"平局时优先选更靠中心的格"）

=================== 判据 ====================
  · 配对 McNemar 精确检验（同 seed 判输赢是否翻转）
  · Wilson CI
  · 同时报告**判定只看 win rate**；不给"决策质量"当胜率结论
  · ⚠️ 台架等价性：baseline 臂必须与 sim/eval_harness 同 seed 同结果
    （本脚本用 ab_policy.play_one，该台架已过 12/12 等价性闸门）

用法:
  python bench/ab_tiebreak.py --games 400 --workers 12
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


def mcnemar_exact(b, c):
    """双尾精确 McNemar（b/c 为两臂不一致的配对数）。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    return min(1.0, 2.0 * tail)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _is_frontier(w, h, cell_value, i, j):
    """未开格 (i,j) 是否紧邻已开数字格（受局部约束的前沿格）。"""
    for a in range(i - 1, i + 2):
        for b in range(j - 1, j + 2):
            if a < 1 or a > w or b < 1 or b > h:
                continue
            if 0 <= cell_value[b, a] <= 8:
                return True
    return False


def install_open5x5_patch(mode):
    """替换 ProbabilityMixin.open_num5x5。mode 见 RULES。

    返回 restore 函数。⚠️ baseline 返回空 restore（不 patch 任何东西）。
    """
    from utils.probability import ProbabilityMixin
    orig = ProbabilityMixin.open_num5x5
    if mode == "baseline":
        return lambda: None

    def patched(self, cell_value, pos):
        x, y = pos
        w, h = self.w, self.h
        if mode == "fix_nobounds":
            res = 0
            for i in range(x - 2, x + 3):
                for j in range(y - 2, y + 3):
                    if i < 1 or i > w or j < 1 or j > h:
                        continue           # ← 唯一改动：越界跳过
                    if 0 <= cell_value[j, i] <= 8:
                        res += 1
            return res
        if mode == "center_bias":
            return min(x - 1, w - x, y - 1, h - y)
        if mode == "farthest_from_edge":
            return min(x - 1, w - x, y - 1, h - y)
        if mode == "open_plus_frontier":
            # 已开邻居数（越界不计） + 前沿格数
            opened = 0
            front = 0
            for i in range(x - 2, x + 3):
                for j in range(y - 2, y + 3):
                    if i < 1 or i > w or j < 1 or j > h:
                        continue
                    v = cell_value[j, i]
                    if 0 <= v <= 8:
                        opened += 1
                    elif v == 9 and not (i == x and j == y):
                        if _is_frontier(w, h, cell_value, i, j):
                            front += 1
            return opened + front
        raise ValueError(mode)

    ProbabilityMixin.open_num5x5 = patched

    def restore():
        ProbabilityMixin.open_num5x5 = orig
    return restore


def run_arm(mode, seeds, cfg):
    """跑一个臂，返回 {seed: won}。"""
    from sim.eval_harness import install_stubs
    install_stubs(cfg["state_dir"], True)
    import ab_policy as ab
    restore = install_open5x5_patch(mode)
    out = {}
    try:
        for sd in seeds:
            try:
                r = ab.play_one(sd, cfg["w"], cfg["h"], cfg["a"], "baseline",
                                cfg["limit"], cfg["world_cap"], cfg["node_cap"])
                out[sd] = {"won": bool(r.get("won")),
                           "died": bool(r.get("died")),
                           "steps": r.get("steps"),
                           "ms": r.get("ms"),
                           "error": r.get("error")}
            except Exception as e:                      # noqa: BLE001
                out[sd] = {"won": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        restore()
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
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--world-cap", type=int, default=200000)
    ap.add_argument("--node-cap", type=int, default=2000000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--arms", default=None,
                    help="逗号分隔的臂（缺省跑全部 4 个）；复现实验时只跑 2 个臂省时")
    ap.add_argument("--label", default=None, help="输出文件名后缀（区分复现轮次）")
    args = ap.parse_args()

    state_dir = os.path.join(ROOT, "state", "ab", "tiebreak_ab")
    os.makedirs(state_dir, exist_ok=True)
    cfg = {"w": args.w, "h": args.h, "a": args.a, "limit": args.limit,
           "world_cap": args.world_cap, "node_cap": args.node_cap,
           "state_dir": state_dir}
    seeds = [args.seed + i for i in range(args.games)]

    import time
    ARMS = tuple((args.arms or "baseline,open_plus_frontier,farthest_from_edge,"
                  "fix_nobounds").split(","))
    got = {}
    print("=" * 92)
    print(f"破平局准则整局配对 A/B：{args.games} 局 × {len(ARMS)} 臂")
    print(f"棋盘 {args.w}x{args.h} {args.a} 雷；seed {seeds[0]}..{seeds[-1]}")
    print("=" * 92, flush=True)
    for mode in ARMS:
        t0 = time.time()
        got[mode] = run_arm(mode, seeds, cfg)
        w = sum(1 for v in got[mode].values() if v.get("won"))
        n = sum(1 for v in got[mode].values() if not v.get("error"))
        print(f"  {mode:<14} {w}/{n} = {w/max(1, n)*100:.2f}%  "
              f"耗时 {time.time()-t0:.0f}s", flush=True)

    print("\n" + "=" * 92)
    print("各臂胜率（Wilson 95%CI）")
    print("=" * 92)
    for mode in ARMS:
        v = got[mode]
        ok = {s: x for s, x in v.items() if not x.get("error")}
        w = sum(1 for x in ok.values() if x["won"])
        lo, hi = wilson(w, len(ok))
        print(f"  {mode:<14} {w}/{len(ok)} = {w/len(ok)*100:6.2f}%  "
              f"CI [{lo*100:.2f}%, {hi*100:.2f}%]")

    print("\n" + "=" * 92)
    print("配对 McNemar（同 seed；只统计两臂都有效的局）")
    print("=" * 92)
    print(f"{'A vs B':<34} {'A赢B输':>7} {'B赢A输':>7} {'p':>9} {'Δ胜率':>9} {'判定'}")
    print("-" * 92)
    pairs = [p for p in [("open_plus_frontier", "baseline"),
             ("farthest_from_edge", "baseline"),
             ("fix_nobounds", "baseline"),
             ("open_plus_frontier", "farthest_from_edge")]
             if p[0] in got and p[1] in got]
    for a, b in pairs:
        vio_a = vio_b = 0
        n = 0
        for s in seeds:
            xa, xb = got[a].get(s), got[b].get(s)
            if not xa or not xb or xa.get("error") or xb.get("error"):
                continue
            n += 1
            if xa["won"] and not xb["won"]:
                vio_a += 1
            elif xb["won"] and not xa["won"]:
                vio_b += 1
        p = mcnemar_exact(vio_a, vio_b)
        wa = sum(1 for s in seeds if (got[a].get(s) or {}).get("won"))
        wb = sum(1 for s in seeds if (got[b].get(s) or {}).get("won"))
        delta = (wa - wb) / max(1, n)
        verdict = "显著" if p < 0.05 else "不显著"
        print(f"{a + ' vs ' + b:<34} {vio_a:>7} {vio_b:>7} {p:>9.4f} "
              f"{delta*100:>+8.2f}pp  {verdict}")

    # 逐局差异明细（帮助判断是否只是随机抖动）
    print("\n" + "=" * 92)
    print("逐局胜负翻转明细")
    print("=" * 92)
    for a, b in pairs:
        flips = [s for s in seeds
                 if (got[a].get(s) or {}).get("won") is not None
                 and (got[b].get(s) or {}).get("won") is not None
                 and got[a][s]["won"] != got[b][s]["won"]]
        print(f"  {a} vs {b}: {len(flips)} 局翻转"
              + (f"（前 10：{flips[:10]}）" if flips else ""))

    out = args.out or os.path.join(ROOT, "state", "ab",
                                   "tiebreak_ab%s.json"
                                   % ("" if not args.label else "_" + args.label))
    _atomic_json_dump({"games": args.games, "seed": args.seed,
                   "config": cfg, "results": got, "complete": True}, out)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main()
