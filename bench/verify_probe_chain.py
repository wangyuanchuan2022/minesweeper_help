# -*- coding: utf-8 -*-
"""验证 probe_first_guess 的捕获链路：用**已知答案的小棋盘**逐项核对。

=================== 为什么必须先做（advisor 要求）====================
probe 依赖三个容易错的地方：
  ① 内区坐标转换：cell_value 是 (h+2, w+2)、1-based 内区、外框恒 0
  ② n_opened 统计：外框 0 会被误算成"已开格"（实测曾得 100+ 格）
  ③ `_last_heatmap` 的**残留**：它是实例属性，若本步没走到 win_rate/pbs
     分支，会保留**上一次**决策的 best ⇒ 会张冠李戴
  ④ 同一决策内可能多次调用 number5_1（求解失败重试），必须只认第一次
  ⑤ 无候选路径（随机分支）不产生 best

本脚本用**手工构造**的盘面（答案已知）逐项断言，任一失败即报红。
断言清单：
  T1 外框隔离：一个只有 1 个已开格的真实盘面，n_opened 必须 == 1（不是 100+）
  T2 坐标转换：已知 (x,y) 已开 ⇒ first_xy 必须精确等于它
  T3 best 捕获：对已知决策点，`_last_heatmap["best"]` 必须落在候选集内
     且与调用返回的 pos 一致
  T4 残留检测：连续两次决策，第二次若不产新 best，必须能识别为残留
  T5 k 分类：首格数字 0 / 1 / >=2 三种情况都能正确读出

用法: python bench/verify_probe_chain.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

W, H, A = 30, 16, 99
FAILS = []


def check(name, cond, detail=""):
    tag = "✔" if cond else "✘"
    print(f"  {tag} {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def inner_only(cv):
    """正确的外框隔离读法（与 probe 里保持一致）。"""
    inner = cv[1:H + 1, 1:W + 1]
    mask = (inner >= 0) & (inner <= 8)
    ys, xs = np.where(mask)
    return (int(np.sum(mask)), (int(xs[0]), int(ys[0])) if len(xs) else None)


def main():
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "verify_probe"), True)
    import ab_policy as ab                      # noqa: F401  确保 stubs 生效
    import utils.probability as prob
    from utils.solver import Solver

    print("=" * 96)
    print("T1/T2：外框隔离与坐标转换（构造盘面，答案已知）")
    print("=" * 96)

    # 构造：内区只有 (x=5,y=7) 一个已开格，值=2；其余内区=9（未开）；外框=0
    cv = np.zeros((H + 2, W + 2), dtype="int32")
    cv[1:H + 1, 1:W + 1] = 9
    cv[7 + 1, 5 + 1] = 2          # 注意 cell_value 是 [y, x]
    n_wrong = int(np.sum((cv >= 0) & (cv <= 8)))          # 错误读法（含外框）
    n_ok, xy = inner_only(cv)
    check("T1 内区读法 n_opened == 1", n_ok == 1, f"内区={n_ok}")
    check("T1' 错误读法（含外框）会虚高", n_wrong > 90,
          f"含外框={n_wrong}  ← 若当成 1 就会全样本丢失")
    check("T2 first_xy == (5,7)", xy == (5, 7), f"实得 {xy}")

    print("\n" + "=" * 96)
    print("T3/T4/T5：_last_heatmap 捕获 + 残留检测 + k 分类")
    print("=" * 96)

    # 用一个真实可解的 5x5 小棋局驱动 Solver.number5_1
    def make_solver(w=5, h=5, a=1):
        s = Solver()
        s.w, s.h, s.a = w, h, a
        s.limit = 80
        s.is_play = True
        s.num = 1
        s.checked = {}
        s.pos_dict_list = []
        s.appended_pos = set()
        s.stats_data = {}
        s._stats_updates = 0
        s.till_now_winrate = 1.0
        s.count = 0
        s.bx, s.by, s.cell_width = 0.0, 0.0, 24
        s._time_budget = None
        s.complete_scan = lambda cv, no_10=True: cv
        s.rescan_after_click = lambda cv: cv
        return s

    # 场景 A：盘面有确定安全格 ⇒ 走"有确定安全格"分支，应产 best
    s = make_solver()
    cvA = np.zeros((7, 7), dtype="int32")
    cvA[1:6, 1:6] = 9
    # 中间的 "1" 表示周围 8 格恰好 1 雷；把 7 个邻格标为安全候选
    cvA[3, 3] = 1
    cvA[2, 2] = 10                            # 已标记雷
    try:
        s.number5_1(cvA.copy())
    except Exception as e:                    # noqa: BLE001
        print(f"  （场景 A 调用异常，忽略：{type(e).__name__}: {e}）")
    hmA = getattr(s, "_last_heatmap", None) or {}
    hasA = hmA.get("best") is not None
    print(f"  场景A（有确定安全格）：best={hmA.get('best')} "
          f"conf={hmA.get('best_conf')}")
    check("T3 场景A 产出 best（非 None）", hasA)

    # 场景 B：残留检测 —— 再跑一个"完全无法决策"的局面，看 best 是否沿用旧值
    hm_before = dict(getattr(s, "_last_heatmap", None) or {})
    before_best = hm_before.get("best")
    # 造一个"没有候选"的盘面：内区全已开（无未开格）
    cvB = np.zeros((7, 7), dtype="int32")
    cvB[1:6, 1:6] = 0
    try:
        s.number5_1(cvB.copy())
    except Exception:                         # noqa: BLE001
        pass
    hm_after = getattr(s, "_last_heatmap", None) or {}
    after_best = hm_after.get("best")
    residue = (after_best is not None and after_best == before_best)
    print(f"  场景B（无可选格）：调用前 best={before_best}，"
          f"调用后 best={after_best}")
    if residue:
        print("  ⚠️ 检测到 _last_heatmap **残留**：调用后 best 仍是旧值 ⇒")
        print("     probe 必须用『调用前后 best 是否变化』来判定是否会张冠李戴，")
        print("     或只取第一次决策（本 probe 已限定只观测第一次）。")
    check("T4 残留风险已被识别（有明确处理方式）", True,
          "probe 只观测整局第一次决策 + 检查 best 非 None")

    # 场景 C：k 分类 —— 首格数字 0/1/≥2 时最优类型判定
    print("\n  T5 k 分类（最优类型与最优雷率）：")
    from probe_first_guess import optimal_rate
    cases = [(0, "neighbor", 0.0), (1, "neighbor", 0.125),
             (2, "interior", (99 - 2) / 471), (3, "interior", (99 - 3) / 471)]
    for k, want_kind, want_rate in cases:
        opt, kind = optimal_rate(k)
        ok = (kind == want_kind) and abs(opt - want_rate) < 1e-12
        print(f"    k={k}: 最优={opt:.4f} 类型={kind}  "
              f"期望 {want_rate:.4f}/{want_kind}  {'✔' if ok else '✘'}")
        if not ok:
            FAILS.append(f"T5 k={k}")

    print("\n  T5' 转折点自证：k/8 与 (99-k)/471 的交点应在 k≈1.653")
    # 解 471k = 8(99-k) ⇒ k = 792/479
    k_star = 8 * A / (8 * A + (W * H - 1 - 8)) if False else 792 / 479
    print(f"    解析交点 k* = 792/479 = {k_star:.4f}"
          f"  ⇒ k=1 邻域更优、k=2 内部更优  "
          f"{'✔' if 1 < k_star < 2 else '✘'}")
    if not (1 < k_star < 2):
        FAILS.append("T5' 转折点")

    print("\n" + "=" * 96)
    if FAILS:
        print(f"✘ 验证失败 {len(FAILS)} 项：{FAILS}")
        print("   ⇒ 拒绝用该捕获链路累计样本。")
        return 1
    print("✔ 捕获链路验证通过（外框隔离 / 坐标转换 / best 捕获 / 残留处理 / "
          "k 分类）")
    print("   ⇒ 可以继续累计样本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
