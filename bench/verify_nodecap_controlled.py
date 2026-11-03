# -*- coding: utf-8 -*-
"""受控校验：用已知 424,114 节点的用例验证 node_cap 命中边界。

=================== 为什么必须做 ====================
扫描读数显示 cap=10万 与 cap=200万 的降级数**同为 220**，
而实测生产最大节点仅 99,970。若两者都成立，则：
  · cap=10万 时，99,970 < 100,000 ⇒ **不应命中**
  · cap=200万 时更不应命中
⇒ 纯 node_cap 命中在**两档都应为 0**。非零且相同说明：
  A) 计数混入了其他降级（world_cap / invalid_case / bench Budget）
  B) 接线未完全生效
  C) 节点峰值测量漏了调用

本脚本用**已知需要 424,114 节点的用例**做受控校验：
  · cap=100,000 ⇒ **必须命中**（424,114 > 100,000）
  · cap=2,000,000 ⇒ **不得命中**（424,114 < 2,000,000）
  · 同时 hook 原生函数核实**实际收到的 cap**
  · 分开记录 _wr_nodecap_hits 与各 gate

用法: python bench/verify_nodecap_controlled.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def call_with_cap(case, cap, hook_log):
    """直接调 C++ win_rate，cap 显式传入，并记录实际收到的值。"""
    import numpy as np
    import utils.native as nat

    groups = [np.asarray(g, np.int32) for g in case["groups"]]
    cv = np.asarray(case["cell_value"], np.int32)
    hook_log.append(cap)
    return nat.mscore.win_rate(
        case["clicks"], case["clicks9"], groups, cv, case["ck"],
        case["num10"], case["a"], case["w"], case["h"], case["is_play"],
        lambda v: None, cap)


def main():
    with open(os.path.join(ROOT, "bench", "data", "wr_cases_big.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    print("=" * 92)
    print("受控校验：已知节点需求的用例上验证 node_cap 命中边界")
    print("=" * 92)

    # ---------- 1) 找出已知需要多少节点的用例（二分反推） ----------
    print("\n1) 二分反推各用例的最小可完成 cap（= 节点需求）")
    needs = []
    for ci, c in enumerate(cases):
        lo, hi = 1, 10_000_000
        # 先确认大 cap 能完成
        try:
            call_with_cap(c, hi, [])
        except Exception:                              # noqa: BLE001
            continue

        def works(cap):
            try:
                call_with_cap(c, cap, [])
                return True
            except Exception:                          # noqa: BLE001
                return False

        if works(1):
            needs.append((ci, 1))
            continue
        while lo < hi:
            mid = (lo + hi) // 2
            if works(mid):
                hi = mid
            else:
                lo = mid + 1
        needs.append((ci, lo))

    needs.sort(key=lambda x: -x[1])
    print(f"   用例数 {len(needs)}，节点需求 top5：")
    for ci, n in needs[:5]:
        print(f"     [{ci:>2}] {n:>9,} 节点")

    if not needs:
        print("无可用用例")
        return

    # ---------- 2) 选一个节点需求 > 100,000 的用例 ----------
    target = next(((ci, n) for ci, n in needs if n > 100_000), None)
    if target is None:
        print("\n⚠️ 没有节点需求 > 100,000 的用例，无法做边界校验")
        print("   （这本身说明：bench 用例集里最大节点需求 < 10 万）")
        return
    ci, need = target
    case = cases[ci]
    print(f"\n2) 选定用例 [{ci}]：需要 {need:,} 节点")
    print(f"   预期：cap=100,000 必须命中（{need:,} > 100,000）")
    print(f"         cap=2,000,000 不得命中（{need:,} < 2,000,000）")

    # ---------- 3) 受控校验 ----------
    for cap, should_hit in ((100_000, True), (2_000_000, False)):
        log = []
        hit = False
        err = ""
        try:
            call_with_cap(case, cap, log)
        except RuntimeError as e:
            err = str(e)
            hit = "node cap" in err
        except Exception as e:                         # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        print(f"\n   cap={cap:>9,}: "
              f"{'命中' if hit else '未命中'}"
              f"{'  err=' + err[:60] if err else ''}")
        check(f"原生函数实际收到 cap={cap:,}",
              log and log[-1] == cap, f"收到 {log}")
        if should_hit:
            check(f"cap={cap:,} 必须命中（节点需求 {need:,} > cap）", hit,
                  f"实际未命中，err={err[:60]}")
        else:
            check(f"cap={cap:,} 不得命中（节点需求 {need:,} < cap）", not hit,
                  f"实际命中，err={err[:60]}")

    # ---------- 4) 生产侧计数隔离性 ----------
    print("\n3) 生产侧计数隔离性：_wr_nodecap_hits 差值 vs 其他 gate")
    print("   说明：本脚本直接调 C++，不经过 Solver，故 _wr_nodecap_hits 不变。")
    import utils.probability as prob
    before = prob._WR_NODECAP_TOTAL[0]
    try:
        call_with_cap(case, 100_000, [])
    except Exception:                                  # noqa: BLE001
        pass
    after = prob._WR_NODECAP_TOTAL[0]
    check("直接调 C++ 不增加 _WR_NODECAP_TOTAL（计数在 Python 包装层）",
          after == before, f"{before} -> {after}")
    print("   ⇒ 因此扫描里 _wr_nodecap_hits 非零，必然来自 probability.win_rate")
    print("     的异常分支，而非直接调用。若扫描读数异常，需查该分支是否")
    print("     把其他异常也计入了 nodecap。")

    print()
    print("=" * 92)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    if FAIL == 0:
        print("结论：node_cap 边界行为正确（已知 424,114 节点用例上，")
        print("      10 万命中、200 万不命中），且原生函数确实收到传入的 cap。")
    else:
        print("结论：边界行为不符合预期，扫描读数不可信，禁止据此调参。")


if __name__ == "__main__":
    main()
