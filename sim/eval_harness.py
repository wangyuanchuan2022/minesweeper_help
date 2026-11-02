# -*- coding: utf-8 -*-
"""算法极限胜率评估台：**完整复刻生产 play() 决策环**，只把屏幕换成模拟棋盘。

=================== 口径（决定这个数字意味着什么） ===================

复刻对象 = `utils/solver.py` 的 `Solver.play()` 每步流水线：

    complete_scan(读屏) → mine_clear1(确定性推理) → mine_clear3_1(1-1/1-2 推理)
    → 若 sum 无变化 → number5_1(概率/胜率决策，内部自己点 + 自己重扫)

也就是说：**免费推理每步能开多少格，全部照实计入**。这不是"只拿一个决策函数
打分"，而是整个解题主体。

被替换掉的只有"外部世界 I/O"：
  · `complete_scan` / `rescan_after_click`：不再抓屏 + 模板匹配，改为
    ① 先按序把求解器已发出的点击施加到模拟棋盘，② 再把棋盘真值中
    **人类可见的部分**回填成 cell_value（未开=9，已开=0..8）。
    ← 这一步等价于"识别 100% 正确"，且只有已开格的信息，没有雷位泄漏。
  · `pyautogui.click`：记录成待施加的点击，不真的点。
  · `time.sleep`、`data.json` 统计、胜负弹窗 `_locate`：空实现。
  · 决策逻辑（native C++ 与纯 Python 回退）**一行未改**。

因此本数字 = **算法上限胜率**：视觉 100% 正确、点击零延迟，其余全是真实算法。
算法改动若让这个数字上升，就是真的变强；若不变，说明只改了速度/工程。

=================== 为什么要 is_play=True ===================

`is_play` 在 win_rate 里控制分支定界剪枝：

    if i > 1 and clicks2p[clicks[i]] < max(res) and self.is_play:
        res.append(0); continue

把它设 False（早期 sim/algorithm_winrate.py 的做法）会关掉剪枝 ⇒ 决策结果与
生产不一致、且会爆炸（实测出现过单次 win_rate 40 秒、超 30 秒决策预算）。
本评估台恒用 True，与生产一致。

=================== 污染隔离 ===================

早期脚本每局都写生产 `data_time.json` / `state/time_budget.json`，既污染真实
对局统计又拖慢评估。这里默认重定向到 `state/eval_run/`，`--no-record` 时完全不写。

用法:
  python sim/eval_harness.py --games 200 --workers 12 --no-record
  python sim/eval_harness.py --games 6 --diag --no-record      # 逐局诊断
"""
import os

# 必须在 import PyQt5/cv2 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import argparse
import json
import math
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

UNOPENED = 9


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


# ---------------------------------------------------------------------------
# 副作用隔离（worker 初始化时执行一次）
# ---------------------------------------------------------------------------
def install_stubs(state_dir=None, no_record=False):
    os.chdir(ROOT)

    import pyautogui
    pyautogui.click = lambda *a, **k: None
    pyautogui.moveTo = lambda *a, **k: None

    import time as _time
    _time.sleep = lambda *a, **k: None

    import cv2
    try:
        cv2.setNumThreads(1)
    except Exception:  # noqa: BLE001
        pass

    import utils.vision as vision
    import utils.solver as solver_mod
    import utils.probability as prob

    # 视觉：不抓屏（真正读取由 SimDriver.complete_scan 接管）
    vision.BoardVisionMixin._grab_board = lambda self: None
    vision.BoardVisionMixin._grab_screen_bgr = staticmethod(lambda: None)
    vision.BoardVisionMixin._locate = lambda self, f, screen=None: (False, 0, 0)
    vision.BoardVisionMixin.compare_img = lambda self, img, no_10: 9

    # 统计落盘 / 胜负弹窗
    solver_mod.Solver._locate = lambda self, f, screen=None: (False, 0, 0)
    solver_mod.Solver._load_stats = lambda self: {}
    solver_mod.Solver._flush_stats = lambda self: None

    # 状态文件重定向（不污染生产）
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
        prob._PS_SAMPLES_PATH = os.path.join(state_dir, "data_time.json")
        prob._TB_STATE_PATH = os.path.join(state_dir, "time_budget.json")
    if no_record:
        prob._ps_record_ms = lambda size, ms: None
        prob._tb_save = lambda state: None


# ---------------------------------------------------------------------------
# 模拟驱动：把求解器的点击施加到棋盘，并把"人类可见盘面"喂回去
# ---------------------------------------------------------------------------
class SimDriver:
    """充当"屏幕 + 鼠标"。求解器发起的每个点击按序施加到模拟棋盘。

    ⚠️ 信息隔离：回填给求解器的 cell_value 只含**已开格的数字**与未开格，
    从不含雷位。真值只在 Board._mines 与本次驱动内部。
    """

    def __init__(self, board, solver, rng):
        self.board = board
        self.s = solver
        self.rng = rng
        self.pending = []          # [(x, y)] 待施加点击（0-based）
        self.pending_conf = []     # [conf] 与 pending 对齐（该点击所属决策的自评安全率）
        self.n_click = 0
        self.n_dead_click = 0      # 点了已开/标记格（无害重复点击）
        self.cur_conf = None       # 当前决策的自评安全率（供 on_click 关联）
        self.cur_branch = None
        self.in_decision = False   # 是否处于 number5_1 概率决策阶段（校准口径）
        # 事后校准样本：[(conf, is_mine)]，真值只进这里，绝不回填给求解器
        self.calib = []
        self.risky_opens = 0
        self.risky_fail = 0
        self.death_conf = None     # 致死那一击的自评安全率（判"死因"用）

    # --- 求解器调用：点一下（坐标是屏幕像素，需换算成格子） ---
    def on_click(self, px, py):
        """像素 → 格子。

        ⚠️ 生产代码里的点击坐标一律是 **1-based**（cell_value 含 1 格边框），
        即 click(bx + p[0]*cell_width)，p[0]∈[1,w]。因此反算必须减 1，
        否则整个棋盘偏移一格（本评估台起草时因此全线踩雷、步数=1）。
        """
        cw = self.s.cell_width
        x = int(round((px - self.s.bx) / cw)) - 1
        y = int(round((py - self.s.by) / cw)) - 1
        if not (0 <= x < self.board.w and 0 <= y < self.board.h):
            return
        self.pending.append((x, y))
        # 置信度在**入队时**就地捕获，但只在"概率决策阶段"才打标签。
        # 免费推理阶段（in_decision=False）的点击安全率恒为确定 1，
        # 让它们进校准会把真实安全率整体抬高，造出"概率系统性低估"的假象。
        if not self.in_decision:
            self.pending_conf.append(None)
            return
        hm = getattr(self.s, "_last_heatmap", None) or {}
        c = hm.get("best_conf")
        self.pending_conf.append(float(c) if c is not None else None)

    def flush_clicks(self):
        """结算待施加点击。

        ⚠️ 校准只统计"本次决策 subject 的点击"：判据是 `_last_heatmap`
        是否有对应 best_conf，且该格确实是未开的。免费推理的点击没有
        best_conf（或沿用旧值），会被 conf=None / 非本次决策识别出来。
        更稳的判据是调用方传入 phase：见 mark_phase()。
        """
        for (x, y), c in zip(self.pending, self.pending_conf):
            self.n_click += 1
            if self.board.cells[y][x] != UNOPENED:
                self.n_dead_click += 1
                continue
            if c is not None and c < 0.999:
                is_mine = (x, y) in self.board.mines
                self.calib.append((float(c), bool(is_mine)))
                self.risky_opens += 1
                if is_mine:
                    self.risky_fail += 1
            self.board.open(x, y)
            if self.board.dead:
                # 致死一击：记下当时的自评安全率（= 1 说明有确定性推理错误）
                self.death_conf = None if c is None else float(c)
                break
        self.pending.clear()
        self.pending_conf.clear()

    # --- 求解器调用：读屏（等价于"识别 100% 正确"） ---
    def complete_scan(self, cell_value, no_10=True):
        self.flush_clicks()
        if self.board.dead:
            raise GameOver("dead")     # 与生产一致：踩雷后棋盘冻结
        import numpy as np
        b = self.board
        out = cell_value
        for y in range(1, b.h + 1):
            for x in range(1, b.w + 1):
                if out[y, x] == UNOPENED:      # 只重认"内存认为未开"的格
                    v = b.cells[y - 1][x - 1]
                    out[y, x] = UNOPENED if v == UNOPENED else int(v)
        return out

    def rescan_after_click(self, cell_value):
        """生产里 mine_clear1/3_1 在推理后开格会重扫；这里等价于再读一次屏。"""
        return self.complete_scan(cell_value)


class GameOver(Exception):
    pass


# ---------------------------------------------------------------------------
# 单局：复刻 play() 的每步流水线
# ---------------------------------------------------------------------------
def play_one(seed, w, h, a, limit=80, wr_threshold=None, diag=False, first=None,
             max_steps=None):
    import numpy as np
    from sim.minesweeper_sim import Board, UNOPENED as U
    from utils.solver import Solver

    rng = random.Random(seed)
    # ⚠️ 复现性硬要求：求解器内部大量使用 **numpy 全局随机态**
    # （np.random.choice / np.random.shuffle，见 probability.py 的随机分支与
    # 最大概率平局随机选点）。同一个 worker 进程连续跑多局会累积状态 →
    # 同 seed 重跑结果不同（实测 40 局中 8 局不一致）。必须每局显式重置。
    np.random.seed(seed)
    board = Board(w, h, a, rng)
    x0, y0 = first if first else (w // 2, h // 2)
    board.open(x0, y0)
    board.view(set()).audit_no_leak()

    s = Solver()
    s.w, s.h, s.a = w, h, a
    s.limit = limit
    s.is_play = True                    # 与生产一致（启用剪枝）
    s.num = 1
    s.checked = {}
    s.pos_dict_list = []
    s.appended_pos = set()
    s.stats_data = {}
    s._stats_updates = 0
    s.till_now_winrate = 1.0
    s.count = 0
    # 屏幕坐标基准与格子宽度（click 换算用）；cell_width 只影响坐标换算，不影响决策
    s.bx, s.by, s.cell_width = 0.0, 0.0, 24
    # ⚠️ _time_budget=None ⇒ 生产代码里 wr_threshold 退化为常量
    # （probability.py:437 `6 + LIMIT_BONUS_WIN_RATE`），`_wr_current_threshold`
    # 根本不会被调用。若只 patch `_wr_current_threshold`，--wr-threshold **不生效**
    # （实测：阈值 8 与 999 两臂逐局完全一致，因为两臂实际都在用 8）。
    # 因此直接改阈值的真正来源：native.LIMIT_BONUS_WIN_RATE。
    s._time_budget = None
    if wr_threshold is not None:
        import utils.native as _native
        _native.LIMIT_BONUS_WIN_RATE = float(wr_threshold) - 6.0

    drv = SimDriver(board, s, rng)
    s.complete_scan = drv.complete_scan
    s.rescan_after_click = drv.rescan_after_click

    # 包一层 pyautogui.click（模块级已置空，这里按实例接管）
    import pyautogui
    pyautogui.click = drv.on_click

    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
    for i in range(1, w + 1):
        for j in range(1, h + 1):
            cell_value[j, i] = UNOPENED

    steps = 0
    max_steps = max_steps or w * h * 3
    records = []
    log_survival = 0.0
    n_guess = 0
    min_conf = 1.0
    errors = []
    stalls = 0
    t0 = time.perf_counter()
    decided = 0
    # 可信度校准用：(conf, 该格是否为雷)。**真值只用于事后评估**，
    # 保证永远不会出现在喂给求解器的 cell_value 里。
    calib = []
    last_clicks = []

    while not board.dead and not board.won and steps < max_steps:
        steps += 1
        try:
            before_clicks = drv.n_click
            drv.in_decision = False   # 免费推理阶段
            cell_value = s.complete_scan(cell_value, True)
            sum2 = np.sum(cell_value)
            try:
                cell_value = s.mine_clear1(cell_value, 0, 0)
            except ValueError as e:
                errors.append(f"mine_clear1: {e}")
                continue
            cell_value = s.mine_clear3_1(cell_value, 0, 0)
            sum3 = np.sum(cell_value)
            if board.dead or board.won:
                break
            if sum3 == sum2:
                _pre = cell_value.copy()
                decided += 1
                drv.in_decision = True    # 概率决策阶段（点击参与校准）
                cell_value = s.number5_1(cell_value)
                for _flag in (10, 11):
                    _m = (_pre == _flag) & (cell_value == UNOPENED)
                    cell_value[_m] = _flag
                drv.in_decision = False
                if board.dead or board.won:
                    break
            # ⚠️ 只在**本步真的做了决策**时记录置信度。纯推理步不产生新决策，
            # 但 _last_heatmap 会沿用上一次决策的值（有意的 UI 行为），
            # 若每步都乘一次 log(conf)，同一决策被重复计入几十次。
            if decided:
                hm = getattr(s, "_last_heatmap", {}) or {}
                conf = hm.get("best_conf")
                prob = hm.get("prob") or {}
                best = hm.get("best")
                n_unop = int(np.sum(cell_value == UNOPENED)) - int(np.sum(cell_value == 10))
                branch = "unknown"
                if prob:
                    branch = "deep" if len(prob) >= n_unop else "greedy"
                max_frontier = max(prob.values()) if prob else None
                chosen_frontier = (prob.get(tuple(best)) if (prob and best is not None)
                                   else None)
                if conf is not None:
                    conf = float(conf)
                    log_survival += math.log(max(conf, 1e-9))
                    min_conf = min(min_conf, conf)
                    if conf < 0.999:
                        n_guess += 1
                if diag:
                    records.append({"step": steps, "branch": branch,
                                    "confidence": conf, "n_prob": len(prob),
                                    "n_unopened": n_unop, "total": hm.get("total"),
                                    "max_frontier": max_frontier,
                                    "chosen_frontier": chosen_frontier,
                                    "best": best, "n_click": drv.n_click})
            if drv.n_click == 0 and not board.dead and not board.won:
                stalls += 1
                if stalls >= 3:
                    break
            else:
                stalls = 0
        except GameOver:
            break

    # 事后校准：把每个未标记的雷格当作"求解器本可避免的点击"，不，改为
    # 直接检查"求解器自认为安全率<1 的格"里真实含雷的比例 → 校准曲线。
    drv.flush_clicks()

    dt = time.perf_counter() - t0
    return {
        "seed": seed,
        "won": bool(board.won),
        "died": bool(board.dead),
        "steps": steps,
        "opened": board.opened_count,
        "target": w * h - a,
        "n_click": drv.n_click,
        "n_dead_click": drv.n_dead_click,
        "n_guess": n_guess,
        "log_survival": log_survival,
        "min_conf": min_conf,
        "n_err": len(errors),
        "errors": errors[:8],
        "stalls": stalls,
        "ms": dt * 1000.0,
        "calib": drv.calib,
        "risky_opens": drv.risky_opens,
        "risky_fail": drv.risky_fail,
        "death_conf": drv.death_conf,
        "records": records if diag else None,
    }


# ---------------------------------------------------------------------------
# 并行
# ---------------------------------------------------------------------------
_W = {}


def _worker_init(cfg):
    install_stubs(cfg.get("state_dir"), cfg.get("no_record", False))
    _W["cfg"] = cfg


def _worker_run(seed):
    cfg = _W["cfg"]
    try:
        return play_one(seed, cfg["w"], cfg["h"], cfg["a"], cfg["limit"],
                        cfg.get("wr_threshold"), cfg.get("diag", False),
                        cfg.get("first"))
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"seed": seed, "won": False, "died": False, "steps": 0,
                "error": f"{type(e).__name__}: {e}",
                "tb": traceback.format_exc()[-800:]}


# ---------------------------------------------------------------------------
# 并行跑批：subprocess + 文件重定向（沙箱禁用命名管道，不能用 Pool）
# ---------------------------------------------------------------------------
def run_chunk(seeds, cfg, out_path):
    """子进程入口：跑一段 seed，结果写 JSON 文件（不经管道）。"""
    install_stubs(cfg.get("state_dir"), cfg.get("no_record", False))
    _W["cfg"] = cfg
    rows = [_worker_run(s) for s in seeds]
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    os.replace(tmp, out_path)


def _run_parallel_subprocess(seeds, cfg, nw, total, state_dir):
    """把 seeds 切成 nw 块，各起一个子进程跑；结果各自写文件，父进程读回。"""
    import subprocess
    tmpdir = os.path.join(state_dir, "chunks")
    os.makedirs(tmpdir, exist_ok=True)
    for old in os.listdir(tmpdir):
        try:
            os.remove(os.path.join(tmpdir, old))
        except OSError:
            pass

    chunks = [[] for _ in range(nw)]
    for i, s in enumerate(seeds):
        chunks[i % nw].append(s)
    chunk_paths = [os.path.join(tmpdir, f"chunk{i}.json") for i in range(nw)]

    script = os.path.abspath(__file__)
    procs, logs = [], []
    for ci, chunk in enumerate(chunks):
        if not chunk:
            continue
        cfg_path = os.path.join(tmpdir, f"cfg{ci}.json")
        seeds_path = os.path.join(tmpdir, f"seeds{ci}.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        with open(seeds_path, "w", encoding="utf-8") as f:
            json.dump(chunk, f)
        logf = open(os.path.join(tmpdir, f"log{ci}.txt"), "w", encoding="utf-8")
        logs.append(logf)
        # stdio 全部走文件重定向（沙箱禁命名管道，不能给管道对象）
        procs.append((ci, subprocess.Popen(
            [sys.executable, script, "--chunk", cfg_path, seeds_path, chunk_paths[ci]],
            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, close_fds=True)))

    results, reported = [], set()
    while len(reported) < len(procs):
        for ci, p in procs:
            if ci in reported:
                continue
            if p.poll() is None:
                continue
            reported.add(ci)
            if p.returncode != 0:
                print(f"  ⚠️ 块 {ci} 退出码 {p.returncode}（见 {tmpdir}/log{ci}.txt）",
                      flush=True)
            try:
                with open(chunk_paths[ci], encoding="utf-8") as f:
                    rows = json.load(f)
                results.extend(rows)
                w = sum(1 for x in results if x.get("won"))
                print(f"  块 {ci} 完成（{len(rows)} 局）累计 "
                      f"{len(results)}/{total} 胜率 {w/max(1,len(results))*100:.2f}%",
                      flush=True)
            except (OSError, ValueError) as e:
                print(f"  ⚠️ 块 {ci} 结果读取失败：{e}", flush=True)
        time.sleep(0.5)

    for _, p in procs:
        p.wait()
    for f in logs:
        f.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--wr-threshold", type=float, default=None)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--first", type=int, nargs=2, default=None)
    ap.add_argument("--diag", action="store_true")
    ap.add_argument("--no-record", action="store_true", default=True)
    ap.add_argument("--record", dest="no_record", action="store_false")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk", nargs=3, default=None, metavar=("CFG", "SEEDS", "OUT"),
                    help="内部：子进程模式（跑指定 seeds，结果写 OUT）")
    args = ap.parse_args()

    if args.chunk:
        cfg_path, seeds_path, out_path = args.chunk
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        with open(seeds_path, encoding="utf-8") as f:
            seeds = json.load(f)
        run_chunk(seeds, cfg, out_path)
        return

    nw = args.workers or max(1, (os.cpu_count() or 4) - 1)
    nw = min(nw, args.games)
    state_dir = args.state_dir or os.path.join(ROOT, "state", "eval_run")

    print("=" * 76)
    print("算法极限胜率（复刻生产 play() 全流水线；视觉=100%正确，点击=零延迟）")
    print(f"棋盘 {args.w}x{args.h}，{args.a} 雷，首点 3x3 安全；limit={args.limit}；"
          f"wr_threshold={args.wr_threshold}")
    print(f"局数 {args.games}；并行 {nw}；状态 {state_dir}")
    print("=" * 76)

    cfg = {"w": args.w, "h": args.h, "a": args.a, "limit": args.limit,
           "wr_threshold": args.wr_threshold, "diag": args.diag,
           "state_dir": state_dir, "no_record": args.no_record,
           "first": args.first}
    seeds = [args.seed + i for i in range(args.games)]
    t0 = time.time()
    results = []
    if nw <= 1:
        install_stubs(state_dir, args.no_record)
        _W["cfg"] = cfg
        for i, sd in enumerate(seeds):
            results.append(_worker_run(sd))
            if (i + 1) % max(1, args.games // 10) == 0:
                w = sum(1 for x in results if x.get("won"))
                print(f"  ... {i+1}/{args.games}  胜率 {w/(i+1)*100:.2f}%", flush=True)
    else:
        # ⚠️ 沙箱禁用命名管道：multiprocessing.Pool 建队列时 CreateFile 报
        # WinError 5（本机实测）。改用 subprocess 分批 + **文件重定向**（非管道）
        # 的并行方式，每个子进程跑一段 seed 并写自己的 JSON，父进程读回聚合。
        results = _run_parallel_subprocess(seeds, cfg, nw, args.games, state_dir)
    dt = time.time() - t0
    results.sort(key=lambda r: r["seed"])

    errs = [r for r in results if r.get("error")]
    ok = [r for r in results if not r.get("error")]
    wins = sum(1 for r in ok if r["won"])
    n = len(ok)
    lo, hi = wilson_ci(wins, n)

    print("-" * 76)
    print(f"算法极限胜率 = {wins}/{n} = {(wins/n*100 if n else 0):.2f}%   "
          f"95%CI [{(lo*100):.2f}%, {(hi*100):.2f}%]")
    if n:
        died = sum(1 for r in ok if r["died"])
        print(f"踩雷 {died} / 未完成 {n - wins - died} / "
              f"求解异常局 {sum(1 for r in ok if r.get('n_err'))}")
        print(f"平均步数 {sum(r['steps'] for r in ok)/n:.1f}   "
              f"平均猜测决策 {sum(r['n_guess'] for r in ok)/n:.1f}   "
              f"平均点击 {sum(r['n_click'] for r in ok)/n:.1f}   "
              f"无效点击 {sum(r['n_dead_click'] for r in ok)/n:.1f}")
        ls = sum(r["log_survival"] for r in ok)
        print(f"理论存活率（Σlog 自评安全率）= {math.exp(ls/n)*100:.2f}%"
              f"   ← 决策若全对，胜率应≈此值")
        # ---- 校准：自评安全率 p 的格，真实是雷的比例应≈1-p ----
        calib = []
        for r in ok:
            calib.extend(r.get("calib") or [])
        if calib:
            buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8),
                       (0.8, 0.9), (0.9, 0.99), (0.99, 1.0)]
            print(f"可信度校准（{len(calib)} 次风险点击；自评安全率 vs 真实安全率）：")
            print("   区间            次数   自评均值   真实均值   偏差(pp)")
            for lo_, hi_ in buckets:
                sub = [(c, m) for (c, m) in calib if lo_ <= c < hi_]
                if not sub:
                    continue
                cmean = sum(c for c, _ in sub) / len(sub)
                tmean = sum(0.0 if m else 1.0 for _, m in sub) / len(sub)
                print(f"   [{lo_:.2f},{hi_:.2f})   {len(sub):5d}   "
                      f"{cmean:8.4f}   {tmean:8.4f}   "
                      f"{(tmean - cmean)*100:+7.2f}")
            cm = sum(c for c, _ in calib) / len(calib)
            tm = sum(0.0 if m else 1.0 for _, m in calib) / len(calib)
            print(f"   合计 {len(calib)} 次：自评 {cm:.4f} vs 真实 {tm:.4f}"
                  f"  偏差 {(tm-cm)*100:+.2f}pp"
                  f"   实际踩雷 {sum(1 for _, m in calib if m)}")
            if abs(tm - cm) > 0.02 and tm < cm:
                print("   ⚠️ 求解器系统性高估安全率 → 概率/聚合有偏，改它才有收益")
            elif abs(tm - cm) <= 0.02:
                print("   ✅ 校准良好：概率本身可信，胜率瓶颈在'无法再精确'的局面")
        print(f"单局均耗时 {sum(r['ms'] for r in ok)/n/1000:.2f}s")
        # ---- 死因分解：致死一击的自评安全率分布 ----
        dcs = [r.get("death_conf") for r in ok if r.get("died")]
        dcs_certain = sum(1 for c in dcs if c is not None and c >= 0.999)
        dcs_none = sum(1 for c in dcs if c is None)
        dcs_guess = [c for c in dcs if c is not None and c < 0.999]
        print(f"死因分解（{len(dcs)} 次踩雷）：")
        print(f"   确定性推理错误（自评=1 却踩雷）： {dcs_certain}"
              f"{'  ⚠️ 算法BUG' if dcs_certain else '  ✅ 无'}")
        print(f"   无置信度（随机分支/免费推理）：   {dcs_none}")
        if dcs_guess:
            dcs_guess.sort()
            print(f"   概率猜测失败：{len(dcs_guess)} 次；"
                  f"致死时自评安全率 中位数 {dcs_guess[len(dcs_guess)//2]:.4f}"
                  f"（均值 {sum(dcs_guess)/len(dcs_guess):.4f}）")
        print(f"推进量 {sum(r['opened'] for r in ok)/n:.0f}/"
              f"{args.w * args.h - args.a} 格")
    if errs:
        print(f"⚠️ {len(errs)} 局异常：")
        for r in errs[:5]:
            print(f"   seed={r['seed']} {r.get('error')}")
            if r.get("tb"):
                print(f"   {r['tb']}")
    print(f"总墙钟 {dt:.1f}s")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"config": {k: v for k, v in cfg.items() if k != "diag"},
                       "games": args.games, "workers": nw, "wins": wins, "n": n,
                       "rate": wins / n if n else 0.0, "ci": [lo, hi],
                       "wall_s": dt, "results": results}, f, ensure_ascii=False)
        print(f"结果已落盘 {args.out}")


if __name__ == "__main__":
    main()
