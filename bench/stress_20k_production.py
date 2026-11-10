# -*- coding: utf-8 -*-
"""两万局极限胜率压测：当前 C++ 生产策略（三层 decide）的整局胜率评估。

=================== 与 eval_harness 的关系 ===================
完全复用 sim/eval_harness.py 的生产流水线复刻与并行底座（它已验证过的口径）：
生产 play() 全流水线 + 视觉 100% 正确 + 点击零延迟 + is_play=True 剪枝开启。
本脚本只加四件事：
  1. 两万局规模的**分批断点续跑**（每批 flush 增量 JSON，中断后 --resume 续跑，
     已完成的局不重算——单批崩溃不损失全局进度）。
  2. **内存四重保护**（用户硬要求「不要把内存顶爆」，本项目曾两次爆内存）：
     ① 每 worker 串行跑 N 局后**整体退出重建**（子进程回收一切 C++ 堆与
        Python 对象，防 memo/checked 缓存跨局累积——win_rate 的 memo 只写不删）；
     ② 每 worker 内存水位自检（psutil 可用时 RSS 超限即本批收尾退出，父进程
        重启续跑——OOM 发生前主动止损而非被动崩死）；
     ③ 全局并发 worker 数上限钳制（默认 4，防止 C++ 多实例同时深搜内存叠加；
        每个 decide 内部还有 mem_budget=1.5GiB 的 C++ 侧闸门兜底）；
     ④ chunk 结果文件即写即读即清（父进程只保留聚合统计，不持有全部 2 万局的
        逐局明细——明细按批 flush 到磁盘，聚合后释放）。
  3. **滚动胜率观测**：每批结束打印滚动胜率与 95% CI，中途可看趋势。
  4. 校准与死因分解照搬 eval_harness（含「确定性推理错误=算法BUG」判定）。

=================== 口径（勿改；改动即与历史读数不可比） ===================
**纯 C++ decide 循环**（用户拍板 2026-11-06）= 38% 历史基线口径（_corner2000.py）：
  · 角落首点（0-based [0,0] ≡ 生产 1-based (1,1)，同指左上角格；延迟布雷
    只排除首点本格——sim Board.generate 忠实实现）
  · 循环调 mscore.decide(frame)，certain_safe → 批量点 tied[:64]，
    其他 → 点 (x,y)；decide 默认参数（wr_threshold=8/node_cap=200万）
  · 纯 C++ 协议：不经生产 Python number5_1 编排链（该链的决策节奏损耗
    ~17% vs 37% 是另一个口径，已单独记录 project 记忆）
设 MSW_DSTAR 等环境变量可调 C++ 侧参数（但改了就不是"当前生产策略"口径）。

=================== 用法 ===================
  # 全量 20000 局（默认 4 worker，每 worker 每 25 局重建）
  python bench/stress_20k_production.py --games 20000

  # 断点续跑（中断后原命令重跑即可，自动跳过已完成局）
  python bench/stress_20k_production.py --games 20000 --resume

  # 更保守的内存配置（2 worker、每 10 局重建、RSS 上限 1.5GiB/worker）
  python bench/stress_20k_production.py --games 20000 --workers 2 \\
      --chunk-games 10 --rss-limit-mb 1536
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

# decide 循环协议不抓屏不点真屏，但保留 offscreen 等环境设置无害且防
# 任何隐式 Qt 依赖；线程数钳制防 C++ 多实例叠加占满核。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# GBK 控制台防 UnicodeEncodeError（⚠️/✅ 等字符在 cp936 下打印会崩）
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 纯 C++ decide 循环协议已在下方实现（_decide_loop_game）；不再 import
# eval_harness（那是 Python 编排链口径，决策节奏不同——见 protocol 注释）。
# ---------------------------------------------------------------------------

# 2026-11-06 生产修复版 pyd 的 SHA-256 前 8 位（重编译后 SHA 会变——那是正常
# 现象，确认新构建含 is_play 参数后更新此常量或用 --allow-sha-mismatch 放行；
# 此校验防的是"误用旧备份 pyd 导致 C++ decide 静默回退、两万局白跑"）。
PYD_SHA_EXPECTED = "ADA6DAFC"  # 2026-11-06 修复版（decide 含 is_play，E2E 已验证）

try:
    import psutil
except ImportError:
    psutil = None  # 内存自检降级为不可用（保护层①③仍生效）


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def pyd_sha8():
    """生产 pyd 的 SHA-256 前 8 位十六进制（大写），文件缺失返回 None。"""
    import hashlib
    p = os.path.join(ROOT, "utils", "mscore.cp310-win_amd64.pyd")
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:8].upper()


# ---------------------------------------------------------------------------
# 纯 C++ decide 循环协议（用户拍板 2026-11-06：压测改用此口径）
#
# 背景：eval_harness 走生产 Python number5_1 编排链，其决策节奏（每步先免费
# 推理、无进展才调 decide）导致 certain_safe 批量点击机会大幅减少——同 seed
# 对拍：纯 decide 循环 37%（certain_safe 1424 次/100局 + optimal 625 次），
# 编排链 ~17%（certain_safe 0 次）。引擎本身两边确定性一致（同盘面重放逐位
# 一致）。本压测口径 = 纯 decide 循环（= 38% 历史基线口径），测的是 C++ 策略
# 本身的胜率；编排损耗（17% vs 37%）是另一笔账，已单独记录。
#
# 协议（与 _corner2000.py 逐条对齐）：
#   · 棋盘 30x16 / 99 雷，Arbiter 延迟布雷（首点角落 (0,0) 0-based，只排除
#     首点本格——sim Board.generate 忠实实现）
#   · 循环调 decide(frame)；certain_safe → 批量点 tied[:64]；其他 → 点 (x,y)
#   · decide 返回无效坐标时兜底点第一个未开格（与基线脚本一致）
# ---------------------------------------------------------------------------
def _decide_loop_game(seed, w, h, a):
    """一局纯 decide 循环。返回 dict（与 eval_harness 局记录字段兼容）。"""
    import random as _random
    import numpy as _np
    from sim.minesweeper_sim import Board
    from utils import native as _native

    W2 = w + 2
    rng = _random.Random(seed)
    b = Board(w, h, a, rng)
    b.open(0, 0)  # 角落首点（0-based；延迟布雷只排除此格）

    mine = [[False] * W2 for _ in range(h + 2)]
    for (mx, my) in b.mines:
        mine[my + 1][mx + 1] = True
    cell = [[0] * W2 for _ in range(h + 2)]
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            cell[y][x] = 9

    state = {"opened": 1 if b.opened_count else 0, "dead": False, "steps": 0}

    def number(x, y):
        c = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 1 <= nx <= w and 1 <= ny <= h and mine[ny][nx]:
                    c += 1
        return c

    def flood(start):
        stack = [start]
        while stack:
            x, y = stack.pop()
            if cell[y][x] != 9 or mine[y][x]:
                continue
            n = number(x, y)
            cell[y][x] = n
            state["opened"] += 1
            if n == 0:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        stack.append((x + dx, y + dy))

    # 首点展开
    flood((1, 1))

    def click(x, y):
        state["steps"] += 1
        if mine[y][x]:
            state["dead"] = True
            return
        flood((x, y))

    def is_won():
        return state["opened"] >= w * h - a

    def frame():
        return [row[:] for row in cell]

    t0 = time.perf_counter()
    while not state["dead"] and not is_won() and state["steps"] < 3000:
        cv = _np.array(frame(), dtype=_np.int32)
        r = _native.mscore.decide(cv, a=a, w=w, h=h)
        reason = str(r["reason"])
        if reason == "certain_safe":
            batch = [(int(c[0]), int(c[1])) for c in (r["tied"] or [])][:64]
            clicked = False
            for (bx, by) in batch:
                if cell[by][bx] == 9:
                    click(bx, by)
                    clicked = True
                    if state["dead"]:
                        break
            if not clicked:
                break
            continue
        x, y = int(r["x"]), int(r["y"])
        if x >= 1 and y >= 1:
            click(x, y)
        else:
            # 兜底：点第一个未开格（与 _corner2000.py 基线一致）
            fb = None
            for yy in range(1, h + 1):
                for xx in range(1, w + 1):
                    if cell[yy][xx] == 9:
                        fb = (xx, yy)
                        break
                if fb:
                    break
            if not fb:
                break
            click(fb[0], fb[1])
    ms = (time.perf_counter() - t0) * 1000.0
    return {"seed": seed, "won": bool(is_won() and not state["dead"]),
            "died": bool(state["dead"]), "steps": state["steps"],
            "ms": ms, "error": None, "n_guess": None, "log_survival": 0.0,
            "min_conf": None, "records": None}


# ---------------------------------------------------------------------------
# Worker 侧：带内存自检与重建周期的 chunk 跑批
# ---------------------------------------------------------------------------
def run_chunk_stress(seeds, cfg, out_path):
    """子进程入口：跑一段 seeds，带内存保护，结果原子写 out_path。

    与 eval_harness.run_chunk 的差异：
      · 每 chunk_games 局做一次进程级"体检"（RSS 超限即提前收尾，
        已跑完的局照常落盘——损失的是本 chunk 的尾部，不是全部）；
      · 明细瘦身：不驻留大字段，控制 chunk 结果文件体积。
    """
    os.chdir(ROOT)
    # decide 循环协议：不需要视觉/Qt stub（不抓屏不点击），但要确保
    # native 加载好；CUDA/线程环境变量已在模块顶部设置。
    chunk_games = max(1, int(cfg.get("chunk_games", 25)))
    # ⚠️ commit 上限保留小数（38.3 这类精细阈值也要生效）——int() 会把 <1 的
    # 差异抹掉，阳性对照实验因此假阴性过一轮。
    rss_limit = float(cfg.get("rss_limit_mb", 0))  # 语义 = commit 上限（MB）；0=不自检
    proc = psutil.Process() if (psutil is not None and rss_limit > 0) else None
    jsonl_path = cfg.get("jsonl_path")
    jsonl_f = open(jsonl_path, "a", encoding="utf-8") if jsonl_path else None

    sha8 = pyd_sha8()
    rows = []
    t0 = time.time()
    import gc as _gc
    _commit_prev = None  # 上一局结束时的 commit（局级增量守卫用）
    for i, seed in enumerate(seeds):
        try:
            r = _decide_loop_game(seed, cfg["w"], cfg["h"], cfg["a"])
        except Exception as e:  # noqa: BLE001 —— 单局异常不拖垮整个 chunk
            import traceback
            r = {"seed": seed, "won": False, "died": False, "steps": 0,
                 "ms": 0.0, "error": f"{type(e).__name__}: {e}",
                 "tb": traceback.format_exc()[-500:]}
        # ⚠️ 每局强制 GC（内存根修，实测 2026-11-06）：
        # decide 内部 part_solve 大组会产生 1.9GB 级解矩阵 numpy 数组
        #（实测 1590 万解 × 30 格 × 4B），循环内残引用让大块 commit 跨局
        # 滞留（实测 worker commit 累积到 21GB 而 RSS 只显示 60-80MB——
        # 换页掩盖）。gc.collect() 强制归还。实测 gc 后 commit 2607→784MB。
        _gc.collect()
        # 内存自检指标 = Private Bytes（= commit，任务管理器"提交大小"），
        # 不是 RSS！decide 深搜局的大块内存被 Windows 换页后 RSS 显得只有
        # 60-80MB，但 commit 才是系统级"内存不足"的判据。
        commit_now = (proc.memory_info().private / (1024 * 1024)
                      if proc is not None else None)
        rows.append(r)
        # ---- 逐局 JSONL 即时 flush（崩溃零丢失；~1KB/局，全量 ~20MB）----
        if jsonl_f is not None:
            jsonl_f.write(json.dumps({
                "seed": seed, "won": r.get("won"), "died": r.get("died"),
                "steps": r.get("steps"), "ms": r.get("ms"),
                "error": r.get("error"), "rss_mb": commit_now,
                "pyd_sha8": sha8, "worker_pid": os.getpid(),
            }, ensure_ascii=False) + "\n")
            jsonl_f.flush()
            os.fsync(jsonl_f.fileno())  # 硬盘级落盘（flush 只到 OS 缓冲）
        # ---- 内存自检（单守卫：commit 绝对上限，每局查）----
        # （局级增量守卫已删——gc.collect() 才是根修，守卫堆叠只会误杀深搜局。）
        if proc is not None and commit_now is not None and commit_now > rss_limit:
            print(f"  [worker] commit {commit_now:.0f}MB > 限 {rss_limit}MB，"
                  f"第 {i + 1} 局后收尾重建（已完成 {len(rows)} 局）",
                  flush=True)
            break
        if (i + 1) % 50 == 0:
            w = sum(1 for x in rows if x.get("won"))
            el = time.time() - t0
            print(f"  [worker] {i + 1}/{len(seeds)}  胜率 "
                  f"{w / (i + 1) * 100:.2f}%  {el / (i + 1):.2f}s/局",
                  flush=True)
    if jsonl_f is not None:
        jsonl_f.close()

    # rows 以汇总形式落盘（每 chunk 一份，聚合时合并）
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "calib_all": []}, f, ensure_ascii=False)
    os.replace(tmp, out_path)


# ---------------------------------------------------------------------------
# 父进程侧：分批调度 + 断点续跑 + 滚动聚合
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=20000)
    ap.add_argument("--workers", type=int, default=2,
                    help="并发子进程数（默认 2）。⚠️ 单次 decide 内部深搜"
                         "（win_rate memo 200 万节点 + tie-break world 实例化）"
                         "可瞬时冲到 ~1GB/worker——实测 374 局出现 1 次（约 "
                         "0.3%），4 worker 同时撞上 = 4GB+ 叠加。2 worker 时"
                         "同时撞概率极低且单次峰值也减半。")
    ap.add_argument("--batch", type=int, default=2000,
                    help="滚动观测/批 JSON 落盘粒度（断点主恢复源=逐局 JSONL）。"
                         "1M 局规模建议 2000；进度指针每批原子落盘")
    ap.add_argument("--chunk-games", type=int, default=25,
                    help="每 worker 跑 N 局后做 RSS 自检")
    ap.add_argument("--restart-games", type=int, default=100,
                    help="worker 硬重启周期：每跑 N 局进程退出重建（与 RSS 无关"
                         "的内存硬隔离，防 C++ 层循环引用/未释放堆的慢积累；"
                         "父进程立即补位新进程，进度不丢）")
    ap.add_argument("--rss-limit-mb", type=int, default=1536,
                    help="单 worker Private Bytes（commit）上限（MB）；超限该 "
                         "worker 主动收尾重建。⚠️ 监控指标是 commit 不是 RSS："
                         "decide 深搜大块内存被换页后 RSS 假低（实测 worker "
                         "RSS 60-80MB 但 commit 累积到 21GB），commit 才是"
                         "系统级「内存不足」的判据。正常稳态 commit 40-80MB；"
                         "单次 decide 深搜尖峰 1-3GB（换页后 RSS 看不见）。"
                         "0=关闭自检（psutil 缺失时自动关闭）")
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20261106)
    ap.add_argument("--resume", action="store_true",
                    help="断点续跑：跳过已完成 seed")
    ap.add_argument("--dry-run", action="store_true",
                    help="口径/内存/速度快速验证：等价 --games 100 --workers 2")
    ap.add_argument("--allow-sha-mismatch", action="store_true",
                    help="跳过 pyd SHA 与 py310 环境硬校验（不推荐，仅确认"
                         "重编译版本后使用）")
    ap.add_argument("--out", default=None, help="最终聚合 JSON 路径")
    args = ap.parse_args()
    if args.dry_run:
        args.games = min(args.games, 100)
        args.workers = min(args.workers, 2)
        args.batch = min(args.batch, 100)

    # ---- 口径与环境硬校验（启动即失败，防止无效读数跑 1 小时）----
    # 口径（勿改，改动即与历史读数不可比）：**纯 C++ decide 循环**（用户拍板
    # 2026-11-06，= 38% 历史基线口径 _corner2000.py）｜角落首点（0-based
    # [0,0] ≡ 生产 1-based (1,1)，同指左上角格）｜decide 不传 wr_threshold/
    # node_cap（pybind 默认 8 / 200 万，与基线脚本一致）｜is_play 默认 True。
    # 注：编排链损耗（生产 number5_1 ~17% vs 本口径 37%）是另一个口径，已单独
    # 记录在 project 记忆，不在本压测范围。
    #
    # ① PyQt5 可 import 校验（环境错 = 假数据源，直接终止）：
    try:
        import PyQt5  # noqa: F401
        from PyQt5 import QtCore  # noqa: F401  触发 Qt 初始化依赖链
    except Exception as e:  # noqa: BLE001 —— ImportError/OSError/初始化失败一网打尽
        print(f"[FATAL] PyQt5 导入/初始化失败：{type(e).__name__}: {e}")
        print("        压测必须在生产 py310 环境跑："
              "<PY310_PYTHON>")
        sys.exit(1)
    # ② 生产 pyd 真校验：从 native 实际加载的模块定位文件（而非猜路径），
    #    防「磁盘上换了 pyd、进程里加载的是另一份」的口径漂移。
    from utils import native as _native
    if _native.mscore is None:
        print("[FATAL] native.mscore 未加载（pyd 缺失或解释器非 cp310）——"
              "压测口径要求 C++ 生产策略，拒绝纯 Python 回退跑两万局。")
        sys.exit(1)
    import hashlib
    try:
        _mscore_file = _native.mscore.__file__
        _sha_full = hashlib.sha256(open(_mscore_file, "rb").read()).hexdigest()
        h8 = _sha_full[:8].upper()
    except (OSError, AttributeError) as e:
        print(f"[FATAL] 无法计算已加载 pyd 的 SHA：{e}")
        sys.exit(1)
    print(f"已加载 pyd: {_mscore_file}")
    print(f"pyd SHA-256 前 8 位 = {h8}")
    if h8 != PYD_SHA_EXPECTED:
        print(f"⚠️ 警告：pyd SHA 与记录的生产修复版 {PYD_SHA_EXPECTED} 不一致。")
        print("   若刚重编译属正常（SHA 随构建变化），确认含 is_play 参数后加"
              " --allow-sha-mismatch；若误用旧备份（.bak_prehelp 等）先恢复。")
        if not args.allow_sha_mismatch:
            print(f"[FATAL] pyd SHA 不匹配（{h8} != {PYD_SHA_EXPECTED}）")
            sys.exit(1)
    if "py310" not in sys.executable.replace("\\", "/").lower():
        print(f"⚠️ 警告：当前解释器 {sys.executable} 非 py310——生产 pyd 绑定 "
              "cp310，不匹配时 native 静默回退纯 Python，胜率口径失效。")
        if not args.allow_sha_mismatch:
            print("[FATAL] Python 环境不是 py310（用 "
                  "<PY310_PYTHON> 运行）")
            sys.exit(1)

    state_dir = os.path.join(ROOT, "state", "stress20k")
    prog_dir = os.path.join(state_dir, "progress")
    os.makedirs(prog_dir, exist_ok=True)
    jsonl_path = os.path.join(state_dir, f"runs_{args.seed}.jsonl")

    # ---- 断点续跑：盘点已完成 seed（批 JSON + 逐局 JSONL 双源）----
    done = {}
    if args.resume:
        # 主恢复源：逐局 JSONL（崩溃零丢失；每行自带 pyd_sha8 可审计口径）
        if os.path.exists(jsonl_path):
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if not r.get("error"):
                            done[r["seed"]] = bool(r.get("won"))
                    except (ValueError, KeyError):
                        continue
        # 兜底源：进度指针（原子替换的 done_seeds.json，最快）与批 JSON
        ptr_path = os.path.join(state_dir, "done_seeds.json")
        if os.path.exists(ptr_path):
            try:
                with open(ptr_path, encoding="utf-8") as f:
                    for k, v in json.load(f).get("seeds", {}).items():
                        done.setdefault(int(k), bool(v))
            except (OSError, ValueError):
                pass
        for fn in os.listdir(prog_dir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(prog_dir, fn), encoding="utf-8") as f:
                    batch = json.load(f)
                for r in batch.get("rows", []):
                    if not r.get("error"):
                        done.setdefault(r["seed"], bool(r.get("won")))
            except (OSError, ValueError):
                pass

    all_seeds = [args.seed + i for i in range(args.games)]
    # ⚠️ done 口径收窄到本任务 seed 区间（2026-11-06 实测 bug：done_seeds.json
    # 是全局共享指针，其他测试段的历史会被计入「已完」→「累计 8400/200」虚高）。
    _lo, _hi = args.seed, args.seed + args.games
    done = {s: w for s, w in done.items() if _lo <= s < _hi}
    todo = [s for s in all_seeds if s not in done]
    print("=" * 76)
    print("两万局极限压测：纯 C++ decide 循环口径（= 38% 历史基线口径）")
    print(f"口径：角落首点(0,0)｜decide 默认参数(wr=8/node_cap=200万/is_play=T)"
          f"｜certain_safe 批量")
    print(f"目标 {args.games} 局；已完 {len(done)}；待跑 {len(todo)}；"
          f"并发 {args.workers}；硬重启周期 {args.restart_games} 局/worker；"
          f"commit 自检 {args.rss_limit_mb or '关'}MB；逐局 JSONL {jsonl_path}")
    print("=" * 76, flush=True)

    cfg_base = {
        "w": args.w, "h": args.h, "a": args.a, "limit": args.limit,
        "chunk_games": args.chunk_games,
        "rss_limit_mb": args.rss_limit_mb if psutil is not None else 0,
        "jsonl_path": jsonl_path,   # 逐局即时 flush（崩溃零丢失）
    }
    script = os.path.abspath(__file__)

    # ---- 工作队列派发：每个 worker 至多领 restart_games 局，跑完即退出，
    #      父进程立即补位新进程——内存硬隔离与 RSS 无关，C++ 层慢积累被
    #      进程边界彻底封死。---
    t_all = time.time()
    queue = list(todo)
    inflight = {}
    batch_rows, batch_calib = [], []
    batch_idx = 0
    seq = 0
    tmpdir = os.path.join(state_dir, "tmp")
    os.makedirs(tmpdir, exist_ok=True)

    def _spawn():
        """领一批 seed（至多 restart_games）起一个 worker。

        ⚠️ seed 只从 queue 领取、queue 在单进程父循环内独占维护，且只派发
        不在 done 里的 seed——每个 seed 全程至多被一个 worker 跑一次
        （硬重启后新 worker 领的是 queue 里的新 seed，不会重复跑）。
        """
        nonlocal seq
        while queue and queue[0] in done:
            queue.pop(0)  # 双保险：跳过已完成 seed（正常时 queue 已过滤）
        if not queue:
            return
        chunk = queue[:args.restart_games]
        del queue[:len(chunk)]
        si = seq  # 本 worker 的文件序号（清理时按它精确删自己那组）
        cpath = os.path.join(tmpdir, f"cfg_{si}.json")
        spath = os.path.join(tmpdir, f"seeds_{si}.json")
        opath = os.path.join(tmpdir, f"out_{si}.json")
        cfg = dict(cfg_base)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with open(spath, "w", encoding="utf-8") as f:
            json.dump(chunk, f)
        logf = open(os.path.join(tmpdir, f"log_{si}.txt"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, script, "--worker", cpath, spath, opath],
            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, close_fds=True)
        seq += 1
        inflight[opath] = {"op": proc, "logf": logf, "n_expect": len(chunk),
                           "si": si}

    for _ in range(min(args.workers, len(queue))):
        _spawn()

    # 实时进度心跳（用户指令 2026-11-06：命令要汇报进度）：
    # 批级汇总每 batch 局才打一条，50 万局要等几分钟才见一次输出——终端
    # 静默易被误认为卡死。心跳每 60s 打一行「已完成/总数 + 实时胜率 + 在飞
    # worker 数」，依据 = 逐局 jsonl 行数（worker 每局 fsync，读行数零延迟）。
    _last_hb = time.time()

    def _hb_count():
        """当前 jsonl 行数（≈ 已完成局数；含少量历史坏行，仅用于进度展示）。"""
        try:
            with open(jsonl_path, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    while inflight:
        finished = [p for p, m in inflight.items() if m["op"].poll() is not None]
        if not finished:
            if time.time() - _last_hb >= 60.0:
                _last_hb = time.time()
                _n = _hb_count()
                print(f"[心跳] jsonl 累计 {_n} 局 ｜ 在飞 worker {len(inflight)} ｜ "
                      f"队列剩余 {len(queue)}（详细滚动胜率见批级输出；worker "
                      f"明细日志 state/stress20k/tmp/log_*.txt）", flush=True)
            time.sleep(0.5)
            continue
        for opath in finished:
            m = inflight.pop(opath)
            m["op"].wait()
            m["logf"].close()
            rc = m["op"].returncode
            try:
                with open(opath, encoding="utf-8") as f:
                    payload = json.load(f)
                rows = payload.get("rows", [])
                batch_rows.extend(rows)
                batch_calib.extend(payload.get("calib_all", []))
                n_bad = sum(1 for r in rows if r.get("error"))
                if rc != 0 or n_bad:
                    print(f"  ⚠️ worker rc={rc} 异常局 {n_bad}/"
                          f"{m['n_expect']}（日志 state/stress20k/tmp/）",
                          flush=True)
                # 一致性对账：chunk 聚合行数必须等于领的 seed 数（防漏/防重）
                if len(rows) != m["n_expect"]:
                    print(f"  ⚠️ 对账失败：chunk 聚合 {len(rows)} != 领取 "
                          f"{m['n_expect']}（缺失的 seed 由 --resume 补跑）",
                          flush=True)
            except (OSError, ValueError) as e:
                # 聚合丢失 ≠ 数据丢失：jsonl 逐局落盘在，resume 可恢复
                print(f"  ⚠️ worker 结果读取失败（chunk 聚合丢失，jsonl 与 "
                      f"--resume 可恢复）：{e}", flush=True)
            finally:
                # ---- 清理时机：本 worker 收工后立即删它自己那组文件 ----
                # ⚠️ 绝不能在批边界按 range(seq) 清理（会删到在飞 worker 的
                # out 文件——两轮实测同一坑）。收工点删除 = 文件生命周期与
                # worker 生命周期严格绑定，不存在误删窗口。
                for pre in (f"cfg_{m['si']}.json", f"seeds_{m['si']}.json",
                            f"out_{m['si']}.json", f"log_{m['si']}.txt"):
                    fp = os.path.join(tmpdir, pre)
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            # 进程补位：队列还有货就立刻起新 worker（硬重启语义）
            while queue and len(inflight) < args.workers:
                _spawn()

        # 批级滚动观测 + 批 JSON 原子落盘（jsonl 为主恢复源，批 JSON 兜底）
        # ⚠️ done dict 只做 bool 摘要，绝不清空——父进程内存 O(局数) 恒定
        #（100 万局 × ~100B ≈ 100MB，可控）；批 JSON 明细按批落盘不驻留。
        # 滚动观测频率随规模自适应：每批打印一次，1M 局时批=2000 局 → 500 次
        # 打印，节奏合适。
        if len(batch_rows) >= args.batch or (not queue and not inflight):
            bpath = os.path.join(prog_dir, f"b{batch_idx:05d}.json")
            tmp = bpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"batch": batch_idx, "rows": batch_rows,
                           "calib": batch_calib}, f, ensure_ascii=False)
            os.replace(tmp, bpath)
            ok = [r for r in batch_rows if not r.get("error")]
            for r in ok:
                done[r["seed"]] = bool(r.get("won"))
            # ---- 进度指针原子落盘（每批一次临时文件+os.replace；逐局级
            #      恢复已由 worker jsonl+fsync 保证，此处只加速 resume 扫描）----
            ptr_path = os.path.join(state_dir, "done_seeds.json")
            ptr_tmp = ptr_path + ".tmp"
            with open(ptr_tmp, "w", encoding="utf-8") as f:
                json.dump({"seeds": {str(k): v for k, v in done.items()}},
                          f, ensure_ascii=False)
            os.replace(ptr_tmp, ptr_path)
            n_done = len(done)
            wins = sum(1 for r in done.values() if r)
            lo, hi = wilson_ci(wins, n_done)
            el = time.time() - t_all
            eta = el / max(1, n_done) * (args.games - n_done) if n_done else 0
            print(f"[批 {batch_idx}] +{len(batch_rows)} 局（异常 "
                  f"{len(batch_rows) - len(ok)}）｜ 累计 {n_done}/{args.games}  "
                  f"胜率 {wins / max(1, n_done) * 100:.2f}%  "
                  f"95%CI [{lo * 100:.2f}, {hi * 100:.2f}]%  "
                  f"已跑 {el / 60:.1f}min  ETA {eta / 3600:.2f}h", flush=True)
            batch_rows, batch_calib = [], []
            batch_idx += 1
            # 注意：临时文件清理已移到 worker 收工处理器（finally 块）——
            # 按 worker 自己的 si 精确删，与生命周期绑定，批边界不做任何
            # 清理（历史两轮都在这里误删在飞 worker 的文件）。

    # ---- 终局聚合（done 值为 bool 摘要；明细在 jsonl 与批 JSON 里）----
    wins = sum(1 for r in done.values() if r)
    n = len(done)
    lo, hi = wilson_ci(wins, n)
    print("=" * 76)
    print(f"最终：{wins}/{n} = {wins / max(1, n) * 100:.2f}%   "
          f"95%CI [{lo * 100:.2f}%, {hi * 100:.2f}%]")
    print(f"总墙钟 {(time.time() - t_all) / 60:.1f}min；逐局 JSONL {jsonl_path}")

    if args.out:
        summary = {
            "config": {"games": args.games, "workers": args.workers,
                       "batch": args.batch, "chunk_games": args.chunk_games,
                       "restart_games": args.restart_games,
                       "rss_limit_mb": args.rss_limit_mb,
                       "w": args.w, "h": args.h, "a": args.a,
                       "limit": args.limit, "seed": args.seed,
                       "pyd_sha8": h8,
                       "protocol": "pure_cpp_decide_loop",
                       "first_click": "corner(0,0,0-based)"},
            "wins": wins, "n": n, "rate": wins / max(1, n), "ci": [lo, hi],
            "wall_s": time.time() - t_all,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False)
        print(f"摘要已落盘 {args.out}")


if __name__ == "__main__":
    # ⚠️ Windows spawn 三道防线（缺一 = 子进程重入主模块递归拉起进程树，
    # 内存瞬间被吃满——「一启动 90%+」的根因）：
    # ① multiprocessing.freeze_support()：pyinstaller 冻结环境 / 某些
    #    spawn 通道下必需；非冻结环境是无害 no-op。
    # ② 子进程分支执行完必须 sys.exit()——否则 fall through 到 main()，
    #    worker 里再起 worker（指数级进程树）。
    # ③ 父进程侧只允许经 subprocess.Popen 起 worker（当前实现），绝不改用
    #    multiprocessing.Pool/Process——沙箱禁命名管道（实证 WinError 5），
    #    且 Pool 的 spawn 重入面更大。
    import multiprocessing
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        # 子进程模式：--worker CFG SEEDS OUT
        cfg_path, seeds_path, out_path = sys.argv[2:5]
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        with open(seeds_path, encoding="utf-8") as f:
            seeds = json.load(f)
        run_chunk_stress(seeds, cfg, out_path)
        sys.exit(0)  # ⚠️ 必须：防 fall through 到 main() 递归起进程树
    main()
