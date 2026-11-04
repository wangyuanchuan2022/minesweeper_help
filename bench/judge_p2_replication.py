# -*- coding: utf-8 -*-
"""P2 独立复现的判定器（按 review/P2复现预注册.md 的冻结判据机械执行）。

=================== 为什么要有独立判定脚本 ====================
预注册纪律要求「判据不得事后修改」。人工在看到结果后临时手算，
存在**无意的口径漂移**风险（换 CI 方法、换单双侧、换合并方式）。
本脚本把判据固化成代码，输入只有结果 JSON，输出只有三种裁定：
    REPLICATED / DIRECTION_ONLY / NOT_REPLICATED
且**拒绝**把正式实验与复现合并成一个"官方"数字（预注册第四节第 2 条）。

=================== 冻结判据（逐条对应预注册文档）====================
主判据（第三节）：
    Δ 方向必须与正式实验一致（corner > center）
副判据：
    McNemar p < 0.05（双侧）
    报告 95%CI，不要求排除 0
    效应量与 MDE 对照，但不再作为"是否采纳"的唯一门槛
明确规定：
    1. 不因结果修改判据
    2. 不把复现与正式实验合并成一个"官方"数字宣称达标
    3. 复现方向相反 ⇒ 未复现，不得采纳
    4. 方向相同但不显著 ⇒ 报告"方向一致但未复现显著性"

40% 目标判定（第四节，冻结表）：
    corner 两轮均 >= 40% 且 CI 下界 >= 40%  → 可称达标
    corner 点估计 >= 40% 但 CI 下界 < 40%    → 不能宣称达标
    corner 点估计 < 40%                      → 未达标

⚠️ 本脚本对「两轮合并」只输出描述性数字并强制标注 selection bias，
   不输出可用于宣称达标的合并判定。

用法:
  python bench/judge_p2_replication.py \
      --formal state/ab/firstclick_p2_formal.json \
      --repl state/ab/firstclick_p2_replication.json
"""
import argparse
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def mcnemar_exact(b, c):
    """双侧精确 McNemar。b/c = 不一致对的两个方向计数。"""
    n = b + c
    if n == 0:
        return 1.0
    # 双侧 p = 2 * P(X <= min(b,c)), X ~ Bin(n, 0.5)，截断到 1
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def load_arms(path):
    """从结果 JSON 读出每臂的 won/n。要求完整（complete=true 且 failures 为 0）。"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    complete = True
    for s in d["summaries"]:
        kind = s["kind"]
        out[kind] = {
            "won": int(s["won"]), "n": int(s["n"]),
            "rate": float(s["rate"]), "ci95": list(s["ci95"]),
            "zero_rate": s.get("zero_rate"),
            "failures": s.get("failures"),
            "requested": s.get("requested"),
            "complete": s.get("complete"),
        }
        if not s.get("complete") or s.get("failures"):
            complete = False
    return d, out, complete


def paired_from_per_game(d):
    """若结果 JSON 里有逐局记录，还原配对（同 seed 两臂）。

    注意：延迟布雷下首点不同 ⇒ 棋盘不同，这是**策略级配对**（seed 作区组），
    不是"同一棋盘选不同格"。预注册第五节已声明该限定。
    """
    res = d.get("results")
    if not res:
        return None
    # 逐局：results 是 {kind: [{seed, won}, ...]}
    by = {}
    for kind, arr in res.items():
        for r in arr:
            by.setdefault(r["seed"], {})[kind] = bool(r["won"])
    pairs = [(s, v["center"], v["corner"]) for s, v in by.items()
             if "center" in v and "corner" in v]
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--formal", default=os.path.join(
        "state", "ab", "firstclick_p2_formal.json"))
    ap.add_argument("--repl", default=os.path.join(
        "state", "ab", "firstclick_p2_replication.json"))
    ap.add_argument("--out", default=os.path.join(
        "state", "ab", "p2_replication_verdict.json"))
    args = ap.parse_args()

    for p in (args.formal, args.repl):
        if not os.path.exists(p):
            print("缺失输入：%s（复现可能还没跑完）" % p)
            return 1

    print("=" * 100)
    print("P2 独立复现判定（判据冻结于 review/P2复现预注册.md，本脚本只执行不修改）")
    print("=" * 100)

    verdict = {}
    for tag, path in (("正式", args.formal), ("复现", args.repl)):
        d, arms, complete = load_arms(path)
        print()
        print("-" * 100)
        print("【%s实验】%s" % (tag, os.path.basename(path)))
        print("-" * 100)
        print("  完整性：%s" % ("完整（每臂 requested 局有效、失败 0）"
                                if complete else "**不完整，读数不可用于判定**"))
        for kind in ("center", "corner"):
            a = arms[kind]
            print("    %-7s %4d/%-4d = %.2f%%  95%%CI [%.2f, %.2f]"
                  % (kind, a["won"], a["n"], a["rate"] * 100,
                     a["ci95"][0] * 100, a["ci95"][1] * 100))
        delta = (arms["corner"]["rate"] - arms["center"]["rate"]) * 100
        print("    Δ(corner−center) = %+.2fpp" % delta)
        if arms["corner"].get("zero_rate") is not None:
            print("    首格为 0 率：corner %.2f%%  center %.2f%%（理论 49.85%% / 15.45%%）"
                  % (arms["corner"]["zero_rate"] * 100,
                     arms["center"].get("zero_rate", float("nan")) * 100))
        pairs = paired_from_per_game(d)
        mc = None
        if pairs:
            b = sum(1 for _, cw, kw in pairs if kw and not cw)
            c = sum(1 for _, cw, kw in pairs if cw and not kw)
            p = mcnemar_exact(b, c)
            mc = {"corner_only": b, "center_only": c, "p": p, "n_pairs": len(pairs)}
            print("    配对 McNemar：corner 独赢 %d / center 独赢 %d，双侧精确 p = %.4f"
                  % (b, c, p))
        else:
            print("    （结果文件无逐局记录，无法配对检验——只能报独立两样本）")
        verdict[tag] = {"arms": arms, "delta_pp": delta, "mcnemar": mc,
                        "complete": complete, "path": path}

    print()
    print("=" * 100)
    print("按冻结判据裁定")
    print("=" * 100)
    f, r = verdict["正式"], verdict["复现"]

    # 硬门禁：任一轮不完整 ⇒ 拒绝裁定（预注册第五节「完整性」是硬门禁）
    if not (f["complete"] and r["complete"]):
        print("  ✘ 完整性门禁未通过：")
        for tag in ("正式", "复现"):
            v = verdict[tag]
            if not v["complete"]:
                for kind in ("center", "corner"):
                    a = v["arms"][kind]
                    print("      %s/%s：run %s / requested %s / failures %s / complete %s"
                          % (tag, kind, a["n"], a.get("requested"),
                             a.get("failures"), a.get("complete")))
        print()
        print("  ⇒ 裁定：**INCONCLUSIVE** —— 实验不完整，不得据此判定复现与否")
        print("     （预注册第五节：每臂必须恰好 N 局有效、失败 0，是硬门禁）")
        out_bad = {"verdict": "INCONCLUSIVE",
                   "reason": "completeness gate failed",
                   "formal": f, "replication": r,
                   "criteria_source": "review/P2复现预注册.md（冻结，未修改）"}
        out_p = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(out_p), exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as fh:
            json.dump(out_bad, fh, ensure_ascii=False, indent=1)
        print()
        print("  判定已写 %s" % out_p)
        return 4

    direction_ok = r["delta_pp"] > 0
    print("  主判据（Δ 方向与正式一致，corner > center）：")
    print("    正式 Δ = %+.2fpp，复现 Δ = %+.2fpp ⇒ %s"
          % (f["delta_pp"], r["delta_pp"],
             "方向一致" if direction_ok else "**方向相反**"))
    p_repl = r["mcnemar"]["p"] if r["mcnemar"] else None
    if p_repl is not None:
        print("  副判据（复现 McNemar p < 0.05 双侧）：p = %.4f ⇒ %s"
              % (p_repl, "显著" if p_repl < 0.05 else "不显著"))

    if not direction_ok:
        label = "NOT_REPLICATED"
        text = "未复现（方向相反）⇒ 按预注册第三节第 3 条，角落开局不得采纳"
    elif p_repl is not None and p_repl < 0.05:
        label = "REPLICATED"
        text = "复现成功（方向一致且显著）"
    else:
        label = "DIRECTION_ONLY"
        text = "方向一致但未复现显著性（按第三节第 4 条如实报告，不宣称采纳）"
    print()
    print("  ⇒ 裁定：**%s** —— %s" % (label, text))

    print()
    print("=" * 100)
    print("40%% 目标判定（预注册第四节冻结表）")
    print("=" * 100)
    for tag in ("正式", "复现"):
        a = verdict[tag]["arms"]["corner"]
        lo, hi = a["ci95"]
        if a["rate"] >= 0.40 and lo >= 0.40:
            j = "可称达标"
        elif a["rate"] >= 0.40:
            j = "不能宣称达标（CI 跨 40%）"
        else:
            j = "未达标"
        print("  %s corner = %.2f%%，95%%CI [%.2f, %.2f] ⇒ %s"
              % (tag, a["rate"] * 100, lo * 100, hi * 100, j))

    print()
    print("=" * 100)
    print("合并（仅描述性，禁止用于宣称达标）")
    print("=" * 100)
    print("  ⚠️ 正式实验是先跑且已看到结果的，与复现不平权；合并值含选择偏差。")
    tot_c = f["arms"]["corner"]["won"] + r["arms"]["corner"]["won"]
    tot_cn = f["arms"]["corner"]["n"] + r["arms"]["corner"]["n"]
    tot_m = f["arms"]["center"]["won"] + r["arms"]["center"]["won"]
    tot_mn = f["arms"]["center"]["n"] + r["arms"]["center"]["n"]
    lo_c, hi_c = wilson(tot_c, tot_cn)
    lo_m, hi_m = wilson(tot_m, tot_mn)
    print("    corner 合并 %d/%d = %.2f%%  CI [%.2f, %.2f]（含选择偏差，探索性）"
          % (tot_c, tot_cn, tot_c / tot_cn * 100, lo_c * 100, hi_c * 100))
    print("    center 合并 %d/%d = %.2f%%  CI [%.2f, %.2f]"
          % (tot_m, tot_mn, tot_m / tot_mn * 100, lo_m * 100, hi_m * 100))
    print("    Δ 合并 = %+.2fpp" % ((tot_c / tot_cn - tot_m / tot_mn) * 100))
    print("    ⇒ 该行**不得**作为「达到 40%」的依据（预注册第三节第 2 条）。")

    out = {
        "verdict": label, "verdict_text": text,
        "direction_ok": bool(direction_ok),
        "formal": f, "replication": r,
        "pooled_descriptive_ONLY": {
            "corner": {"won": tot_c, "n": tot_cn, "rate": tot_c / tot_cn,
                       "ci95": [lo_c, hi_c]},
            "center": {"won": tot_m, "n": tot_mn, "rate": tot_m / tot_mn,
                       "ci95": [lo_m, hi_m]},
            "contains_selection_bias": True,
            "usable_for_40pct_claim": False,
        },
        "criteria_source": "review/P2复现预注册.md（冻结，未修改）",
    }
    out_p = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print()
    print("  判定已写 %s" % out_p)
    return 0 if label != "NOT_REPLICATED" else 3


if __name__ == "__main__":
    sys.exit(main())
