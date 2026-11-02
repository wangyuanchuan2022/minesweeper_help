# -*- coding: utf-8 -*-
"""确认 _pbs_compute_python 的 min() 崩溃是真缺陷还是构造数据假象。

触发点：utils/probability.py:884 `min_mine_cnt = min(mine_num)`
当 for 循环（A(ck) 全组合）里没有任何一组满足
    min_val <= _mine_num + num10 <= self.a
时，mine_num 为空 → min() 抛 ValueError → 上层 except Exception 里
回退纯 Python 又抛同样错 → 直接崩溃（不是优雅降级）。

本脚本用最小可控输入证明：
  (A) 候选过滤条件下，什么参数会让 mine_num 为空；
  (B) 真实对局中是否可能到达该参数（雷数配置/局面不一致时）。
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.helpers import make_full_solver, load_test_data


def probe(a, num10, clicks9_len, group_sums, label):
    """直接模拟 probability.py:857-884 的过滤逻辑，判断 mine_num 是否为空。"""
    min_val = a - clicks9_len
    kept = [s for s in group_sums if min_val <= (s + num10) <= a]
    empty = len(kept) == 0
    print(f"  {label:<38} a={a:<4} num10={num10:<3} len(clicks9)={clicks9_len:<3} "
          f"min_val={min_val:<4} 组合雷数候选={group_sums} → "
          f"{'❌ 空（会崩溃）' if empty else '✅ 非空'}")
    return empty


def main():
    print("过滤条件（probability.py:870）: min_val <= _mine_num + num10 <= self.a")
    print("其中 min_val = self.a - len(clicks9)")
    print("=" * 84)

    # (A) 参数扫描：找会崩溃的组合
    print("\n[A] 参数扫描（clicks9 越大 min_val 越小，越容易保留；a 越小越容易全被排除）")
    for a in (10, 30, 99):
        for num10 in (0, 20, 98):
            for c9 in (0, 10, 60):
                probe(a, num10, c9, [1, 2, 3], f"a={a} num10={num10} c9={c9}")

    print("\n[B] 关键判据：空集条件")
    print("  mine_num 为空 ⟺ 对每个组合 c 都有  c + num10 < a - len(clicks9)  或  c + num10 > a")
    print("  由于 c >= 0 且 num10 <= a，'c + num10 > a' 只在 c > a - num10 时成立")
    print("  ⇒ 最易触发：len(clicks9) 很小（min_val 接近 a）而 num10 接近 a")
    print("  即：棋盘上雷基本都被标记(num10≈a)、且内部未开格很少 —— 属于残局/识别偏误场景")

    print("\n[C] 用真实局面跑一次，看是否真的能触发（构造残局）")
    d = load_test_data("test_33.json")
    cv = np.array(d["cell_value"], np.int32)
    s = make_full_solver(w=30, h=16, a=99)
    s.is_play = False

    clicks = [tuple(c) for c in d["clicks"]]
    cs = [tuple(c) for c in d["cs"]]
    res_a, n_a, _ = s.part_solve(clicks[:8], cv.copy(), d["num10"], d["num9"], cs, _try=False)
    # 构造 a 偏小 + clicks9 偏少：让 min_val 逼近 a
    for a_try in (d["num10"] + 1, d["num10"] + 2):
        s.a = a_try
        clicks9 = [(i, j) for j in range(1, 17) for i in range(1, 31)
                   if cv[j, i] == 9 and (i, j) not in set(clicks)][:1]
        try:
            out = s.process_bigger_situation(n_a, d["num9"], d["num10"],
                                             list(clicks[:8]), clicks9,
                                             [res_a], [n_a], cv.copy(), [])
            print(f"  a={a_try} (num10={d['num10']}) → 正常返回 type={type(out)}")
        except Exception as e:
            print(f"  a={a_try} (num10={d['num10']}) → ❌ {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
