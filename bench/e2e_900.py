# -*- coding: utf-8 -*-
"""真实端到端 900 局测量跑者：**完整 GUI + 真实 Arbiter 窗口 + 真实截图/点击**。

========================= 与既有压测的区别 =========================
本仓已有两类测量，都不覆盖"真实端到端"：
  · `sim/eval_harness.py` / `bench/stress_20k_production.py`：模拟棋盘 + 假截图，
    测的是**算法上限**，不含视觉识别、不含 UI 渲染、不含真实点击延迟。
  · `bench/verify_*.py`：离线对拍，测单点正确性。

本脚本跑的是**生产真实链路**：
    真实 Arbiter 进程 → 真实屏幕截图 → 模板识别 → C++ decide → 真实鼠标点击
    → GUI（main.MyMainWindow）实时渲染热力图/候选/决策卡

========================= 它如何驱动 GUI =========================
不起 `python main.py`（那需要人手点按钮），而是：
  1. `QApplication` + `main.MyMainWindow()` —— 与生产**同一个类、同一套信号接线**；
  2. 直接调 `win.auto_play_func` 等价的内部流程，但绕过 QInputDialog
     （用 `--games` 指定局数），走 `auto_play_thread` 的真实 play() 主循环；
  3. Qt 事件循环在**主线程**跑（`app.processEvents()`），后台 Solver QThread
     与生产完全一致 —— 所以 heatmap_signal → update_heatmap 的真实渲染路径
     全部被走到并计时。
  4. 每局结束记录：胜负、墙钟、决策数、内存；每 N 局落盘一次（断点续跑）。

========================= 前置条件 =========================
  · Arbiter 已运行（窗口标题 = cfg.json 的 win_name）
  · 分辨率/DPI 与 cfg.json 的 bx/by/cell_width 匹配（本项目生产标定 1600x2560）
  · **必须由用户在真实终端运行**：沙箱进程是 Low 完整性级别，无法向 Arbiter
    （Medium IL）发送鼠标/键盘输入（SetCursorPos / PostMessage 均被拒）。

========================= 用法 =========================
  # 900 局，探针全开，结果落 state/e2e_900/
  D:\\anaconda3\\envs\\py310\\python.exe bench\\e2e_900.py --games 900

  # 断点续跑（中断后原命令重跑）
  ... --games 900 --resume

  # 先跑 3 局冒烟
  ... --games 3 --tag smoke

  # 关探针（纯胜率测试，零额外开销）
  set MSW_E2E_PROBE=0 && ...

输出：
  state/e2e_900/probe.jsonl      探针原始事件（性能/准确性/功能/内存）
  state/e2e_900/games.jsonl      逐局明细（胜负/步数/墙钟）
  state/e2e_900/summary.json     汇总
分析：
  D:\\anaconda3\\envs\\py310\\python.exe bench\\analyze_e2e.py --jsonl state/e2e_900/probe.jsonl
"""
import argparse
import io
import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 探针必须在 import utils 之前设置环境（模块级读取开关）
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("MSW_E2E_PROBE", "1")
os.environ.setdefault("MSW_E2E_MEM_HZ", "2")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STATE_DIR = os.path.join(ROOT, "state", "e2e_900")
GAMES_PATH = os.path.join(STATE_DIR, "games.jsonl")
SUMMARY_PATH = os.path.join(STATE_DIR, "summary.json")
LOCK_PATH = os.path.join(STATE_DIR, "run.lock")


def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    return ((c - r) / d, (c + r) / d)


def load_done():
    """读已完成的局（断点续跑）。"""
    done = []
    if not os.path.exists(GAMES_PATH):
        return done
    with io.open(GAMES_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.append(json.loads(line))
            except ValueError:
                continue
    return done


def append_game(rec):
    os.makedirs(STATE_DIR, exist_ok=True)
    with io.open(GAMES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_summary(payload):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SUMMARY_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SUMMARY_PATH)


def _count_game_ends(probe_path):
    """统计探针里已完成局数（用于目标达成判定与重启决策）。

    只读文件尾部若干字节（900 局 × 每局约 2 条事件，全文件也不大，但避免
    每次轮询都全量解析 —— 这里按行扫，行数上限万级，可接受）。
    """
    n = 0
    try:
        with io.open(probe_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"game_end"' in line:
                    n += 1
    except OSError:
        return 0
    return n


def preflight():
    """前置检查：Arbiter 在跑、原生模块可用、模板可加载、坐标能抓到位。"""
    problems = []
    import win32gui
    import setting
    from utils import native

    hwnd = win32gui.FindWindow(None, setting.cfg["win_name"])
    if not hwnd:
        problems.append(
            f"找不到扫雷窗口（标题 {setting.cfg['win_name']!r}）——请先启动 Arbiter")
    else:
        win32gui.ShowWindow(hwnd, 1)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
        if win32gui.GetForegroundWindow() != hwnd:
            problems.append(
                "Arbiter 未能切到前台（可能被其它窗口挡住）——本脚本需要前台窗口")

    if not native.available:
        problems.append("C++ 原生模块不可用（utils/mscore*.pyd 加载失败）")
    elif not native.has_decide():
        problems.append("原生模块无 decide 导出（pyd 版本不对）")

    # 坐标/模板：抓一张棋盘看是否能识别
    try:
        import numpy as np
        from PIL import ImageGrab
        from utils.mm0 import ClientToScreen
        cfg = setting.cfg
        bx, by = ClientToScreen(hwnd, cfg["bx"], cfg["by"])
        cw, w, h = cfg["cell_width"], cfg["w"], cfg["h"]
        img = ImageGrab.grab((bx + 0.5 * cw, by + 0.5 * cw,
                              w * cw + bx + 0.5 * cw,
                              h * cw + by + 0.5 * cw))
        arr = np.array(img)
        if arr.shape[0] != h * cw or arr.shape[1] != w * cw:
            problems.append(f"棋盘抓取尺寸异常 {arr.shape}，期望 {(h * cw, w * cw, 3)}")
        else:
            # 未开格应是灰块：灰度均值应在合理范围
            g = arr.mean(axis=2)
            if g.std() < 5:
                problems.append(
                    f"棋盘区域图像过于均匀（std={g.std():.1f}）——"
                    f"bx/by 偏移可能不对，或棋盘未显示")
    except Exception as e:  # noqa: BLE001
        problems.append(f"棋盘抓取失败：{type(e).__name__}: {e}")

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=900)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--resume", action="store_true",
                    help="续跑：跳过 games.jsonl 里已完成的局")
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=0,
                    help="墙钟上限（秒），0=不限")
    args = ap.parse_args()

    # 本轮进程的墙钟起点（用 time.time() 与探针的 ts 同口径）。
    # 用于把追加写的 probe.jsonl 里「上一轮残留」与「本轮」切开。
    t_run_start = time.time()
    # 本轮开始前的累计局数/胜局（交叉校验真值源，探针撞上限时也可靠）
    _gs_start_total = 0
    _gs_start_win = 0
    try:
        import setting as _setting
        _p = os.path.join(_setting.STATE_DIR, "game_stats.json")
        if os.path.exists(_p):
            with io.open(_p, encoding="utf-8") as _f:
                _gs0 = json.load(_f)
            _gs_start_total = int(_gs0.get("played", 0))
            _gs_start_win = int(_gs0.get("win", 0))
    except Exception:  # noqa: BLE001
        pass

    print("=" * 100)
    print("真实端到端测量：GUI + Arbiter + 真实截图/点击")
    print("=" * 100)
    print(f"目标 {args.games} 局    tag={args.tag}")
    print(f"探针目录 {os.environ.get('MSW_E2E_DIR') or os.path.join(ROOT, 'state', 'e2e')}")

    if not args.skip_preflight:
        print("\n[前置检查]")
        problems = preflight()
        if problems:
            print("  ❌ 前置检查未通过：")
            for p in problems:
                print(f"     · {p}")
            print("\n  修好后重跑。加 --skip-preflight 可强制跳过（不建议）。")
            return 2
        print("  ✅ Arbiter 在前台、原生模块可用、棋盘抓取正常")

    os.makedirs(STATE_DIR, exist_ok=True)
    # 单实例锁：两个跑者同时驱动同一个 Arbiter 会互相抢点击
    if os.path.exists(LOCK_PATH):
        try:
            with io.open(LOCK_PATH, encoding="utf-8") as f:
                info = json.load(f)
            pid = int(info.get("pid") or 0)
            alive = False
            if pid:
                try:
                    import psutil
                    alive = psutil.pid_exists(pid)
                except Exception:  # noqa: BLE001
                    alive = True
            if alive:
                print(f"\n❌ 已有跑者在运行（pid={pid}）。先停止它，或删除 {LOCK_PATH}")
                return 2
        except (ValueError, OSError):
            pass
    with io.open(LOCK_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started": time.time()}, f)

    done = load_done() if args.resume else []
    if done and not args.resume:
        print(f"\n⚠️ {GAMES_PATH} 已有 {len(done)} 局记录；未加 --resume，"
              f"将重新开始并**追加**（旧记录保留）。")
    if done:
        print(f"续跑：已完成 {len(done)} / {args.games} 局")

    # ------------------------------------------------------------------
    # 启动真实 GUI（与生产同一个类）
    # ------------------------------------------------------------------
    from PyQt5.QtWidgets import QApplication
    from utils import e2e_probe as probe

    probe.start_mem_sampler()
    app = QApplication.instance() or QApplication(sys.argv)

    import main as main_mod
    win = main_mod.MyMainWindow()
    win.show()
    app.processEvents()

    thread = win.auto_play_thread
    print(f"\n[GUI] 主窗口已起；auto_play_thread 就绪（is_play={thread.is_play}）")

    # ⚠️ pyautogui 的 FAILSAFE 会在鼠标位于屏幕角落时抛 FailSafeException，
    # 而本脚本启动后没有真实鼠标移动，光标可能恰好停在角落 ⇒ 第一轮扫描就
    # 被终止（实测：3 局冒烟 1s 内退出，探针记录 category=failsafe）。
    # 生产 GUI 由人手启动通常不触发；自动跑必须显式关闭（只在本测量进程内）。
    import pyautogui
    pyautogui.FAILSAFE = False
    print("[GUI] pyautogui.FAILSAFE 已关闭（自动跑必须；生产手工运行不受影响）")

    # 让 GUI 真正把「开始自动」走完（等价于点按钮，但不弹局数对话框）
    target = args.games
    thread.set_args(target)
    win.stackedWidget.setCurrentIndex(1)
    win.set_btns_Enabled(False)
    win.return_to_main_page.setVisible(False)
    thread.start()

    _hk = thread.stop_hotkey_label()
    print(f"[GUI] 自动线程已启动，目标 {target} 局。")
    print(f"       按 {_hk} 可随时紧急终止（鼠标被占用时也能停）；"
          f"Ctrl+C 同样可中断。")
    print("       已完成的局会保留，重跑本文件可续跑。\n")

    # ------------------------------------------------------------------
    # 事件循环 + 监控
    # ------------------------------------------------------------------
    t_start = time.perf_counter()
    last_total = 0
    last_report = time.perf_counter()
    interrupted = False
    reported_idx = len(done)
    restart_count = 0
    max_restarts = 30
    try:
        while True:
            app.processEvents()
            time.sleep(0.02)
            now = time.perf_counter()
            if now - last_report >= 10.0:
                last_report = now
                try:
                    txt = win.heatmap_label_stats.text()
                except Exception:  # noqa: BLE001
                    txt = ""
                el = now - t_start
                print(f"  [{el:7.0f}s] {txt}"
                      + (f"  (重启 {restart_count} 次)" if restart_count else ""))
                if args.max_seconds and el > args.max_seconds:
                    print(f"\n达到墙钟上限 {args.max_seconds:.0f}s，请求停止")
                    thread.request_cancel()

            # ---- 线程死亡检测 + 自动重启 ----
            # ⚠️ 端到端跑 900 局时，线程可能因**环境性**故障而死（实测：
            #    ImageGrab 间歇性 OSError: screen grab failed，沙箱 Low IL 下
            #    尤其频繁）。这类死亡不该让整轮 900 局白跑 —— 记录死因、退避
            #    后重启线程续跑，并统计重启次数（>阈值才判定为真故障退出）。
            #
            # ⚠️ 但**用户主动停止必须尊重**：紧急热键 / Ctrl+C / 墙钟上限 /
            #    达标都会调 request_cancel() 置位取消标志。若此时仍走自动重启，
            #    clear_cancel()+start() 会把用户刚停掉的线程重新拉起 ⇒ 热键
            #    形同失效。判据：cancel_requested() 为真 ⇒ 不重启、直接退出。
            if not thread.isRunning():
                if thread.cancel_requested():
                    print("\n[停止] 收到主动终止（热键/Ctrl+C/墙钟上限/达标），"
                          "不再自动重启")
                    interrupted = True
                    break
                # 读探针里本局之后是否有 error 事件（判死因）
                if args.max_seconds and (now - t_start) > args.max_seconds:
                    break
                if restart_count >= max_restarts:
                    print(f"\n❌ 线程已连续死亡 {restart_count} 次，超过上限 "
                          f"{max_restarts}，停止（请检查环境）")
                    break
                # 已跑够目标局数？
                n_done = _count_game_ends(probe.path())
                if n_done >= args.games:
                    break
                restart_count += 1
                wait = min(5.0, 0.5 * restart_count)
                print(f"\n  ⚠️ 自动线程已停止（已完成 {n_done}/{args.games} 局），"
                      f"{wait:.1f}s 后第 {restart_count} 次重启…")
                time.sleep(wait)
                try:
                    win.reload()          # 重新读 cfg（可能被用户改过）
                    thread.clear_cancel()
                    # 剩余局数：已完成的会由 _gs_save 累计，这里传剩余量
                    thread.set_args(max(1, args.games - n_done))
                    thread.start()
                    print(f"  ▶ 已重启（剩余 {max(1, args.games - n_done)} 局）")
                except Exception as e:  # noqa: BLE001
                    print(f"  ❌ 重启失败：{type(e).__name__}: {e}")
                    break
                last_report = time.perf_counter()
            elif _count_game_ends(probe.path()) >= args.games:
                print(f"\n已达目标 {args.games} 局，请求停止")
                thread.request_cancel()
                break
    except KeyboardInterrupt:
        interrupted = True
        print("\n[中断] 收到 Ctrl+C，请求协作式停止…")
        thread.request_cancel()
    finally:
        # 等线程真正退出（最多 10s），让 finally 落盘
        deadline = time.perf_counter() + 10.0
        while thread.isRunning() and time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.02)
        probe.stop_mem_sampler()
        try:
            probe.write_summary({"tag": args.tag, "interrupted": interrupted})
        except Exception:  # noqa: BLE001
            pass
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 从探针事件里提取逐局明细
    # ⚠️ 2026-11-09 实测仪器 bug：probe.jsonl 是**追加写**，重跑时文件里
    #    仍有上一轮的全部 game_end。首版汇总读全文件 ⇒ 把「上轮 253 局 +
    #    本轮 4 局」当成本轮 257 局，胜率 38.91% 也是两轮混合值。
    #    现按**本次进程启动时间戳**切段：只取 t_run_start 之后的 game_end。
    #    同时用 game_stats.json 的累计差做交叉校验（见下方校验输出）。
    # ------------------------------------------------------------------
    probe_path = probe.path()
    games = []
    if os.path.exists(probe_path):
        with io.open(probe_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("type") == "game_end"
                        and isinstance(r.get("ts"), (int, float))
                        and r["ts"] >= t_run_start):
                    games.append(r)

    # 续跑时把本轮之前已落盘的明细并入（已按同一切点过滤，不重复）
    if args.resume and done:
        games = done + games
    for g in games:
        append_game(g) if not args.resume else None

    # 交叉校验：game_stats.json 累计差（探针被上限截断时这是唯一真值源）
    _gs_match = None
    try:
        import setting as _setting
        with io.open(os.path.join(_setting.STATE_DIR, "game_stats.json"),
                     encoding="utf-8") as _f:
            _gs_now = json.load(_f)
        _delta = int(_gs_now.get("played", 0)) - _gs_start_total
        _gs_match = {"delta_played": _delta, "probe_game_end": len(games)}
    except Exception:  # noqa: BLE001
        pass
    if _gs_match:
        print(f"\n[交叉校验] game_stats 累计差 {_gs_match['delta_played']} 局 "
              f"vs 探针本轮 game_end {_gs_match['probe_game_end']} 局")
        if _gs_match["delta_played"] > _gs_match["probe_game_end"]:
            print("  ⚠️ 探针事件少于真实局数（可能撞上文件上限被截断）；"
                  "胜率/局数以 game_stats 累计差为准，见下方校正值。")

    n = len(games)
    wins = sum(1 for g in games if g.get("won"))
    # 探针被截断时用 game_stats 累计差作为真值源（局数 + 胜局）
    gs_games = gs_wins = None
    if _gs_match:
        try:
            import setting as _setting
            with io.open(os.path.join(_setting.STATE_DIR, "game_stats.json"),
                         encoding="utf-8") as _f:
                _gs_now = json.load(_f)
            gs_games = _gs_match["delta_played"]
            gs_wins = int(_gs_now.get("win", 0)) - _gs_start_win
        except Exception:  # noqa: BLE001
            pass
    truncated = (gs_games is not None and gs_games > n)
    # 报告口径：探针完整时用探针；截断时明确标注改用 game_stats 校正值
    report_games, report_wins = ((gs_games, gs_wins) if truncated
                                 else (n, wins))
    lo, hi = _wilson(report_wins, report_games) if report_games else (0, 0)
    walls = [float(g.get("wall_ms") or 0.0) / 1000.0 for g in games]
    peaks = [int(g.get("peak_mem_mb") or 0) for g in games]
    el = time.perf_counter() - t_start

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    print(f"  本次进程运行 {el:.0f}s")
    print(f"  探针本轮 game_end 记录 {n}")
    if truncated:
        print(f"  ⚠️ 探针被文件上限截断：仅 {n} 局有明细；"
              f"以下以 game_stats 累计差（真值源）校正：")
    if report_games:
        print(f"  校正后局数 {report_games}  胜局 {report_wins} = "
              f"{report_wins / report_games * 100:.2f}%  "
              f"(95% CI {lo * 100:.2f}% ~ {hi * 100:.2f}%)")
        print(f"  真实墙钟 {el:.0f}s / {report_games} 局 = "
              f"{el / report_games:.2f}s/局")
    if n:
        print(f"  （探针明细）单局 wall_ms：中位 "
              f"{sorted(walls)[n // 2]:.2f}s  均值 {sum(walls) / n:.2f}s"
              f"  最大 {max(walls):.1f}s  ← 该字段口径有缺陷，见分析文档")
        if peaks:
            print(f"  （探针明细）单局峰值 commit：中位 "
                  f"{sorted(peaks)[n // 2]}MB  最大 {max(peaks)}MB")

    summary = {
        "tag": args.tag, "target": args.games,
        "elapsed_s": round(el, 1),
        "games": report_games, "wins": report_wins,
        "win_rate": (round(report_wins / report_games, 4)
                     if report_games else None),
        "ci95": [round(lo, 4), round(hi, 4)] if report_games else None,
        "wall_s_per_game": (round(el / report_games, 2)
                            if report_games else None),
        "wall_median_s": round(sorted(walls)[n // 2], 2) if n else None,
        "wall_mean_s": round(sum(walls) / n, 2) if n else None,
        "wall_max_s": round(max(walls), 2) if n else None,
        "peak_commit_max_mb": max(peaks) if peaks else None,
        "probe_game_end": n,
        "probe_truncated": bool(truncated),
        "probe_path": probe_path,
        "interrupted": interrupted,
        "python": sys.version.split()[0],
        "pyd": os.path.join(ROOT, "utils", "mscore.cp310-win_amd64.pyd"),
    }
    try:
        import hashlib
        h = hashlib.sha256()
        with open(summary["pyd"], "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        summary["pyd_sha8"] = h.hexdigest()[:8].upper()
    except OSError:
        pass
    write_summary(summary)
    print(f"\n  落盘：{GAMES_PATH}")
    print(f"  落盘：{SUMMARY_PATH}")
    print(f"  探针：{probe_path}")
    print(f"\n  分析：D:\\anaconda3\\envs\\py310\\python.exe bench\\analyze_e2e.py "
          f"--jsonl {probe_path}")
    return 0 if not interrupted else 130


if __name__ == "__main__":
    sys.exit(main())
