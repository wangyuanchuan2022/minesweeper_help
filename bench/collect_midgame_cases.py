# -*- coding: utf-8 -*-
"""采集**中局**概率决策点快照（limitation > wr_threshold），供离线分析。

=================== 为什么必须采中局 ====================
生产 `win_rate`(完整前瞻) 只在 `limitation <= 8` 时运行，而
`limitation = 0.8*len(clicks9) + log2(total)`，真实对局 limitation 中位数是 61。
⇒ **中局根本没有前瞻，只有贪心(max safety)**。

此前 33 个精确用例（bench/data/wr_cases_big.json）全部 limitation<=8、
U<=7 ⇒ 它们是**残局**（剩余雷几乎都在前沿/已标记），不是中局。
所以"2-ply/3-ply 无增益"的旧结论**不能外推到中局**——中局从没测过。

本脚本在真实对局里拦截 `process_bigger_situation`（中局/降级分支），
把决策点原样存盘（clicks/clicks9/groups/ck/cell_value 都是人类可见信息，
不含雷位），供 bench/analyze_midgame_room.py 离线算：
  · 精确每格安全率（复用 production 的 `_pbs_compute_dp`，数学上精确）
  · 采样世界下的"点开 c 预期新开格数"（信息增益代理）
  · 生产实际选格 vs 各种替代打分，以及"换格的空间有多大"

用法:
  python bench/collect_midgame_cases.py --games 60 --max-cases 150
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

MINE = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--max-cases", type=int, default=150)
    ap.add_argument("--per-bucket-quota", type=int, default=40,
                    help="每个 limitation 分层最多采多少个点（防大局占满样本）")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--wr-threshold", type=float, default=8.0,
                    help="只采 limitation > 该值的决策点（即生产不走 win_rate 的中局）")
    ap.add_argument("--out", default=os.path.join(ROOT, "bench", "data",
                                                  "midgame_cases.json"))
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "midgame"), True)

    import numpy as np

    import ab_policy as ab
    import utils.probability as prob

    cases = []
    skipped = {"small": 0, "empty": 0, "err": 0, "quota": 0}
    # 分层配额：中局的价值集中在 limitation 稍高于阈值的那一段（8,20]，
    # 因为再大就无法精确枚举世界了。无配额会让样本全被 U≈465 的超大局占满。
    stats = {"buckets": {}}
    per_bucket = {}
    orig_pbs = prob.ProbabilityMixin.process_bigger_situation

    def _bucket(lim):
        if lim <= 20:
            return "(8,20]"
        if lim <= 60:
            return "(20,60]"
        return "(60,inf]"

    def hooked(self, total, num9, num10, clicks, clicks9, res_list, ck,
               cell_value, pos):
        """先跑原实现（拿到生产的真实选格），再把输入快照存盘。"""
        out = orig_pbs(self, total, num9, num10, clicks, clicks9, res_list,
                       ck, cell_value, pos)
        try:
            F = len(clicks)
            U = len(clicks9)
            total_i = int(total) if not isinstance(total, (list, tuple)) else \
                int(np.asarray(total).ravel()[0])
            lim = U * 0.8 + math.log2(max(total_i, 1))
            if lim <= args.wr_threshold:
                skipped["small"] += 1
                return out
            bucket = _bucket(lim)
            stats["buckets"][bucket] = stats["buckets"].get(bucket, 0) + 1
            if per_bucket.get(bucket, 0) >= args.per_bucket_quota:
                skipped["quota"] += 1
                return out
            per_bucket[bucket] = per_bucket.get(bucket, 0) + 1
            # 生产在本分支返回 (pos, confidence, total, _prob)
            picked = None
            conf = None
            if isinstance(out, tuple) and len(out) >= 4:
                if out[0]:
                    picked = [int(out[0][0][0]), int(out[0][0][1])]
                conf = float(out[1])
            if F < 1 or U < 1 or not picked:
                skipped["empty"] += 1
                return out
            if len(cases) >= args.max_cases:
                return out
            groups = []
            for g in res_list:
                arr = np.asarray(g)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                groups.append([[int(v) for v in row] for row in arr.tolist()])
            ck_flat = []
            for c in ck:
                if isinstance(c, (list, tuple, np.ndarray)):
                    a = np.asarray(c).ravel()
                    ck_flat.append(int(a[0]) if a.size else 0)
                else:
                    ck_flat.append(int(c))
            cases.append({
                "F": F, "U": U, "num10": int(num10),
                "total": total_i, "limitation": round(lim, 3),
                "n_cand": F + U,
                "a": int(self.a), "w": int(self.w), "h": int(self.h),
                "clicks": [[int(p[0]), int(p[1])] for p in clicks],
                "clicks9": [[int(p[0]), int(p[1])] for p in clicks9],
                "ck": ck_flat,
                "groups": groups,
                "cell_value": np.asarray(cell_value).astype(int).tolist(),
                "picked": picked,
                "confidence": conf,
            })
        except Exception:                                  # noqa: BLE001
            skipped["err"] += 1
        return out

    prob.ProbabilityMixin.process_bigger_situation = hooked
    try:
        for gi in range(args.games):
            if len(cases) >= args.max_cases:
                break
            sd = args.seed + gi
            try:
                ab.play_one(sd, 30, 16, 99, "baseline", args.limit, 200000, None)
            except Exception:                              # noqa: BLE001
                pass
    finally:
        prob.ProbabilityMixin.process_bigger_situation = orig_pbs

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"cases": cases, "skipped": skipped,
                   "wr_threshold": args.wr_threshold}, f, ensure_ascii=False)

    print("=" * 78)
    print(f"captured {len(cases)} mid-game decision points -> {args.out}")
    print(f"skipped: limitation<=th {skipped['small']}  empty/other {skipped['empty']}"
          f"  quota {skipped['quota']}  err {skipped['err']}")
    print(f"limitation buckets seen in real games: {stats['buckets']}")
    print(f"limitation buckets captured: {per_bucket}")
    if cases:
        for k in ("limitation", "U", "F", "total", "n_cand"):
            v = sorted(c[k] for c in cases)
            print(f"  {k:>11}: min {v[0]} med {v[len(v) // 2]} "
                  f"p90 {v[int(0.9 * len(v))]} max {v[-1]}")


if __name__ == "__main__":
    main()
