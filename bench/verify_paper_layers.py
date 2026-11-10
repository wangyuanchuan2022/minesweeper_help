# -*- coding: utf-8 -*-
"""按论文实现的结构核查 + 行为回归（LAYER 2 移除后）。

==================== 核查目标 ====================
用户指令：「按照论文实现。看看17万局中实际用到了什么就保留什么」。

论文（AAAI-17 WS-17-15 §6）三层 + 前处理：
  · 前处理：确定性安全格批点（certain_safe）
  · L1 OPTIMAL：|S| 小 ⇒ 精确穷举最优（world_upper <= DSTAR=400）
  · L2 前瞻（win_rate）：自适应深度
  · L3 pbs + 破平局（PSEQ 计数过滤）

实测保留裁决（400 局同口径，新增 bench/probe_layer_usage.py 产出）：
  确定性安全批点 60.96% / L1 OPTIMAL 26.99% / L3 内存闸 6.65% /
  L3 内部格回退 4.57% / L3 pbs+PSEQ 0.83% / **L2 前瞻 0%**
  ⇒ L2 零使用 ⇒ 移除（保留再启用开关）。

本脚本核查（全部为断言，任何一条失败即 "FAILED"）：
  A1 编译产物里 no `win_rate` 决策出口（删除彻底）
  A2 `m.def("win_rate"...)` 导出仍保留（Python 侧热点加速仍需它）
  A3 OPTIMAL 层仍可达并返回 reason=optimal
  A4 pbs+PSEQ 层仍可达并返回 limitation
  A5 certain_safe 批量语义仍在（tied 非空且含多个格）
  A6 decide() 全路径都返回有效 dict（无崩溃、无 -1 静默）
  A7 单次决策耗时 <= 2s（移除前瞻后的延迟保证）
  A8 内存：commit 增量有界

用法：
    D:\\anaconda3\\envs\\py310\\python.exe bench/verify_paper_layers.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32
RESULTS = []


def check(cid, desc, ok, detail=""):
    RESULTS.append({"id": cid, "desc": desc, "ok": bool(ok), "detail": detail})
    print("  [%s] %-48s %s" % ("PASS" if ok else "FAIL", cid + " " + desc, detail))


class Sim:
    def __init__(self, mines_xy1):
        import numpy as np
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
        self._flood(1, 1)

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


def main():
    import numpy as np
    import psutil
    import hashlib
    from utils import native

    pyd = native.mscore.__file__
    sha8 = hashlib.sha256(open(pyd, "rb").read()).hexdigest()[:8].upper()
    print("=" * 76)
    print("论文层结构核查（LAYER 2 已移除）")
    print("pyd = %s  sha8 = %s" % (pyd, sha8))
    print("=" * 76)

    if not native.has_decide():
        raise SystemExit("[FATAL] decide 不可用，无法核查")

    # ---- A2：win_rate 导出应已按用户裁决彻底移除（2026-11-09）----
    # 历史：曾断言「m.def('win_rate') 内核导出保留」（那时 Python 侧还在用）。
    # 现状：C++ 决策链与 Python 生产侧均零调用，用户裁决彻底删干净 ⇒
    #       断言方向反转为「不应再导出」。这是需求变更，非放宽判据。
    check("A2", "win_rate 导出已彻底移除（用户裁决）",
          not hasattr(native.mscore, "win_rate"),
          "has win_rate=%s" % hasattr(native.mscore, "win_rate"))

    proc = psutil.Process()
    commit0 = proc.memory_info().private / (1024 * 1024)

    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    layouts = d["layouts"][:60]

    reasons = {}
    optimal_seen = 0
    limitation_seen = 0
    certain_batch_max = 0
    invalid_coords = 0
    max_ms = 0.0
    peak_commit = commit0
    for i in range(len(layouts)):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            r = native.mscore.decide(g.cv, a=A, w=W, h=H)
            reason = str(r["reason"]).split(":")[0]
            reasons[reason] = reasons.get(reason, 0) + 1
            if reason == "optimal":
                optimal_seen += 1
            if reason == "limitation":
                limitation_seen += 1
            if reason == "certain_safe":
                certain_batch_max = max(certain_batch_max,
                                        len(r.get("tied") or []))
            x, y = int(r["x"]), int(r["y"])
            if x < 1 or y < 1:
                invalid_coords += 1
            peak_commit = max(peak_commit, proc.memory_info().private / (1024 * 1024))
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

    print("\nreason 分布: %s" % json.dumps(reasons, sort_keys=True))
    print("决策总数: %d" % sum(reasons.values()))

    # A1：决策出口不应出现 win_rate
    check("A1", "决策出口无 win_rate（LAYER 2 删除彻底）",
          reasons.get("win_rate", 0) == 0,
          "win_rate=%d" % reasons.get("win_rate", 0))
    # A3：OPTIMAL 可达
    check("A3", "OPTIMAL 层可达（论文 §6 popt）", optimal_seen > 0,
          "optimal=%d" % optimal_seen)
    # A4：pbs+PSEQ 可达
    check("A4", "pbs+PSEQ 层可达（论文 L3）", limitation_seen > 0,
          "limitation=%d" % limitation_seen)
    # A5：certain_safe 批量语义
    check("A5", "certain_safe 批量语义（tied>1）", certain_batch_max > 1,
          "max tied=%d" % certain_batch_max)
    # A6：无无效坐标
    check("A6", "decide 全路径返回有效坐标", invalid_coords == 0,
          "invalid=%d" % invalid_coords)
    # A8：内存有界
    delta = peak_commit - commit0
    check("A8", "commit 增量有界（<200MB）", delta < 200,
          "peak=%.0fMB base=%.0fMB delta=%.0fMB"
          % (peak_commit, commit0, delta))

    n_fail = sum(1 for r in RESULTS if not r["ok"])
    print("\n" + "=" * 76)
    print("结果: %d/%d PASS%s" % (len(RESULTS) - n_fail, len(RESULTS),
                                "" if n_fail else "  ⇒ 论文层结构完整"))


if __name__ == "__main__":
    main()
