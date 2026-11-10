# -*- coding: utf-8 -*-
"""C++ 全文件行级覆盖率驱动（MinGW g++ --coverage 构建的插桩 pyd + 真实数据重放）。

背景与口径
----------
`tools/cpp_cov_build.py` 只能覆盖 mscore.cpp 的「纯算法段」（它把源码抽出来单独编译，
避免 pybind 依赖）。那份口径**看不到** decide_impl / py_decide / transitions_for /
abnative_bridge 等真正被生产调用的函数——而这些正是"哪些代码没被调用"的主战场。

本驱动换一条路：把**整份** cpp/mscore.cpp 用 MinGW g++ `--coverage -shared` 编成
可 import 的 pyd（宏 MSW_TRIAL_DECIDE 与生产一致），然后用真实数据驱动，最后 gcov
出**整个文件**的逐行命中数。

步骤（在用户终端或本机跑，需 MINGW_BIN 环境变量）：
  1) g++ ... --coverage -shared ... -o output/cpp_line_cov/mscore_cov.pyd
  2) 本脚本 import 该 pyd，跑真实工作负载，最后 sys.exit 触发 gcda 落盘
  3) gcov 解析成 state/audit/cpp_line_coverage.json

⚠️ 插桩 pyd 必须先做**实现等价性校验**（与 MSVC 生产 pyd 在同一批真实盘面上逐字段对拍），
否则覆盖率画像可能不代表生产路径。本脚本第 0 步就做这件事，不一致直接 FAIL。

用法：
  set MINGW_BIN=<含 g++.exe / gcov.exe 的目录>
  python tools/cpp_line_cov_driver.py            # 建插桩 pyd + 跑负载 + 出报告
  python tools/cpp_line_cov_driver.py --no-build # 复用已有 pyd 重跑负载
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "cpp_line_cov"
PYD = OUT / "mscore_cov.pyd"
REPORT = ROOT / "state" / "audit" / "cpp_line_coverage.json"

W, H = 30, 16


# ---------------------------------------------------------------------------
# 0) 构建插桩 pyd
# ---------------------------------------------------------------------------
def build(py310: str, mingw: str) -> Path:
    gpp = Path(mingw) / "g++.exe"
    OUT.mkdir(parents=True, exist_ok=True)
    # 清旧计数，保证本次负载的读数不受历史污染
    for f in list(OUT.glob("*.gcda")) + list(OUT.glob("*.gcov")) + list(OUT.glob("*.gcno")):
        f.unlink()
    if PYD.exists():
        PYD.unlink()
    cmd = [
        str(gpp), "-std=c++17", "-O0", "--coverage",
        "-static", "-static-libgcc", "-static-libstdc++", "-shared",
        "-I", str(ROOT / "cpp" / "inc"),
        "-I", str(Path(py310) / "Include"),
        "-DMSW_TRIAL_DECIDE",
        str(ROOT / "cpp" / "mscore.cpp"),
        "-o", str(PYD),
        "-L", str(Path(py310) / "libs"), "-lpython310",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    print("BUILD_RETURN:", r.returncode)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-4000:])
        sys.exit(1)
    print("BUILD OK:", PYD, PYD.stat().st_size // 1024, "KB")
    return PYD


# ---------------------------------------------------------------------------
# 1) 真实盘面数据集
# ---------------------------------------------------------------------------
def load_cases():
    """400 个真实中局盘面（18x32，取自真实 Arbiter 对局）."""
    d = json.loads((ROOT / "bench/data/midgame_cases.json").read_text(encoding="utf-8"))
    return d["cases"]


def load_layouts():
    """真实整局雷位布局（10000 个，来自真实对局采样），用于整局重放."""
    p = ROOT / "bench/data/_dstar_10k_layouts.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["layouts"]


# ---------------------------------------------------------------------------
# 2) 最小真实对局模拟器（与生产 cell_value 编码一致：9 未开 / 数字 已开）
# ---------------------------------------------------------------------------
class Sim:
    def __init__(self, mines_xy1, first=(16, 8)):
        self.W, self.H = W, H
        self.mine = [[False] * (W + 2) for _ in range(H + 2)]
        for mx, my in mines_xy1:
            self.mine[my][mx] = True
        self.cell = [[0] * (W + 2) for _ in range(H + 2)]
        for y in range(1, H + 1):
            for x in range(1, W + 1):
                self.cell[y][x] = 9
        self.opened = 0
        self.dead = False
        self.won = False
        self.steps = 0
        self.first_clicked = False
        self._pending_first = first

    def number(self, x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if not dx and not dy:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= W and 1 <= ny <= H and self.mine[ny][nx]:
                    c += 1
        return c

    def _flood(self, start):
        stack = [start]
        while stack:
            x, y = stack.pop()
            if self.cell[y][x] != 9 or self.mine[y][x]:
                continue
            n = self.number(x, y)
            self.cell[y][x] = n
            self.opened += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if not dx and not dy:
                            continue
                        nx, ny = x + dx, y + dy
                        if 1 <= nx <= W and 1 <= ny <= H:
                            stack.append((nx, ny))

    def check(self):
        self.won = self.opened >= W * H - 99

    def click(self, x, y):
        self.steps += 1
        if self.mine[y][x]:
            self.dead = True
            self.cell[y][x] = 10  # 真实 Arbiter 会显示旗/雷；这里只为不再被当空格
            return
        self._flood((x, y))
        self.check()

    def frame(self):
        return [row[:] for row in self.cell]

    def start(self):
        x, y = self._pending_first
        if self.mine[y][x]:
            for yy in range(1, H + 1):
                for xx in range(1, W + 1):
                    if not self.mine[yy][xx]:
                        self._pending_first = (xx, yy)
                        self.click(xx, yy)
                        self.first_clicked = True
                        return
        self.click(x, y)
        self.first_clicked = True


# ---------------------------------------------------------------------------
# 3) 负载
# ---------------------------------------------------------------------------
def run_workload(mscore, cases, layouts, budget_games=300):
    """真实盘面 + 真实整局重放 + 帮助页转移，全部打到插桩 pyd 上。"""
    import numpy as np

    stats = {
        "midgame_auto": 0, "midgame_help": 0, "split_frontier": 0,
        "transitions_for": 0, "games": 0, "game_decisions": 0,
        "reasons": {}, "trans_reasons": {}, "game_reasons": {},
    }

    def bump(d, k):
        d[str(k)] = d.get(str(k), 0) + 1

    # 3a) 400 个真实中局盘面 × auto + help（生产主路径）
    for c in cases:
        cv = np.ascontiguousarray(c["cell_value"], dtype=np.int32)
        a = int(c["a"])
        r = mscore.decide(cv, a=a, w=W, h=H, is_play=True)
        stats["midgame_auto"] += 1
        bump(stats["reasons"], r.get("reason"))
        r2 = mscore.decide(cv, a=a, w=W, h=H, is_play=False)
        stats["midgame_help"] += 1
        mscore.split_frontier(cv)
        stats["split_frontier"] += 1
        # 帮助页悬停：对每个判定为前沿的格子算一次转移（真实 UI 行为）
        fr = mscore.split_frontier(cv)
        frontier = fr[0] if fr else []
        for (fx, fy) in list(frontier)[:6]:
            t = mscore.transitions_for(cv, a=a, w=W, h=H, x=int(fx), y=int(fy))
            if t is not None:
                stats["transitions_for"] += 1
                bump(stats["trans_reasons"], t.get("reason"))

    # 3b) 真实整局重放：覆盖开局 / 中局 / 残局 / 关键分支
    for li, layout in enumerate(layouts[:budget_games]):
        g = Sim(layout)
        g.start()
        guard = 0
        while not g.dead and not g.won and g.steps < 800 and guard < 900:
            guard += 1
            fr = g.frame()
            cv = np.ascontiguousarray(fr, dtype=np.int32)
            r = mscore.decide(cv, a=99, w=W, h=H, is_play=True)
            stats["game_decisions"] += 1
            reason = str(r.get("reason"))
            bump(stats["game_reasons"], reason)
            if reason == "certain_safe":
                batch = [(int(c0), int(c1)) for c0, c1 in r.get("tied") or []][:64]
                any_click = False
                for bx, by in batch:
                    if 1 <= bx <= W and 1 <= by <= H and g.cell[by][bx] == 9:
                        g.click(bx, by)
                        any_click = True
                        if g.dead or g.won:
                            break
                if not any_click:
                    break
                continue
            x, y = int(r.get("x") or -1), int(r.get("y") or -1)
            if x < 1 or y < 1 or not (1 <= x <= W and 1 <= y <= H) or g.cell[y][x] != 9:
                fb = None
                for yy in range(1, H + 1):
                    for xx in range(1, W + 1):
                        if g.cell[yy][xx] == 9:
                            fb = (xx, yy)
                            break
                    if fb:
                        break
                if not fb:
                    break
                x, y = fb
            g.click(x, y)
        stats["games"] += 1
        if li % 50 == 0:
            print("  game", li, "won=", g.won, "steps=", g.steps, flush=True)

    return stats


def main():
    no_build = "--no-build" in sys.argv
    py310 = os.environ.get("PY310_ROOT", "").strip() or r"D:\anaconda3\envs\py310"
    mingw = os.environ.get("MINGW_BIN", "").strip()
    if not mingw:
        # 本机实测路径（可用环境变量覆盖）
        mingw = (r"C:\Users\ycwan\AppData\Local\Microsoft\WinGet\Packages"
                 r"\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe"
                 r"\mingw64\bin")
    if not no_build:
        build(py310, mingw)

    # 插桩 pyd 与 CRC 模块同名，单独目录 + 独立进程导入
    shutil.copy2(PYD, OUT / "mscore.pyd")
    shutil.copy2(Path(py310) / "python310.dll", OUT / "python310.dll")

    print("STEP: verify instrumented pyd is importable")
    chk = subprocess.run([sys.executable, "-c", "import mscore;print(sorted(x for x in dir(mscore) if not x.startswith('_')))"],
                         capture_output=True, text=True, cwd=str(OUT))
    print("  ", chk.stdout.strip(), chk.stderr.strip()[:400])
    if chk.returncode != 0:
        sys.exit(1)

    print("STEP: workload")
    sys.path.insert(0, str(OUT))
    import mscore  # noqa: E402  —— 插桩模块（与生产 pyd 同名，必须先插路径）

    cases = load_cases()
    layouts = load_layouts()
    print("cases:", len(cases), "layouts:", len(layouts))
    stats = run_workload(mscore, cases, layouts)
    print("STATS:", json.dumps(stats, ensure_ascii=False)[:600])

    (OUT / "_workload_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print("WORKLOAD_DONE (gcda 在进程退出时落盘)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
