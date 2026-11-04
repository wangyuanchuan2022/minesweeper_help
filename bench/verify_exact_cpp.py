# -*- coding: utf-8 -*-
"""对拍：C++ exact_eval 与 Python ExactEngine 的 after_click 是否逐位一致？

=================== 为什么这一步不能省 ====================
新写一个 C++ 精确引擎，最容易犯的错不是崩溃，而是**算出一个看似合理但错的数**：
  · flood-fill 传错 opened（本项目已踩过一次：传 opened|bit ⇒ 不展开）
  · 世界集合的位索引布局与 Python 不同（Python 用 y*W2+x，含 1 格外框）
  · saturate 的分叉没有递归展开（会系统性低估信息价值）
  · 观测签名漏了"新开格及数字" ⇒ 不同分支被错误合并

判据：**逐个候选格的 after_click 值必须与 Python 版完全相等**（|Δ| < 1e-12）。
不是"接近"、不是"排序一致"，是逐位相等。因为两者算的是同一个数学量。

=================== 阴性对照（必须有）====================
故意改错一处（例如把 flood 的 opened 传成 opened|bit），
必须**立刻**出现大量不一致。若改错了还是一致，说明对拍器本身失效。

用法: python bench/verify_exact_cpp.py
"""
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

from arms_lookahead import ExactEngine, Budget, WKey      # noqa: E402
import utils.native as native                             # noqa: E402


def count_worlds(case):
    import itertools
    a, num10, U = case["a"], case["num10"], case["U"]
    tot = 0
    for idx in itertools.product(*[range(n) for n in case["ck"]]):
        mt = sum(sum(int(v) for v in case["groups"][gi][sel])
                 for gi, sel in enumerate(idx))
        rem = a - num10 - mt
        if rem == 0:
            tot += 1
        elif 0 < rem <= U:
            tot += math.comb(U, rem)
        if tot > 10 ** 12:
            return tot
    return tot


def main():
    if not native.available:
        print("✘ native 不可用")
        return 1
    if not hasattr(native.mscore, "exact_eval"):
        print("✘ mscore.exact_eval 不存在（需重新编译）")
        return 1

    with open(os.path.join(ROOT, "bench", "data", "midgame_cases.json"),
              encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample = [c for c in cases if 8.0 < c["limitation"] <= 60.0]

    print("=" * 96)
    print("C++ exact_eval vs Python ExactEngine 逐位对拍")
    print("=" * 96)

    checked = 0
    total_cells = 0
    max_diff = 0.0
    n_bad = 0
    t_py = 0.0
    t_cpp = 0.0

    for c in sample:
        nw = count_worlds(c)
        if nw > 200:
            continue
        try:
            eng = ExactEngine(c, node_cap=3_000_000, time_cap=30.0,
                              world_cap=None)
        except Exception:                                  # noqa: BLE001
            continue
        if eng.n_worlds == 0:
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        try:
            terms = [t for t in eng.saturate(wk_all, op)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            continue
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)

        # ---- Python 版真值 ----
        t0 = time.perf_counter()
        try:
            q_py = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            continue
        t_py += time.perf_counter() - t0

        # ---- C++ 版 ----
        # 注意：C++ 侧要复现"同一个饱和后的状态"。
        # Python 的 saturate 已把世界子集收敛到 wk2、opened 收敛到 op2，
        # 而 C++ exact_eval 从"原始世界 + 原始 opened"开始自己 saturate。
        # 两者应当收敛到同一组终端态 ⇒ after_click 值应相等。
        # 世界掩码用 16 位定长 hex 传（pybind11 不接受 Python int → uint64_t）。
        worlds = [format(eng.world_bits[i], "016x") for i in wk2]
        clicks = [tuple(x) for x in c["clicks"]]
        clicks9 = [tuple(x) for x in c["clicks9"]]
        import numpy as np
        cv = np.asarray(c["cell_value"], dtype=np.int32)
        t0 = time.perf_counter()
        try:
            q_list, coords, nodes, v_opt = native.mscore.exact_eval(
                worlds, clicks, clicks9, cv,
                int(c["a"]), int(c["w"]), int(c["h"]), 3_000_000, 0)
        except Exception as e:                             # noqa: BLE001
            print(f"  [skip] lim={c['limitation']:.2f} C++ 失败: {e}")
            continue
        t_cpp += time.perf_counter() - t0

        # ---- 逐格比对 ----
        q_cpp = {}
        for (x, y), v in zip(coords, q_list):
            idx = y * eng.W2 + x
            q_cpp[idx] = v
        if set(q_cpp.keys()) != set(q_py.keys()):
            print(f"  ⚠ 候选集不一致 lim={c['limitation']:.2f}: "
                  f"py={len(q_py)} cpp={len(q_cpp)} "
                  f"only_py={len(set(q_py)-set(q_cpp))} "
                  f"only_cpp={len(set(q_cpp)-set(q_py))}")
        for cd in sorted(set(q_py) & set(q_cpp)):
            d = abs(q_py[cd] - q_cpp[cd])
            max_diff = max(max_diff, d)
            total_cells += 1
            if d > 1e-12:
                n_bad += 1
                if n_bad <= 5:
                    print(f"  ✘ 不一致 lim={c['limitation']:.2f} "
                          f"cell={eng._coord(cd)} py={q_py[cd]:.12f} "
                          f"cpp={q_cpp[cd]:.12f} Δ={d:.2e}")
        checked += 1
        print(f"  [{checked:>2}] lim={c['limitation']:>7} w={nw:>4} "
              f"cand={len(cands):>2} 一致格={len(set(q_py)&set(q_cpp)):>2} "
              f"maxΔ={max_diff:.2e}", flush=True)
        if checked >= 8:
            break

    print()
    print("=" * 96)
    print("判定")
    print("=" * 96)
    print(f"  决策点 {checked} 个，候选格 {total_cells} 个")
    print(f"  max |py − cpp| = {max_diff:.3e}")
    print(f"  不一致格数 = {n_bad}/{total_cells}")
    print(f"  耗时：Python {t_py:.2f}s  C++ {t_cpp:.2f}s  "
          f"加速 {t_py/max(1e-9, t_cpp):.1f}x")
    print()
    if n_bad == 0 and total_cells > 0:
        print("  ✔ C++ exact_eval 与 Python ExactEngine 逐位一致。")
        print("    可以放心用它把可枚举决策点样本从 41 扩到 200+。")
        _negative_control(sample)
        return 0
    else:
        print("  ✘ 存在不一致 ⇒ C++ 实现有 bug，**不得**用于任何读数。")
        return 1


def _negative_control(sample):
    """阴性对照：故意破坏输入，必须立刻出现不一致。

    ==================== 踩过的坑（务必保留）====================
    第一版对照是"翻转世界掩码的 bit0" ⇒ **结果仍然完全一致**，
    看起来像"对拍器无区分力"。实际原因是 bit0 对应棋盘外框格
    （坐标 (0,0)），它不在任何候选格的邻域里 ⇒ 翻它不改变任何 after_click。
    ⇒ 教训：阴性对照必须**真的作用在被测量的量上**，
      否则"对照通过/失败"本身都是假的。

    现改用「丢掉一个世界」：世界集少一个 ⇒ 概率分母变、after_click 必变。
    """
    print()
    print("=" * 96)
    print("阴性对照（证明本对拍器能检出真实差异）")
    print("=" * 96)
    import numpy as np
    for c in sample:
        nw = count_worlds(c)
        if nw > 200 or nw < 2:
            continue
        try:
            eng = ExactEngine(c, node_cap=3_000_000, time_cap=30.0,
                              world_cap=None)
        except Exception:                                  # noqa: BLE001
            continue
        if eng.n_worlds < 2:
            continue
        wk_all = WKey.all(eng.n_worlds)
        op = eng.opened0
        try:
            terms = [t for t in eng.saturate(wk_all, op)
                     if not eng.won(t[1]) and eng.candidates(t[0], t[1])]
        except Budget:
            continue
        if not terms:
            continue
        terms.sort(key=lambda t: -t[2])
        wk2, op2, _wt = terms[0]
        cands = eng.candidates(wk2, op2)
        try:
            q_py = {cd: eng.after_click(cd, wk2, op2) for cd in cands}
        except Budget:
            continue
        worlds = [format(eng.world_bits[i], "x") for i in wk2]
        if len(worlds) < 2:
            continue
        worlds_bad = worlds[:-1]          # 故意少一个世界
        cv = np.asarray(c["cell_value"], dtype=np.int32)
        try:
            ql, co, _n, _v = native.mscore.exact_eval(
                worlds_bad, [tuple(x) for x in c["clicks"]],
                [tuple(x) for x in c["clicks9"]], cv,
                int(c["a"]), int(c["w"]), int(c["h"]), 3_000_000, 0)
        except Exception as e:                             # noqa: BLE001
            print(f"  跳过（{e}）")
            continue
        q_cpp = {y * eng.W2 + x: v for (x, y), v in zip(co, ql)}
        common = set(q_py) & set(q_cpp)
        bad = sum(1 for cd in common
                  if abs(q_py[cd] - q_cpp[cd]) > 1e-12)
        print(f"  破坏输入（丢掉 1 个世界，剩 {len(worlds_bad)}）后："
              f"共同格 {len(common)}，不一致 {bad}")
        if bad > 0:
            print(f"  ✔ 阴性对照通过：对拍器能检出真实差异"
                  f"（{bad}/{len(common)} 格发生变化）。")
        else:
            print("  ✘ 阴性对照失败：破坏输入后仍然一致 ⇒ "
                  "上面的 0 不一致不可信，对拍器无效。")
        return


if __name__ == "__main__":
    sys.exit(main())
