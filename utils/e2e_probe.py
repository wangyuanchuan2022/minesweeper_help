# -*- coding: utf-8 -*-
"""端到端测量探针（真实 GUI + 真实 Arbiter 窗口运行时的性能/准确性/功能遥测）。

========================= 设计约束 =========================
1. **默认关闭，关闭时零开销**：所有入口第一行 `if not enabled(): return`，
   不做任何 import 之外的准备工作。生产行为与不装探针完全一致。
2. **绝不影响主流程**：所有记录都包在 try/except 里，异常一律吞掉只留 debug
   日志。探针坏掉绝不能让扫雷跑不动（本项目历史上吃过"观测代码拖死主流程"）。
3. **只观测、不决策**：本模块不返回值给算法，不修改 cell_value，不参与任何判断。
4. **落盘可续读**：JSONL 追加写，一行一个事件；进程崩溃也不丢已写部分。

========================= 开关 =========================
    MSW_E2E_PROBE=1        总开关（默认 0=关）
    MSW_E2E_DIR=<path>     落盘目录（默认 <repo>/state/e2e）
    MSW_E2E_MEM_HZ=<n>     内存采样频率 Hz（默认 2；0=不采）
    MSW_E2E_SLOW_MS=<n>    只把超过 n ms 的 span 写进慢事件流（默认 0=全写）

========================= 事件类型 =========================
    game_begin / game_end      一局的起止（含胜负、步数、墙钟、内存峰值）
    span                       一段代码的耗时（name + ms + 可选字段）
    decision                   一次 C++ decide（reason/xy/conf/world/situations…）
    click                      一次真实点击（坐标 + 该击的自评安全率 + 来源）
    mem                        内存采样（rss / commit / peak）
    mark                       自由计数（name + kv），用于功能覆盖统计
    error                      生产侧捕获到的异常/告警（category + text）

========================= 怎么用 =========================
生产代码里只加两类调用（都在热路径外或微秒级）：

    from . import e2e_probe as probe
    with probe.span("vision.complete_scan"):
        ...原有代码...

    probe.mark("vision.unknown_ratio", ratio=ratio, unknown=unknown, total=total)

分析器：bench/analyze_e2e.py 读 state/e2e/probe.jsonl 出报告。
"""
import json
import os
import sys
import threading
import time

_ENABLED = os.environ.get("MSW_E2E_PROBE", "0") not in ("0", "", "false", "False")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.environ.get("MSW_E2E_DIR") or os.path.join(_ROOT, "state", "e2e")
_PATH = os.path.join(_DIR, "probe.jsonl")
_SUMMARY_PATH = os.path.join(_DIR, "probe_summary.json")

_MEM_HZ = 2.0
try:
    _MEM_HZ = float(os.environ.get("MSW_E2E_MEM_HZ", "2") or 0)
except ValueError:
    _MEM_HZ = 2.0

_SLOW_MS = 0.0
try:
    _SLOW_MS = float(os.environ.get("MSW_E2E_SLOW_MS", "0") or 0)
except ValueError:
    _SLOW_MS = 0.0

# 单文件上限：超过就停写（避免 900 局长跑把磁盘写满）。
# ⚠️ 2026-11-09 实测教训：253 局/2318s 就写到 20MB（139993 事件），
#    第二轮跑到第 4 局就撞上限 ⇒ 后续 253 局详细探针全部丢失，
#    只剩 decide_trace.jsonl（独立链路）覆盖。20MB 对 900 局远远不够。
#    现默认 128MB（约 90 万事件，足够 900 局 + 余量）；可用 MSW_E2E_MAX_MB 覆盖。
_MAX_BYTES = int(float(os.environ.get("MSW_E2E_MAX_MB", "128") or 128)) * 1024 * 1024

_lock = threading.Lock()
_seq = [0]
_stopped = [False]
_t0 = time.time()

# 当前局的上下文（供 span/decision 自动带上 game 序号）
_game = {"idx": 0, "t0": 0.0, "mem_peak": 0, "mem_base": 0, "active": False}

# 内存采样线程
_mem_thread = None
_mem_stop = None


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def enabled():
    """探针是否启用（生产代码唯一需要判断的入口）。"""
    return _ENABLED and not _stopped[0]


def _ensure_dir():
    try:
        os.makedirs(_DIR, exist_ok=True)
    except OSError:
        pass


def _write(rec):
    """追加一条事件（内部函数；调用方已持锁或单线程）。"""
    global _seq
    if _stopped[0]:
        return
    try:
        if _seq[0] % 500 == 0 and os.path.exists(_PATH):
            if os.path.getsize(_PATH) > _MAX_BYTES:
                _stopped[0] = True
                _write_direct({
                    "type": "probe_stopped", "ts": round(time.time(), 3),
                    "reason": "file_size_limit", "bytes": _MAX_BYTES,
                })
                return
        _seq[0] += 1
        rec.setdefault("ts", round(time.time(), 3))
        rec.setdefault("seq", _seq[0])
        rec.setdefault("game", _game["idx"])
        _write_direct(rec)
    except Exception:  # noqa: BLE001 —— 记录失败绝不影响主流程
        pass


def _write_direct(rec):
    try:
        _ensure_dir()
        with open(_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _emit(rec):
    if not enabled():
        return
    with _lock:
        _write(rec)


# ---------------------------------------------------------------------------
# 内存
# ---------------------------------------------------------------------------
def _mem_now():
    """返回 (rss_mb, commit_mb)。

    commit 在 Windows 上取 **PrivateUsage（提交量）**：这是判断"内存是否真的
    在涨"的正确口径——WorkingSet 会被系统按需回收（本项目 2000 局实测过
    sawtooth 假象），commit 只增不减才是真泄漏。
    """
    # 优先 psutil：`private` 字段在 Windows 上就是 PrivateUsage=commit
    try:
        import psutil
        mi = psutil.Process().memory_info()
        rss = mi.rss / 1048576.0
        commit = getattr(mi, "private", mi.rss) / 1048576.0
        return rss, commit
    except Exception:  # noqa: BLE001
        pass
    # 回退 ctypes（必须显式设 argtypes/restype，否则 64 位 HANDLE 会被截断成
    # int32 而恒返回 0 —— 首版就踩了这个坑，读数全是 0 却"看起来正常"）
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(
                k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return (pmc.WorkingSetSize / 1048576.0,
                    pmc.PagefileUsage / 1048576.0)
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _mem_loop(stop_evt):
    interval = 1.0 / _MEM_HZ if _MEM_HZ > 0 else 1.0
    while not stop_evt.wait(interval):
        rss, commit = _mem_now()
        if rss is None:
            continue
        peak = _game["mem_peak"] = max(_game["mem_peak"], int(commit or rss))
        _emit({"type": "mem", "rss_mb": round(rss, 1),
               "commit_mb": round(commit or rss, 1), "peak_mb": peak})


def start_mem_sampler():
    """启动后台内存采样线程（幂等）。GUI 启动时调一次即可。"""
    global _mem_thread, _mem_stop
    if not enabled() or _MEM_HZ <= 0:
        return
    if _mem_thread is not None and _mem_thread.is_alive():
        return
    _mem_stop = threading.Event()
    _mem_thread = threading.Thread(
        target=_mem_loop, args=(_mem_stop,), daemon=True, name="e2e-mem")
    _mem_thread.start()


def stop_mem_sampler():
    if _mem_stop is not None:
        _mem_stop.set()


# ---------------------------------------------------------------------------
# 局生命周期
# ---------------------------------------------------------------------------
def game_begin(idx=None, **kv):
    """新一局开始。**幂等**：已在局中时直接返回（调用方可放在循环顶部无脑调）。

    这样生产侧只需要在 `play()` 的 while 顶部放一行 `probe.game_begin()`，
    再在四处胜负分支放 `probe.game_end(won=...)`，不需要维护额外的状态变量。
    """
    if not enabled() or _game["active"]:
        return
    with _lock:
        _game["idx"] = int(idx if idx is not None else _game["idx"] + 1)
        _game["t0"] = time.perf_counter()
        _game["active"] = True
        rss, commit = _mem_now()
        _game["mem_base"] = int(commit or rss or 0)
        _game["mem_peak"] = _game["mem_base"]
    _emit({"type": "game_begin", "base_mem_mb": _game["mem_base"], **kv})


def game_end(won=None, **kv):
    """一局结束（幂等：未在局中时返回空 dict）。返回本局摘要。"""
    if not enabled() or not _game["active"]:
        return {}
    dt = time.perf_counter() - _game["t0"] if _game["t0"] else 0.0
    rss, commit = _mem_now()
    peak = max(_game["mem_peak"], int(commit or rss or 0))
    summary = {
        "type": "game_end",
        "game": _game["idx"],
        "won": bool(won) if won is not None else None,
        "wall_ms": round(dt * 1000.0, 1),
        "base_mem_mb": _game["mem_base"],
        "peak_mem_mb": peak,
        "end_mem_mb": int(commit or rss or 0),
        **kv,
    }
    _game["active"] = False
    _game["t0"] = 0.0
    _emit(summary)
    return summary


def game_active():
    """当前是否处于一局之内（探针关闭时恒 False）。"""
    return enabled() and _game["active"]


# ---------------------------------------------------------------------------
# 计时 span
# ---------------------------------------------------------------------------
class span(object):  # noqa: N801 —— 故意用小写，作为上下文管理器用起来像语句
    """`with probe.span("vision.scan", cells=480): ...` —— 只测耗时。

    关闭时 `__enter__` 返回 False，调用方可据此跳过额外统计代码。
    """

    __slots__ = ("name", "kv", "t0", "_on")

    def __init__(self, name, **kv):
        self.name = name
        self.kv = kv
        self.t0 = 0.0
        self._on = False

    def __enter__(self):
        self._on = enabled()
        if self._on:
            self.t0 = time.perf_counter()
        return self._on

    def __exit__(self, exc_type, exc, tb):
        if not self._on:
            return False
        ms = (time.perf_counter() - self.t0) * 1000.0
        if exc_type is not None:
            rec = {"type": "span", "name": self.name,
                   "ms": round(ms, 3), "error": str(exc)[:200]}
            rec.update(self.kv)
            _emit(rec)
            return False
        if _SLOW_MS > 0 and ms < _SLOW_MS:
            return False
        rec = {"type": "span", "name": self.name, "ms": round(ms, 3)}
        rec.update(self.kv)
        _emit(rec)
        return False


# ---------------------------------------------------------------------------
# 决策 / 点击 / 标记 / 错误
# ---------------------------------------------------------------------------
_DECISION_KEYS = (
    "reason", "x", "y", "confidence", "world_upper", "situations", "dstar",
    "limitation", "estimated_bytes", "cost_log2", "path", "fallback_from",
)


def decision(r, ms, tag="", **kv):
    """记录一次 C++ decide 的输入规模 + 输出 + 实测耗时。"""
    if not enabled():
        return
    rec = {"type": "decision", "ms": round(float(ms), 3), "tag": tag}
    try:
        if isinstance(r, dict):
            for k in _DECISION_KEYS:
                v = r.get(k)
                if isinstance(v, (int, float, str, bool)) or v is None:
                    rec[k] = v
            for k in ("prob", "tied", "transitions"):
                try:
                    rec[k + "_len"] = len(r.get(k) or [])
                except TypeError:
                    rec[k + "_len"] = 0
        else:
            rec["reason"] = None
            rec["decide_none"] = True
    except Exception:  # noqa: BLE001
        pass
    rec.update(kv)
    _emit(rec)


def click(x, y, conf=None, reason="", source="decide", **kv):
    """记录一次真实点击。conf = 该击的**自评安全率**（用于事后校准/判死因）。"""
    if not enabled():
        return
    rec = {"type": "click", "x": int(x), "y": int(y),
           "conf": None if conf is None else round(float(conf), 5),
           "reason": str(reason), "source": source}
    rec.update(kv)
    _emit(rec)


def mark(name, **kv):
    """自由计数/功能覆盖打点（name 为唯一键，分析器按 name 聚合）。"""
    if not enabled():
        return
    rec = {"type": "mark", "name": str(name)}
    rec.update(kv)
    _emit(rec)


def error(category, text, **kv):
    """生产侧捕获到的异常/告警（用于「功能是否真实现」的负向证据）。"""
    if not enabled():
        return
    rec = {"type": "error", "category": str(category),
           "text": str(text)[:400]}
    rec.update(kv)
    _emit(rec)


# ---------------------------------------------------------------------------
# 收尾汇总
# ---------------------------------------------------------------------------
def write_summary(extra=None):
    """把当前进程的探针状态写成 probe_summary.json（进程退出前调一次）。"""
    if not _ENABLED:
        return None
    try:
        rss, commit = _mem_now()
        out = {
            "path": _PATH,
            "events": _seq[0],
            "stopped": _stopped[0],
            "uptime_s": round(time.time() - _t0, 1),
            "mem_hz": _MEM_HZ,
            "rss_mb": None if rss is None else round(rss, 1),
            "commit_mb": None if commit is None else round(commit, 1),
            "peak_mem_mb": _game["mem_peak"],
            "games": _game["idx"],
            "python": sys.version.split()[0],
            "extra": extra or {},
        }
        _ensure_dir()
        tmp = _SUMMARY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _SUMMARY_PATH)
        return out
    except Exception:  # noqa: BLE001
        return None


def path():
    """探针 JSONL 路径（供 UI/日志提示用户去哪看）。"""
    return _PATH
