# -*- coding: utf-8 -*-
"""阳性对照：确认新 pyd 的 LAYER 2（纯 C++ ExactEngine）真的会被触发。

为什么必须先跑这个：
  2026-11-09 把 LAYER 2 从 py_win_rate 换成 ExactEngine 并下调 COST_CAP。
  如果 cost 闸门过窄，2000 局实测可能**一次都没进 LAYER 2**——那测出来的
  胜率与旧 pyd 的差异就与本次改动无关（在测"没改的东西"，假阴性）。
  所以先用 MSW_COST_CAP_LOG2=40 强制放开，验证：
    ① reason 里出现 win_rate（LAYER 2 可达）；
    ② 该路径下 commit（Private Bytes）不爆（本次 OOM 修复的核心断言）；
    ③ 默认 19.4 时 win_rate 的触发率（供 2000 局读数解释）。

⚠️ 仪器要点（本脚本已实测踩过）：COST_CAP_LOG2 / DSTAR / NODE_CAP 在
cpp/mscore.cpp 里都是 `static` 一次性求值（magic static），**同进程内改
环境变量不生效**。两种臂必须各自起独立子进程，否则两臂读到同一个常量，
测出来"完全一致"是假象（历史已踩过同类仪器故障）。

用法（py310）：
    <PY310_PYTHON> bench/probe_nopywr_live.py
    <PY310_PYTHON> bench/probe_nopywr_live.py --games 20
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32
PYD_EXPECT = "71A63DB3"


def pyd_info():
    from utils import native
    if native.mscore is None or not getattr(native, "has_decide", lambda: False)():
        raise SystemExit("[FATAL] native.decide 不可用——拒绝在纯 Python 回退口径上测量")
    f = native.mscore.__file__
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
    return f, sha[:8].upper()


class Sim:
    """与 bench/_corner2000.py 逐条对齐的模拟器（角落首点、延迟布雷）。"""

    def __init__(self, mines_xy1):
        self.mine = [[False] * W2 for _ in range(H + 2)]
        for (mx, my) in mines_xy1:
            self.mine[my][mx] = True
        import numpy as np
        self.cv = np.zeros((H + 2, W2), dtype=np.int32)
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                self.cv[y][x] = 9
        self.opened = 0
        self.dead = False
        self.steps = 0
        self.won = False
        self._flood(1, 1)
        self._win()

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
        self._win()

    def _win(self):
        self.won = self.opened >= W * H - A

    def frame(self):
        return self.cv


def run_games(layouts, n, label):
    import numpy as np
    import psutil
    from utils import native
    proc = psutil.Process()
    reasons = {}
    peak_commit = 0.0
    max_call_ms = 0.0
    t0 = time.perf_counter()
    wins = 0
    for i in range(n):
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = native.mscore.decide(g.frame(), a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            max_call_ms = max(max_call_ms, dt)
            reason = str(r["reason"])
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
        wins += 1 if g.won else 0
        peak_commit = max(peak_commit, proc.memory_info().private / (1024 * 1024))
    el = time.perf_counter() - t0
    print("[%s] %d 局 胜 %d | %.1fs（%.2fs/局）| peak_commit=%.0fMB | "
          "max_call=%.0fms" % (label, n, wins, el, el / n, peak_commit, max_call_ms))
    print("[%s] reason: %s" % (label, json.dumps(reasons, sort_keys=True)))
    return {"label": label, "n": n, "wins": wins, "reasons": reasons,
            "peak_commit_mb": peak_commit, "max_call_ms": max_call_ms,
            "wall_s": el}


def arm_main(games):
    """子进程侧：只跑一臂（环境变量由父进程在 Popen 时给定）。"""
    d = json.load(open(os.path.join(ROOT, "bench", "data", "_corner2000.json"),
                       encoding="utf-8"))
    cap = os.environ.get("MSW_COST_CAP_LOG2", "default")
    res = run_games(d["layouts"][:games], games, "cap=%s" % cap)
    res["cost_cap_log2"] = cap
    out = os.environ.get("PROBE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--arm", default=None, help="内部用：子进程臂名")
    args = ap.parse_args()

    if args.arm:
        arm_main(args.games)
        return

    f, sha8 = pyd_info()
    print("pyd: %s\nsha256_8 = %s" % (f, sha8))
    if sha8 != PYD_EXPECT:
        raise SystemExit("[FATAL] pyd SHA %s != 预期 %s（口径不确定，拒绝测量）"
                         % (sha8, PYD_EXPECT))

    tmp = os.path.join(ROOT, "output")
    os.makedirs(tmp, exist_ok=True)
    arms = [
        ("默认参数（生产口径）", {}),
        ("强制放开 COST_CAP=40", {"MSW_COST_CAP_LOG2": "40",
                                  "MSW_LOOKAHEAD_BUDGET_MS": "5000"}),
    ]
    results = []
    for idx, (label, env_extra) in enumerate(arms):
        out = os.path.join(tmp, "_probe_arm%d.json" % idx)
        env = dict(os.environ)
        env.pop("MSW_COST_CAP_LOG2", None)
        env.update(env_extra)
        env["PROBE_OUT"] = out
        print("=" * 70)
        print("臂：%s  env=%s" % (label, env_extra or "(默认)"))
        rc = subprocess.call([sys.executable, os.path.abspath(__file__),
                              "--arm", "x", "--games", str(args.games)],
                             env=env, cwd=ROOT)
        if rc != 0:
            raise SystemExit("[FATAL] 臂 %s 子进程 rc=%d" % (label, rc))
        with open(out, encoding="utf-8") as fh:
            results.append(json.load(fh))

    print("=" * 70)
    print("汇总 JSON:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    forced = results[1]["reasons"]
    if "win_rate" in forced:
        print("\n判定 ✅ 强制放开时 reason 出现 win_rate ⇒ 新 ExactEngine 路径可达。")
    else:
        print("\n判定 ❌ 强制放开仍无 win_rate ⇒ LAYER 2 不可达（需查闸门前置条件），"
              "此时 2000 局读数无法反映本次改动。")
    if results[1]["peak_commit_mb"] < 500:
        print("判定 ✅ 强制 LAYER 2 下 peak_commit=%.0fMB ⇒ 内存有界（OOM 修复成立）。"
              % results[1]["peak_commit_mb"])
    else:
        print("判定 ❌ peak_commit=%.0fMB 偏高，需复查。"
              % results[1]["peak_commit_mb"])
    print("默认口径 win_rate 计数 = %s（这是 2000 局能覆盖到的 LAYER 2 比例上限）"
          % results[0]["reasons"].get("win_rate", 0))


if __name__ == "__main__":
    main()
