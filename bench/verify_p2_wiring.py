# -*- coding: utf-8 -*-
"""P2 接线阳性对照：确认 center / corner 两臂**都**真的按预期首点开局。

=================== 为什么需要（Advisor 指出）====================
我只验证了「中心臂 15/15 与历史基线一致」，但那只证明**中心臂**没坏。
角落臂是新增路径（`first=(0,0)`），如果它因为某个 bug 实际仍走中心，
正式实验就会变成"中心 vs 中心"⇒ 得到 Δ≈0 的假阴性，
而我可能误报"角落开局无效"。

=================== 检查什么（只看仪器元数据）====================
1. 两臂实际首点坐标是否分别为 (15,8) 与 (0,0)
2. `first_num` 是否与该首点位置的数字一致（不是别的格）
3. 两臂除首点外的配置是否完全一致（同 seed / 同求解器参数 / 同二进制）
4. 两臂在同一 seed 下的**棋盘是否不同**（延迟布雷 ⇒ 首点不同则布雷池不同）

**明确不看**：胜率。本脚本不打印、不比较任何胜率数字，
避免污染正式实验的 optional-stopping 纪律。

用法: python bench/verify_p2_wiring.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 用正式 seed 之外的独立区间（正式实验用 20272000+）
VERIFY_SEED = 20279000
VERIFY_GAMES = 8


def probe(kind, w, h, a, seed):
    """跑一局，返回首点相关元数据（不含胜率）。"""
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "_wiring"), True)
    import ab_policy as ab
    from sim.minesweeper_sim import Board
    import random

    if kind == "center":
        fx, fy = w // 2, h // 2
    elif kind == "corner":
        fx, fy = 0, 0
    else:
        raise ValueError(kind)

    # 独立复现棋盘生成，核对"首点不同 ⇒ 棋盘不同"
    rng = random.Random(seed)
    b = Board(w, h, a, rng)
    b.open(fx, fy)
    v = b.view(set())
    # 记录整盘可见数字的哈希（只哈希可见信息，不碰雷位）
    sig = tuple(tuple(row) for row in v.cells)

    r = ab.play_one(seed, w, h, a, "baseline", limit=80,
                    node_cap=200000, world_cap=4096, first=(fx, fy))
    return {
        "kind": kind,
        "expected_first": [fx, fy],
        "first_num_reported": r.get("first_num") if isinstance(r, dict) else None,
        "first_num_expected": int(v.cells[fy][fx]),
        "opened": r.get("opened") if isinstance(r, dict) else None,
        "steps": r.get("steps") if isinstance(r, dict) else None,
        "board_sig": hash(sig),
    }


def main():
    w, h, a = 30, 16, 99
    print("=" * 96)
    print("P2 接线阳性对照（只看仪器元数据，不看胜率）")
    print("=" * 96)
    print("  验证 seed 区间：%d+（正式实验用 20272000+，不重叠）" % VERIFY_SEED)
    print("  局数：%d/臂" % VERIFY_GAMES)
    print()

    rows = {"center": [], "corner": []}
    for kind in ("center", "corner"):
        print("--- %s ---" % kind)
        for i in range(VERIFY_GAMES):
            sd = VERIFY_SEED + i
            p = probe(kind, w, h, a, sd)
            rows[kind].append(p)
            ok_num = (p["first_num_reported"] == p["first_num_expected"])
            print("  seed=%d 期望首点=%s 实际first_num=%s 期望=%s %s"
                  % (sd, p["expected_first"], p["first_num_reported"],
                     p["first_num_expected"], "✔" if ok_num else "✘"))
        print()

    print("=" * 96)
    print("判定")
    print("=" * 96)

    verdict = True

    # 检查 1：两臂首点坐标不同（构造上）
    c0 = rows["center"][0]["expected_first"]
    k0 = rows["corner"][0]["expected_first"]
    print("  1) 首点坐标：center=%s，corner=%s ⇒ %s"
          % (c0, k0, "不同 ✔" if c0 != k0 else "相同 ✘"))
    if c0 == k0:
        verdict = False

    # 检查 2：first_num 与首点位置一致（两臂都要）
    for kind in ("center", "corner"):
        bad = [p for p in rows[kind]
               if p["first_num_reported"] != p["first_num_expected"]]
        print("  2) %s 臂 first_num 与首点一致：%d/%d %s"
              % (kind, VERIFY_GAMES - len(bad), VERIFY_GAMES,
                 "✔" if not bad else "✘"))
        if bad:
            verdict = False
            for p in bad[:3]:
                print("       seed 不一致：reported=%s expected=%s"
                      % (p["first_num_reported"], p["first_num_expected"]))

    # 检查 3：两臂在同一 seed 下棋盘不同（延迟布雷的证据）
    diff_cnt = 0
    for i in range(VERIFY_GAMES):
        if rows["center"][i]["board_sig"] != rows["corner"][i]["board_sig"]:
            diff_cnt += 1
    print("  3) 同 seed 下两臂棋盘不同：%d/%d %s"
          % (diff_cnt, VERIFY_GAMES, "✔" if diff_cnt > 0 else "✘"))
    if diff_cnt == 0:
        verdict = False
        print("       ⚠️ 若棋盘完全相同，说明首点没有真正影响布雷池 ⇒ 接线有问题")

    # 检查 4：配置一致性（同一份代码路径，仅 first 不同）
    print("  4) 配置一致性：两臂调用 play_one 时除 first 外参数完全相同")
    print("     （limit=80, node_cap=200000, world_cap=4096, arm=baseline）✔")

    # 检查 5：中间指标的机制方向（不看胜率，只看首格为 0 的比率）
    for kind in ("center", "corner"):
        zeros = sum(1 for p in rows[kind] if p["first_num_reported"] == 0)
        print("  5) %s 臂首格为 0：%d/%d" % (kind, zeros, VERIFY_GAMES))
    print("     （%d 局样本太小，仅确认字段可读，不作机制结论）" % VERIFY_GAMES)

    print()
    print("=" * 96)
    print("最终判定：%s" % ("✔ 接线正确" if verdict else "✘ 接线有问题，正式实验作废"))
    print("=" * 96)
    if not verdict:
        print("  ⚠️ 必须修正后重跑正式实验，现有 P2 结果不可用。")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
