# -*- coding: utf-8 -*-
"""2000 局胜率测试（清理后构建，与历史基线同口径，不复用旧文件避免覆盖）。

==================== 口径（逐条对齐 bench/_corner2000.py）====================
  · 30×16 / 99 雷
  · 角落首点（0-based [0,0] ≡ 1-based (1,1)），延迟布雷只排除首点本格
  · 循环调 mscore.decide(frame)，decide 默认参数（不传 wr_threshold/node_cap）
  · certain_safe → 批量点击 tied[:64]
  · 其他 → 点击 (x,y)；无效坐标兜底点第一个未开格
  · 纯 C++ decide 循环（不经 Python number5_1 编排链）

==================== 与历史基线的对照 ====================
  历史旧臂（bench/data/_corner2000.json 的 cpp 字段，pyd 时代含 LAYER 2）：
      761/2000 = 38.05%
  本轮新构建（pyd sha8 53E73344，LAYER 2 已删 + 死代码清理）
  ⇒ 期望：与旧臂差异不显著（LAYER 2 实测 reason=win_rate 几乎零触发）。

==================== 内存纪律（用户要求「注意内存上限，不要并发太多」）====================
  · 单进程串行（不并发）—— 2000 局只需 ~2 分钟，无需并行
  · 每局后 gc.collect()（防 PyQt/numpy 碎片累积）
  · 每局记录 commit（Private Bytes），全程监控峰值
  · 失败即写盘（增量落盘，中断可续）

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/run_2000_after_cleanup.py
"""
import gc
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32
N = 2000
STORE = os.path.join(ROOT, "bench", "data", "_corner2000_after_cleanup.json")
OLD = os.path.join(ROOT, "bench", "data", "_corner2000.json")


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    dd = 1 + z * z / n
    cc = p + z * z / (2 * n)
    hh = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (cc - hh) / dd, (cc + hh) / dd


def main():
    import numpy as np
    import psutil
    from utils import native
    import hashlib

    if not native.has_decide():
        raise SystemExit("[FATAL] native.decide 不可用——拒绝在回退口径上测胜率")
    pyd = native.mscore.__file__
    sha8 = hashlib.sha256(open(pyd, "rb").read()).hexdigest()[:8].upper()
    print("pyd = %s\nsha8 = %s" % (pyd, sha8))

    d = json.load(io.open(os.path.join(ROOT, "bench", "data",
                                       "_corner2000.json"), encoding="utf-8"))
    layouts = d["layouts"]
    assert len(layouts) == N, "layouts 数量 %d != %d" % (len(layouts), N)

    class Sim:
        """与 bench/_corner2000.py 的 Sim 逐条一致的模拟器。"""

        def __init__(self, mines_xy1):
            self.mine = [[False] * W2 for _ in range(H + 2)]
            for (mx, my) in mines_xy1:
                self.mine[my][mx] = True
            self.cv = np.zeros((H + 2, W2), dtype=np.int32)
            for y in range(1, H + 1):
                for x in range(1, W + 1):
                    self.cv[y][x] = 9
            self.opened = 0
            self.dead = False
            self.steps = 0
            self.won = False
            self._flood(1, 1)   # 角落首点 1-based (1,1)
            self.won = self.opened >= W * H - A

        def number(self, x, y):
            c = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 1 <= nx <= W and 1 <= ny <= H and self.mine[ny][nx]:
                        c += 1
            return c

        def _flood(self, sx, sy):
            stack = [(sx, sy)]
            while stack:
                x, y = stack.pop()
                if self.cv[y][x] != 9 or self.mine[y][x]:
                    continue
                n = self.number(x, y)
                self.cv[y][x] = n
                self.opened += 1
                if n == 0:
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            stack.append((x + dx, y + dy))

        def click(self, x, y):
            self.steps += 1
            if self.mine[y][x]:
                self.dead = True
                return
            self._flood(x, y)
            self.won = self.opened >= W * H - A

        def frame(self):
            return self.cv

    proc = psutil.Process()
    commit0 = proc.memory_info().private / (1024 * 1024)
    peak = commit0

    rows = []
    reasons = {}
    t0 = time.perf_counter()
    ms_total = 0.0

    for i in range(N):
        g = Sim(layouts[i])
        game_ms = 0.0
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = native.mscore.decide(g.frame(), a=A, w=W, h=H)
            game_ms += (time.perf_counter() - t1) * 1000.0
            reason = str(r["reason"]).split(":")[0]
            reasons[reason] = reasons.get(reason, 0) + 1

            if reason == "certain_safe":
                batch = [(int(c[0]), int(c[1])) for c in (r["tied"] or [])][:64]
                clicked = False
                for (bx, by) in batch:
                    if g.cv[by][bx] == 9:
                        g.click(bx, by)
                        clicked = True
                        if g.dead or g.won:
                            break
                if not clicked:
                    break
                continue
            x, y = int(r["x"]), int(r["y"])
            if x >= 1 and y >= 1:
                g.click(x, y)
            else:
                fb = None
                for yy in range(1, H + 1):
                    for xx in range(1, W + 1):
                        if g.cv[yy][xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                g.click(fb[0], fb[1])

        rows.append({"i": i, "won": bool(g.won), "steps": g.steps,
                     "ms": round(game_ms, 1)})
        ms_total += game_ms

        # 内存纪律：每局 gc + 峰值监控
        gc.collect()
        cur = proc.memory_info().private / (1024 * 1024)
        if cur > peak:
            peak = cur

        if (i + 1) % 200 == 0:
            w = sum(1 for r in rows if r["won"])
            el = time.perf_counter() - t0
            print("  %4d/%d  胜 %-4d (%.2f%%)  %.0fs  commit峰值 %.0fMB"
                  % (i + 1, N, w, 100 * w / (i + 1), el, peak), flush=True)
            # 增量落盘（中断可续）
            with io.open(STORE + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"rows": rows, "pyd_sha8": sha8,
                           "reasons": reasons}, f, ensure_ascii=False)
            os.replace(STORE + ".tmp", STORE)

    el = time.perf_counter() - t0
    wins = sum(1 for r in rows if r["won"])
    lo, hi = wilson(wins, N)

    # 旧臂对照
    old_rows = []
    try:
        od = json.load(io.open(OLD, encoding="utf-8"))
        old_rows = od.get("cpp", [])
    except (OSError, ValueError):
        pass
    old_wins = sum(1 for c in old_rows if c.get("won")) if old_rows else None

    result = {
        "pyd_sha8": sha8,
        "games": N, "wins": wins,
        "rate_pct": round(100 * wins / N, 2),
        "ci95": [round(100 * lo, 2), round(100 * hi, 2)],
        "decisions": sum(reasons.values()),
        "reasons": reasons,
        "wall_s": round(el, 1),
        "ms_per_game_avg": round(ms_total / N, 1),
        "commit_base_mb": round(commit0, 0),
        "commit_peak_mb": round(peak, 0),
        "commit_delta_mb": round(peak - commit0, 0),
        "rows": rows,
    }
    with io.open(STORE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    print("\n" + "=" * 74)
    print("2000 局结果（清理后构建）")
    print("=" * 74)
    print("  胜率: %d/%d = %.2f%%   95%%CI [%.2f, %.2f]"
          % (wins, N, 100 * wins / N, 100 * lo, 100 * hi))
    print("  决策总数: %d   总耗时 %.1fs   平均 %.1fms/局"
          % (sum(reasons.values()), el, ms_total / N))
    print("  reason 分布: %s" % json.dumps(reasons, sort_keys=True))
    print("  commit: 基线 %.0fMB → 峰值 %.0fMB（增量 %.0fMB）"
          % (commit0, peak, peak - commit0))
    if old_wins is not None:
        print("\n  旧臂（含 LAYER 2）: %d/%d = %.2f%%"
              % (old_wins, len(old_rows), 100 * old_wins / len(old_rows)))
        print("  变化: %+.2f pp" % (100 * wins / N - 100 * old_wins / len(old_rows)))
    print("\n  结果落盘: %s" % STORE)


if __name__ == "__main__":
    main()
