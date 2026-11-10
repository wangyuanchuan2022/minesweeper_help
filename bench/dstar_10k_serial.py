# -*- coding: utf-8 -*-
"""DSTAR 判别的 1 万局串行实测（400 / 800 / 1200 / 2000）。

==================== 为什么要 1 万局 ====================
600 局配对检验结论是「1~2pp 提升不显著」：
    800:   b/c= 2/7   p=0.180
   2000:   b/c= 2/8   p=0.109
  10000:   b/c= 3/11  p=0.057
但同向不一致对只有 9~14 对，二项检验分辨力太弱。
每臂提到 10000 局，不一致对约放大 16 倍（→ ~150 对），
此时 McNemar 能可靠分辨 1pp 量级差异。

==================== 设计要点 ====================
① **串行**（用户要求）：一次只跑一个臂，绝不并发，避免内存叠加。
② **同源布局**：1 万局 layouts 固定种子生成并缓存，四臂共用（配对检验前提）。
③ **每臂独立子进程**：DSTAR 是 magic static，同进程改环境变量无效。
④ **增量落盘**：每 500 局写盘，中断可续（--resume）。
⑤ **内存纪律**：每局 gc.collect()、监控 commit 峰值、单臂结束进程退出。
⑥ **布局与历史口径一致**：seed=20292000+i、Board(30,16,99)、open(0,0)，
   已验证与 bench/data/_corner2000.json 逐局同源。

==================== 时间估算（实测外推）====================
   500 局 DSTAR=400  → 0.6 min  ⇒ 10000 局 ≈ 12 min
   500 局 DSTAR=2000 → 1.6 min  ⇒ 10000 局 ≈ 32 min
   四档合计约 80~90 min（串行）

==================== 用法 ====================
    # 全量（建议挂后台）
    D:\\anaconda3\\envs\\py310\\python.exe bench/dstar_10k_serial.py

    # 中断续跑
    D:\\anaconda3\\envs\\py310\\python.exe bench/dstar_10k_serial.py --resume

    # 只跑某几档
    D:\\anaconda3\\envs\\py310\\python.exe bench/dstar_10k_serial.py --tiers 400 800
"""
import argparse
import gc
import io
import json
import math
import os
import random
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

W, H, A, W2 = 30, 16, 99, 32
N_GAMES = 10000
SEED_BASE = 20292000
LAYOUT_STORE = os.path.join(ROOT, "bench", "data", "_dstar_10k_layouts.json")
OUT_DIR = os.path.join(ROOT, "state", "dstar_10k")

# ---------------------------------------------------------------- worker 模板
# 用 @@XXX@@ 占位（**不用 % 格式化**，避免 %% 转义与格式串冲突 —— 已踩过坑）
WORKER_TEMPLATE = '''# -*- coding: utf-8 -*-
"""DSTAR arm worker：跑单一 DSTAR 档位，结果增量落盘。"""
import gc
import io
import json
import os
import sys
import time

ROOT = r"@@ROOT@@"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "utils"))
os.chdir(ROOT)

import numpy as np
import psutil
import mscore as td

W, H, A, W2 = 30, 16, 99, 32
LAYOUT_STORE = r"@@LAYOUTS@@"
OUT_PATH = r"@@OUT@@"
N_TOTAL = @@N@@
CHUNK = 500


class Sim:
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
        self._flood(1, 1)
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


def main():
    d = json.load(io.open(LAYOUT_STORE, encoding="utf-8"))
    layouts = d["layouts"][:N_TOTAL]
    dstar = os.environ.get("MSW_DSTAR", "400")

    done = {}
    if os.path.isfile(OUT_PATH):
        try:
            prev = json.load(io.open(OUT_PATH, encoding="utf-8"))
            for r in prev.get("rows", []):
                done[r["i"]] = r
        except (OSError, ValueError):
            done = {}
    if done:
        print("续跑：已完成 {} / {}".format(len(done), N_TOTAL), flush=True)

    proc = psutil.Process()
    commit_base = proc.memory_info().private / (1024 * 1024)
    commit_peak = commit_base

    reasons = {}
    opt_ms = []
    t0 = time.perf_counter()
    rows = [done[k] for k in sorted(done)]

    for i in range(N_TOTAL):
        if i in done:
            continue
        g = Sim(layouts[i])
        while not g.dead and not g.won and g.steps < 3000:
            t1 = time.perf_counter()
            r = td.decide(g.cv, a=A, w=W, h=H)
            dt = (time.perf_counter() - t1) * 1000.0
            rs = str(r["reason"]).split(":")[0]
            reasons[rs] = reasons.get(rs, 0) + 1
            if rs == "optimal":
                opt_ms.append(dt)
            if rs == "certain_safe":
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

        rows.append({"i": i, "won": bool(g.won), "steps": g.steps})
        gc.collect()
        cur = proc.memory_info().private / (1024 * 1024)
        if cur > commit_peak:
            commit_peak = cur

        if (len(rows) % CHUNK == 0) or (i == N_TOTAL - 1):
            el = time.perf_counter() - t0
            k = sum(1 for q in rows if q["won"])
            with io.open(OUT_PATH + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"dstar": dstar,
                           "n": N_TOTAL,
                           "rows": rows,
                           "reasons": reasons,
                           "opt_n": len(opt_ms),
                           "opt_max": (round(max(opt_ms), 2) if opt_ms else None),
                           "commit_base_mb": round(commit_base, 0),
                           "commit_peak_mb": round(commit_peak, 0)},
                          f, ensure_ascii=False)
            os.replace(OUT_PATH + ".tmp", OUT_PATH)
            print("  [DSTAR={}] {}/{}  胜 {} ({:.2f}%)  {:.0f}s"
                  .format(dstar, len(rows), N_TOTAL, k,
                          100.0 * k / len(rows), el), flush=True)

    k = sum(1 for q in rows if q["won"])
    n = len(rows)
    print("完成: DSTAR={} {}/{} = {:.2f}%  opt_n={} opt_max={}s  peak={:.0f}MB"
          .format(dstar, k, n, 100.0 * k / n, len(opt_ms),
                  (round(max(opt_ms), 2) if opt_ms else None), commit_peak))


if __name__ == "__main__":
    main()
'''


def render_worker(n_games, out_path):
    return (WORKER_TEMPLATE
            .replace("@@ROOT@@", ROOT.replace("\\", "\\\\"))
            .replace("@@LAYOUTS@@", LAYOUT_STORE.replace("\\", "\\\\"))
            .replace("@@OUT@@", out_path.replace("\\", "\\\\"))
            .replace("@@N@@", str(n_games)))


def ensure_layouts():
    """生成/加载同源布局（角落首点，与 _corner2000.json 口径一致）。"""
    if os.path.isfile(LAYOUT_STORE):
        try:
            d = json.load(io.open(LAYOUT_STORE, encoding="utf-8"))
            if len(d.get("layouts", [])) >= N_GAMES:
                print("布局缓存命中: %d 局" % len(d["layouts"][:N_GAMES]))
                return d["layouts"][:N_GAMES]
            print("缓存不足（%d < %d），重新生成"
                  % (len(d.get("layouts", [])), N_GAMES))
        except (OSError, ValueError):
            print("缓存损坏，重新生成")

    from sim.minesweeper_sim import Board
    print("生成 %d 局布局（seed_base=%d）..." % (N_GAMES, SEED_BASE), flush=True)
    layouts = []
    t0 = time.perf_counter()
    for i in range(N_GAMES):
        rng = random.Random(SEED_BASE + i)
        b = Board(W, H, A, rng)
        b.open(0, 0)
        layouts.append([[x + 1, y + 1] for (x, y) in b.mines])
        if (i + 1) % 2000 == 0:
            print("  %5d/%d  %.0fs" % (i + 1, N_GAMES,
                                       time.perf_counter() - t0), flush=True)
    os.makedirs(os.path.join(ROOT, "bench", "data"), exist_ok=True)
    with io.open(LAYOUT_STORE, "w", encoding="utf-8") as f:
        json.dump({"seed_base": SEED_BASE, "n": N_GAMES, "layouts": layouts},
                  f, ensure_ascii=False)
    print("布局已落盘: %s（%.1f MB）"
          % (LAYOUT_STORE, os.path.getsize(LAYOUT_STORE) / 1e6))
    return layouts


def run_arm(dstar, resume):
    out_path = os.path.join(OUT_DIR, "arm_%d.json" % dstar)
    if resume and os.path.isfile(out_path):
        try:
            prev = json.load(io.open(out_path, encoding="utf-8"))
            n_prev = len(prev.get("rows", []))
            # ⚠️ 判据必须用**文件里实际完成的局数**与本次目标 N_GAMES 比较，
            #    不能只看 `>= N_GAMES`：实测踩坑——用 `--games 200` 复跑时，
            #    文件里有 6000 条记录，被误判为"已完成 200 局"而整体跳过。
            #    正确语义：只要 n_prev 已达本次目标就跳过该臂；否则续跑。
            if n_prev >= N_GAMES:
                print("  [DSTAR=%d] 已完成 %d 局（本次目标 %d），跳过"
                      % (dstar, n_prev, N_GAMES))
                return prev
            print("  [DSTAR=%d] 续跑：已完成 %d / %d 局"
                  % (dstar, n_prev, N_GAMES))
        except (OSError, ValueError):
            pass

    tmp_script = os.path.join(OUT_DIR, "_worker_%d.py" % dstar)
    io.open(tmp_script, "w", encoding="utf-8").write(
        render_worker(N_GAMES, out_path))

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["MSW_DSTAR"] = str(dstar)
    env["QT_QPA_PLATFORM"] = "offscreen"

    t0 = time.perf_counter()
    print("\n[串行执行] DSTAR=%d，%d 局 ..." % (dstar, N_GAMES), flush=True)
    rc = subprocess.call([sys.executable, tmp_script], cwd=ROOT, env=env)
    el = time.perf_counter() - t0
    if rc != 0:
        print("  [FAIL] DSTAR=%d rc=%d（耗时 %.1f min，日志见 %s）"
              % (dstar, rc, el / 60, tmp_script))
        return None
    print("  DSTAR=%d 完成，耗时 %.1f min" % (dstar, el / 60))
    try:
        os.remove(tmp_script)
    except OSError:
        pass
    return json.load(io.open(out_path, encoding="utf-8"))


def binom_two_sided(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)

    def lc(n_, k_):
        return (math.lgamma(n_ + 1) - math.lgamma(k_ + 1)
                - math.lgamma(n_ - k_ + 1))

    tail = sum(math.exp(lc(n, i) - n * math.log(2)) for i in range(0, k + 1))
    return min(1.0, 2 * tail)


def wilson(k, n, z=1.959963984540054):
    p = k / n
    dd = 1 + z * z / n
    cc = p + z * z / (2 * n)
    hh = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (cc - hh) / dd, (cc + hh) / dd


def analyze(results, base_dstar):
    print("\n" + "=" * 82)
    print("1 万局判别结果")
    print("=" * 82)
    print("%-9s %-8s %-9s %-14s %-9s %-10s %-9s"
          % ("DSTAR", "wins", "胜率%", "95%CI", "opt_n", "opt_max", "峰值MB"))
    print("-" * 82)
    for r in results:
        rows = r["rows"]
        k = sum(1 for x in rows if x["won"])
        n = len(rows)
        lo, hi = wilson(k, n)
        print("%-9s %-8d %-9.2f [%5.2f, %5.2f] %-9d %-10s %-9s"
              % (r["dstar"], k, 100 * k / n, 100 * lo, 100 * hi,
                 r.get("opt_n") or 0, r.get("opt_max"),
                 "%.0f" % (r.get("commit_peak_mb") or 0)))

    base = next((r for r in results if r["dstar"] == str(base_dstar)), None)
    if base is None:
        print("\n[WARN] 缺少基线臂 DSTAR=%d，无法做配对检验" % base_dstar)
        return
    bw = {x["i"]: x["won"] for x in base["rows"]}
    print("\n" + "=" * 82)
    print("配对 McNemar 检验（基线 DSTAR=%d，共同局数 %d）"
          % (base_dstar, len(bw)))
    print("=" * 82)
    print("%-11s %-9s %-10s %-6s %-6s %-9s %s"
          % ("vs", "胜率%", "提升pp", "b", "c", "p 值", "判定"))
    print("-" * 82)
    for r in results:
        if r["dstar"] == str(base_dstar):
            continue
        nw = {x["i"]: x["won"] for x in r["rows"]}
        common = sorted(set(bw) & set(nw))
        b = sum(1 for i in common if bw[i] and not nw[i])
        c = sum(1 for i in common if not bw[i] and nw[i])
        p_val = binom_two_sided(b, c)
        k_new = sum(1 for i in common if nw[i])
        k_base = sum(1 for i in common if bw[i])
        m = len(common)
        delta_pp = 100 * (k_new - k_base) / m
        mark = "✅显著" if p_val < 0.05 else "❌不显著"
        print("%-11s %-9.2f %-10.2f %-6d %-6d %-9.4f %s"
              % ("DSTAR=" + r["dstar"], 100 * k_new / m, delta_pp,
                 b, c, p_val, mark))
        print("            同向不一致对 b+c = %d" % (b + c))

    print("\n" + "=" * 82)
    print("时间成本（确定性事实）")
    print("=" * 82)
    for r in results:
        print("  DSTAR=%-6s optimal %-7d  opt_max %-9sms  峰值 %.0fMB"
              % (r["dstar"], r.get("opt_n") or 0, r.get("opt_max"),
                 r.get("commit_peak_mb") or 0))


def main():
    global N_GAMES
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", type=int, nargs="+",
                    default=[400, 800, 1200, 2000])
    ap.add_argument("--games", type=int, default=N_GAMES)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    N_GAMES = args.games

    os.makedirs(OUT_DIR, exist_ok=True)
    ensure_layouts()

    print("\n" + "=" * 82)
    print("DSTAR 判别实验：%d 局 × %d 档（串行）" % (N_GAMES, len(args.tiers)))
    print("档位: %s" % args.tiers)
    print("=" * 82)

    results = []
    t_all = time.perf_counter()
    for dstar in args.tiers:
        r = run_arm(dstar, args.resume)
        if r:
            results.append(r)

    print("\n总墙钟: %.1f min" % ((time.perf_counter() - t_all) / 60))
    if results:
        analyze(results, args.tiers[0])


if __name__ == "__main__":
    main()
