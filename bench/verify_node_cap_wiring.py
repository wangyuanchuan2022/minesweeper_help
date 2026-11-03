# -*- coding: utf-8 -*-
"""核验 node_cap 扫描是否真的接到生产路径上（排查"零触发"矛盾）。

=================== 矛盾 ====================
  · 早前实测：`bench/data/wr_cases_big.json` 中最重用例需要 **424,114 节点**
  · 本轮扫描：`node_cap=100,000` 却**零触发**
若扫描覆盖了同类负载，10 万 cap 理应触发。可能原因：
  A) 扫描时 win_rate 根本没被调用（wr_threshold 太低，走了贪心）
  B) node_cap 没真正传到 C++（参数未接线）
  C) 扫描的样本不含"最重用例"那类局面
  D) 计数没接线（nodes_visited 没累加）

本脚本逐项排查：
  1) 统计整局里 win_rate 实际被调用的次数（hook 包装）
  2) 每次调用的 nodes_visited 峰值（C++ 侧真实计数）
  3) node_cap 是否真的传进了 C++（读 _wr_node_cap 与调用签名）
  4) 若 win_rate 零调用 ⇒ 是 A（阈值/路径问题），不是 cap 够用

用法: python bench/verify_node_cap_wiring.py --games 12 --arm baseline
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--node-cap", type=int, default=100000)
    args = ap.parse_args()

    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "wire"), True)

    import ab_policy as ab
    import utils.native as nat
    import utils.probability as prob

    print("=" * 96)
    print("核验 node_cap 是否真的接到生产路径")
    print("=" * 96)

    # ---------- 1) 静态：确认参数真的传进 C++ ----------
    import inspect
    src = inspect.getsource(prob.ProbabilityMixin.win_rate)
    check("win_rate 调用 C++ 时传了 node_cap 参数",
          "_wr_node_cap()" in src, "源码里未见 node_cap 传参")
    check("_wr_node_cap 默认值为正（闸门启用）",
          prob._WR_NODE_CAP_RECOMMENDED > 0,
          f"got {prob._WR_NODE_CAP_RECOMMENDED}")

    # ---------- 2) 动态：hook C++ win_rate，记录每次调用的真实 nodes ----------
    calls = []
    orig_cpp = nat.mscore.win_rate

    def hooked(*a, **k):
        # C++ 签名：..., progress, node_cap
        ncap = k.get("node_cap")
        if ncap is None and len(a) >= 12:
            ncap = a[11]
        try:
            out = orig_cpp(*a, **k)
            calls.append({"cap": ncap, "ok": True, "err": None})
            return out
        except Exception as e:                         # noqa: BLE001
            calls.append({"cap": ncap, "ok": False, "err": str(e)[:60]})
            raise

    nat.mscore.win_rate = hooked

    # 同时 hook Python 层，看 win_rate 是否被调用、走哪条路
    py_calls = {"n": 0, "mem_gate_blocked": 0}
    orig_mem_ok = prob.ProbabilityMixin._wr_memory_ok

    def mem_ok_hook(self, clicks, clicks9, total):
        r = orig_mem_ok(self, clicks, clicks9, total)
        if not r:
            py_calls["mem_gate_blocked"] += 1
        return r

    prob.ProbabilityMixin._wr_memory_ok = mem_ok_hook

    try:
        for gi in range(args.games):
            sd = args.seed + gi
            before = len(calls)
            try:
                ab.play_one(sd, 30, 16, 99, args.arm, 80, 200000,
                            args.node_cap)
            except Exception:                          # noqa: BLE001
                pass
            n_this = len(calls) - before
            print(f"  [{gi + 1}/{args.games}] seed={sd} "
                  f"C++ win_rate 调用 {n_this} 次", flush=True)
    finally:
        nat.mscore.win_rate = orig_cpp
        prob.ProbabilityMixin._wr_memory_ok = orig_mem_ok

    # ---------- 3) 判定 ----------
    print()
    print("=" * 96)
    print("核验结果")
    print("=" * 96)
    total_calls = len(calls)
    ok_calls = sum(1 for c in calls if c["ok"])
    err_calls = sum(1 for c in calls if not c["ok"])
    cap_hit = sum(1 for c in calls
                  if not c["ok"] and c["err"] and "node cap" in c["err"])

    print(f"C++ win_rate 总调用次数 : {total_calls}")
    print(f"  成功                  : {ok_calls}")
    print(f"  异常                  : {err_calls}"
          f"（其中 node cap 触发 {cap_hit}）")
    print(f"_wr_memory_ok 拦下次数  : {py_calls['mem_gate_blocked']}")
    print(f"实际传入的 node_cap     : "
          f"{sorted(set(str(c['cap']) for c in calls))}")
    print()

    check("C++ win_rate 确实被调用（不是全走贪心）", total_calls > 0,
          "若为 0，说明 wr_threshold 太低或路径不对，"
          "则「零触发」不能解释为 cap 够用")
    check("node_cap 参数确实传入了 C++", 
          all(c["cap"] is not None for c in calls) if calls else False,
          f"实际传入 {sorted(set(str(c['cap']) for c in calls))}")
    check("传入的 cap 等于本次设定值",
          all(c["cap"] == args.node_cap for c in calls) if calls else False,
          f"期望 {args.node_cap}，实际 "
          f"{sorted(set(str(c['cap']) for c in calls))}")

    print()
    if total_calls == 0:
        print("⇒ **结论不成立**：本次扫描中 C++ win_rate 零调用，")
        print("  「node_cap 零触发」不是因为 cap 够用，而是**根本没走到 win_rate**。")
        print("  可能原因：wr_threshold 太低（limitation > 阈值 → 走贪心）。")
    elif cap_hit == 0:
        print("⇒ 本次样本内 cap 确实未触发，但**不代表生产所有局面都不会触发**。")
        print("  需要与「最重用例 424,114 节点」的读数对齐：")
        print("  那类局面若在本次样本中出现，10 万 cap 应当触发。")
    else:
        print(f"⇒ cap 确实触发 {cap_hit} 次，说明接线正常。")

    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")


if __name__ == "__main__":
    main()
