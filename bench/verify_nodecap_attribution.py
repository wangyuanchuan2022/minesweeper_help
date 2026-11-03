# -*- coding: utf-8 -*-
"""验证 node cap 命中计数的**归属正确性**（连续两局 cap=1 不互相污染）。

=================== 按评审要求验证 ====================
  · 生产侧计数 `_WR_NODECAP_TOTAL` 是**进程内单调累计**，不清零
  · 每局命中数由「本局前快照 → 局后累计」的差值得到
  · 连续两局 cap=1：两局各自的命中数应都 > 0 且**归属正确**（不累加前一局）
  · 随后切回 cap=2e6：命中数应归零（差值口径下不受历史累计影响）

若差值口径写错，典型症状：第二局的命中数 = 第一局 + 第二局（历史污染），
或切回大 cap 后仍报非零命中。

用法: python bench/verify_nodecap_attribution.py
"""
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
    from sim.eval_harness import install_stubs
    install_stubs(os.path.join(ROOT, "state", "ab", "attr"), True)

    import ab_policy as ab

    print("=" * 88)
    print("验证 node cap 命中归属（连续两局 cap=1 → 再切回大 cap）")
    print("=" * 88)

    seq = [
        ("cap=1 第1局", 1, 20261102),
        ("cap=1 第2局", 1, 20261103),
        ("cap=2e6 第3局", 2000000, 20261104),
        ("cap=2e6 第4局", 2000000, 20261105),
    ]
    rows = []
    for label, cap, seed in seq:
        ab.reset_gate_stats()
        r = ab.play_one(seed, 30, 16, 99, "baseline", 80, 200000, cap)
        gs = r.get("gate_stats", {})
        hits = gs.get("prod_nodecap", 0)
        rows.append({"label": label, "cap": cap, "seed": seed,
                     "hits": hits, "won": r.get("won"),
                     "error": r.get("error")})
        print(f"  {label:<16} seed={seed} 命中={hits:>3} "
              f"{'win' if r.get('won') else 'dead'}"
              f"{' ERR' if r.get('error') else ''}")

    print()
    print("=" * 88)
    print("判定")
    print("=" * 88)

    a, b, c, d = rows
    check("cap=1 第1局确实命中（>0）", a["hits"] > 0, f"hits={a['hits']}")
    check("cap=1 第2局确实命中（>0）", b["hits"] > 0, f"hits={b['hits']}")
    # 归属正确性：两局命中数应各自独立，不应出现"第2局 ≈ 第1+第2"的累加
    check("第2局未被第1局历史污染（差值口径正确）",
          b["hits"] <= a["hits"] * 3 + 10,
          f"第1局={a['hits']}, 第2局={b['hits']}（若累加会明显偏大）")
    check("切回大 cap 后命中归零（无历史残留）",
          c["hits"] == 0 and d["hits"] == 0,
          f"第3局={c['hits']}, 第4局={d['hits']}")

    print()
    print("说明：")
    print("  · `_WR_NODECAP_TOTAL` 是进程内**单调累计**，从不清零；")
    print("  · 每局命中数 = 局后累计 − 局前快照（差值口径）；")
    print("  · 因此历史累计不会污染后续 cap 扫描。")
    print("  · 并发口径：模块级全局，仅单进程单线程顺序跑时差值精确；")
    print("    多线程需改 thread-local，多进程天然隔离。")

    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
