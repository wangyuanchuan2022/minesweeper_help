# -*- coding: utf-8 -*-
"""整局配对 A/B：把每个决策的"选格策略"整个换掉，跑完整对局比胜率。

=================== 与 bench/arms_lookahead.py 的分工 ====================
· arms_lookahead：单决策 regret 分析（33 个固定局面），回答"哪一步选错了"。
  **不能**据此宣称整局胜率提升——局部 regret 不线性累加（advisor 提醒）。
· 本脚本：整局配对 A/B（同 seed 同棋盘），回答"胜率到底变不变"。
  这是唯一可用来宣称"提升"的证据。

=================== 臂的定义（每个臂都是一套完整选格策略）====================
  baseline : 生产原样（win_rate 深分支 + process_bigger_situation 贪心分支）
  safety   : 免费推理饱和后取最大 safety
  expand   : 免费推理饱和后取最大期望展开格数
  ply2/3   : 免费推理饱和后取最大 2/3 步前瞻分
  exact    : 免费推理饱和后取精确最优（oracle，理论天花板）

所有非 baseline 臂共用同一套"免费推理饱和"（把在所有一致世界里都不是雷的格
全部开掉，零风险），因此臂间差异纯粹来自候选排序准则。baseline 保持生产行为
（不额外饱和），这样对比是"生产 vs 新策略"。

=================== 安全设计 ====================
  · 世界数上限 --world-cap（默认 4096）：超限该步回退生产策略（不崩溃）
  · 节点上限 --node-cap（默认 20 万/决策）：超限同样回退生产
  · 每步决策记录 branch/耗时，便于事后归因
  · 不改任何生产文件；state 全部重定向

用法:
  python bench/ab_policy.py --games 200 --workers 12 --arm exact
  python bench/ab_policy.py --games 200 --workers 12 --arm baseline
  python bench/ab_compare.py state/ab/baseline.json state/ab/exact.json
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bench"))
os.chdir(ROOT)

UNOPENED = 9


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# ---------------------------------------------------------------------------
def make_case(clicks, clicks9, res_list, cell_value, ck, num10, a, w, h):
    """把求解器的入参打包成 ExactEngine 需要的 case 字典。

    对 ck / groups 做**形状归一化**：生产在不同路径下可能给出
      · ck: [int, ...]  或 [[int], ...]（后者见 process_bigger_situation）
      · groups: [np.ndarray] 或 [list-of-list]
    直接 int(c) 会在 [[3]] 上抛 TypeError。
    """
    import numpy as np

    def _scalar(x):
        if isinstance(x, (list, tuple, np.ndarray)):
            x = np.asarray(x).ravel()
            return int(x[0]) if x.size else 0
        return int(x)

    ck_flat = [_scalar(c) for c in ck]
    total = 1
    for c in ck_flat:
        total *= c
    groups_norm = []
    for g in res_list:
        arr = np.asarray(g)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        groups_norm.append([[int(v) for v in row] for row in arr.tolist()])
    return {
        "F": len(clicks), "U": len(clicks9), "total": total,
        "clicks": [list(map(int, p)) for p in clicks],
        "clicks9": [list(map(int, p)) for p in clicks9],
        "ck": ck_flat,
        "num10": int(num10), "a": int(a), "w": int(w), "h": int(h),
        "is_play": True,
        "groups": groups_norm,
        "cell_value": np.asarray(cell_value).astype(int).tolist(),
    }


class PolicyFallback(Exception):
    """该步无法用目标臂决策（世界数/节点超限、无解等）→ 回退生产。"""


# 各类降级/闸门触发的计数（**可观测性**要求：任何降级都必须能被统计到，
# 否则"跑了 2000 局但不知道降级了多少次"会让结论不可信）。
#   world_cap   —— 世界数超资源上限（合法大局面，资源不可行）
#   invalid_case—— 真正的逻辑矛盾（num10>a 等，规则级不变量被违反）
#   no_world    —— 无一致世界
#   node_budget —— 递归节点/时间超限
_GATE_STATS = {"world_cap": 0, "invalid_case": 0, "no_world": 0,
               "node_budget": 0}


def reset_gate_stats():
    """每局开头清零 bench 侧计数（生产侧用**差值**，见下）。"""
    for k in _GATE_STATS:
        _GATE_STATS[k] = 0


def prod_nodecap_snapshot():
    """读取生产侧 `_wr_nodecap_hits` 的**进程内单调累计值**。

    ==================== 为什么用快照+差值，而不是清零 ====================
    `utils/probability.py` 的 `_WR_NODECAP_TOTAL[0]` 是**进程内单调累计**，
    由每局前后取差值即可得到本局命中数。**不要在 play_one 里清零**：
      · 清零会与 Solver 实例的 `_wr_nodecap_hits` 形成两套口径，容易漂移
      · 若将来并行（多线程/多进程），清零会互相污染
    差值口径天然支持并发（各线程只需各自取前后快照）。

    ⚠️ 多线程/多进程口径：`_WR_NODECAP_TOTAL` 是**模块级全局**，
    仅在单进程单线程顺序跑（当前 bench 的用法）时差值精确。
    若改为多线程，需改成 thread-local；多进程则各进程独立，天然隔离。
    """
    try:
        import utils.probability as _p
        return int(_p._WR_NODECAP_TOTAL[0])
    except Exception:                              # noqa: BLE001
        return 0


def gate_stats(prod_snapshot_before=None):
    """返回闸门计数。

    ⚠️ 必须把**生产侧**降级统计进来：`_GATE_STATS["node_budget"]` 只记
    bench 前瞻臂的 Budget，而生产 `wr_f` 节点超限降级走的是
    `utils/probability.py` 的 `_wr_nodecap_hits`。
    此前两者没打通，导致扫 node_cap 时降级恒为 0，
    得出"10 万 cap 零触发"的错误结论（实测生产最大节点数 99,970）。

    生产侧命中数 = 当前累计 − 传入的本局前快照。
    """
    d = dict(_GATE_STATS)
    cur = prod_nodecap_snapshot()
    before = prod_snapshot_before if prod_snapshot_before is not None else 0
    d["prod_nodecap"] = max(0, cur - before)
    return d


def decide_with_arm(arm, case, world_cap, node_cap):
    """用指定臂在给定状态上选一个格（坐标 1-based）。返回 (cell, info)。

    ⚠️ 世界数上限必须**在构造 ExactEngine 之前**判断，否则无效：
    ExactEngine.__init__ 内部会调 enumerate_worlds 把全部世界分配进内存。
    历史事故：写成"先构造再检查 sig.n_worlds > world_cap"，
    结果 U=30/rem=15 时 C(30,15)=1.55e8 个世界先被分配，
    实测跑到 20GB+ 把整机顶爆 —— 检查完全形同虚设。
    现在 ExactEngine 自身也带 world_cap 并在构造前抛 WorldCapExceeded，
    这里显式捕获并转成 PolicyFallback（回退生产策略）。
    """
    from arms_lookahead import ExactEngine, Budget, WKey
    from compare_2ply_vs_winrate import WorldCapExceeded
    try:
        sig = ExactEngine(case, node_cap=node_cap, time_cap=30.0,
                          world_cap=world_cap)
    except WorldCapExceeded as e:
        # ⚠️ 这是**资源超限降级**，不是"局面非法"。
        # 典型场景：刚开局只开 1 格（F=8, U=471, num10=0），剩余 99 雷要撒进
        # 471 个内部格 ⇒ C(471,99) 天文数字。局面完全合法，只是前置枚举
        # 在本局面下不可行。必须可观测地回退，不能当坏数据丢弃。
        _GATE_STATS["world_cap"] += 1
        raise PolicyFallback(f"资源超限(世界数) {e}")
    except ValueError as e:
        # 真正的**逻辑矛盾**（num10>a、F+U>w*h 等），规则级不变量被违反。
        # 这才是"局面非法"，应回退并记为异常，绝不可硬算概率。
        _GATE_STATS["invalid_case"] += 1
        raise PolicyFallback(f"局面矛盾 {e}")
    if sig.n_worlds == 0:
        _GATE_STATS["no_world"] += 1
        raise PolicyFallback("无一致世界")
    wk = WKey.all(sig.n_worlds)
    op = sig.opened0
    try:
        terms = sig.saturate(wk, op)
    except Budget as e:
        _GATE_STATS["node_budget"] += 1
        raise PolicyFallback(f"资源超限(饱和) {e}")
    # 选第一个有候选的终端态（多终端时按权重最大者，简化但确定性）
    terms = [t for t in terms if not sig.won(t[1]) and sig.candidates(t[0], t[1])]
    if not terms:
        raise PolicyFallback("饱和后无可选格")
    terms.sort(key=lambda t: -t[2])
    wk2, op2, _wt = terms[0]
    cands = sig.candidates(wk2, op2)
    try:
        if arm == "safety":
            c = sig.pick(cands, wk2, op2, "safety", 0)
        elif arm == "expand":
            c = sig.pick(cands, wk2, op2, "expand", 0)
        elif arm == "ply2":
            c = sig.pick(cands, wk2, op2, "ply", 2)
        elif arm == "ply3":
            c = sig.pick(cands, wk2, op2, "ply", 3)
        elif arm == "exact":
            c = max(cands, key=lambda x: (sig.after_click(x, wk2, op2), -x))
        else:
            raise PolicyFallback(f"未知臂 {arm}")
    except Budget as e:
        _GATE_STATS["node_budget"] += 1
        raise PolicyFallback(f"资源超限(决策) {e}")
    return sig._coord(c), {"n_worlds": sig.n_worlds, "nodes": sig.nodes,
                           "n_cand": len(cands), "n_terms": len(terms)}


# ---------------------------------------------------------------------------
def play_one(seed, w, h, a, arm, limit=80, world_cap=4096, node_cap=200000,
             max_steps=None, wr_threshold=None, first=None):
    """跑一局。wr_threshold 不为 None 时强制该阈值（走真实读取路径）。

    first：首点坐标 (x, y)，0-based；None = 默认中心 (w//2, h//2)。

    ==================== 为什么首点位置值得成为实验变量 ====================
    Arbiter 是**延迟布雷**（只保证首点**本格**安全，不保证 8 邻域）。
    因此首格的数字 = 其真实邻域雷数，而邻域格数取决于位置：
        角落 3 格 / 边缘 5 格 / 中心 8 格
    精确组合数给出 P(首格数字 = 0)（= 会 flood-fill 展开一大片）：
        角落 49.85% / 边缘 31.25% / 中心 15.45%
    ⇒ 在 Arbiter 规则下，**角落开局反而最容易开出大片信息**，
      而生产代码固定点中心（w//2, h//2）恰好是三者中最差的。
    ⚠️ 注意这与**现代规则**（首点及 8 邻域无雷）的结论**相反**：
      现代规则下角落只保护 4 格、开局面积小、胜率低约 5pp
      （MinesweeperHQ 170 万局实测）。两套规则不可混用结论。
    """
    import numpy as np
    from sim.eval_harness import SimDriver, GameOver, install_stubs
    from sim.minesweeper_sim import Board, UNOPENED as U
    from utils.solver import Solver
    import utils.probability as prob

    # ⚠️ 关键接线（此前缺失，导致 node_cap 扫描轴空转）：
    # `play_one(node_cap=...)` 原本**只**传给 bench 前瞻臂 decide_with_arm，
    # 而生产的 node_cap 由 `prob._wr_node_cap()` 从环境变量读取，
    # 两者完全没打通。结果：扫 node_cap=10万/200万/2000万 时，
    # 实际传入 C++ 的始终是默认 200万 ⇒ 三档"零触发"是**假象**。
    # 现改为：把本函数的 node_cap 显式写进环境变量，让生产路径读到同一个值。
    # 用 None 表示"不改动"，便于 baseline 对照。
    if node_cap is not None:
        os.environ["MSW_WR_NODE_CAP"] = str(int(node_cap))

    np.random.seed(seed)
    rng = random.Random(seed)
    board = Board(w, h, a, rng)
    fx, fy = first if first else (w // 2, h // 2)
    board.open(fx, fy)
    _view0 = board.view(set())
    # 记录首格数字（P2 的中间指标：首格为 0 的比率）。
    # 必须在任何后续点击前读，否则该格信息被后续状态覆盖。
    # ⚠️ BoardView 用 cells[y][x] 访问（不是 view[y][x]）——
    #    第一版写成 _view0[fy][fx] 直接 TypeError 被吞成 None，
    #    导致 20 局全部 first_num=None。已修正。
    try:
        first_num = int(_view0.cells[fy][fx])
    except Exception:
        first_num = None
    _view0.audit_no_leak()

    s = Solver()
    s.w, s.h, s.a = w, h, a
    s.limit = limit
    s.is_play = True
    s.num, s.checked, s.pos_dict_list, s.appended_pos = 1, {}, [], set()
    s.stats_data, s._stats_updates = {}, 0
    s.till_now_winrate = 1.0
    s.count = 0
    s.bx, s.by, s.cell_width = 0.0, 0.0, 24
    # ⚠️ wr_threshold 生效路径（本轮修复的关键陷阱）：
    # probability.number5_1 里有两处阈值来源：
    #   ① 常量兜底：wr_threshold = 6 + native.LIMIT_BONUS_WIN_RATE
    #   ② 真实读取：if _tb is not None: wr_threshold = _wr_current_threshold(_tb)
    # 此前本函数硬编码 s._time_budget = None ⇒ 永远走①常量路径，
    # 而 _wr_update 又会动态改阈值 ⇒ 外部设 wr_threshold 无效
    # （实测：目标 th=16，实际生效 8）。
    # 现改为：传入 wr_threshold 时构造 _time_budget={"wr":{"t": th}}，
    # 让生产走②真实读取路径，阈值才真正可控。
    if wr_threshold is not None:
        s._time_budget = {"wr": {"t": float(wr_threshold)}}
    else:
        s._time_budget = None

    drv = SimDriver(board, s, rng)
    s.complete_scan = drv.complete_scan
    s.rescan_after_click = drv.rescan_after_click

    import pyautogui
    pyautogui.click = drv.on_click

    arm_stats = {"calls": 0, "fallback": 0, "ms": 0.0, "max_ms": 0.0,
                 "nodes": 0, "worlds": []}

    # 生产侧 node cap 命中的**本局前快照**（用差值算本局命中数，
    # 不清零全局累计，避免与 Solver 实例计数形成两套漂移口径）
    _prod_cap_before = prod_nodecap_snapshot()

    # ⚠️ 闭包链泄漏（本轮修复的重大 bug）
    # 历史写法：在 play_one 内部读 orig_* 并赋值类属性、**从不还原**。
    #   ⇒ 第 1 局装上 wrapper1（闭包捕获当届 arm_stats/board/s/drv）
    #   ⇒ 第 2 局读到的 orig 已是 wrapper1，再套 wrapper2 …
    #   ⇒ 第 N 局形成长度 N 的闭包链，每环持有一整局的 Solver+Board，
    #     且调用时还要穿过 N 层 wrapper ⇒ 2000 局 = 2000 局对象常驻 + O(N²) 调用开销。
    # 修复：记录**真正的原始函数**（模块级一次性快照），并在 finally 里还原。
    # 同时把本局状态放进一个 dict 供 wrapper 读取，避免闭包捕获大对象。
    _STATE = {"arm": arm, "world_cap": world_cap, "node_cap": node_cap,
              "stats": arm_stats}

    if arm != "baseline":
        # 模块级快照：只在第一次 patch 时记录真正的原始实现
        if not hasattr(prob, "_AB_ORIG"):
            prob._AB_ORIG = {
                "win_rate": prob.ProbabilityMixin.win_rate,
                "pbs": prob.ProbabilityMixin.process_bigger_situation,
            }
        orig_wr = prob._AB_ORIG["win_rate"]
        orig_pbs = prob._AB_ORIG["pbs"]

        # ⚠️ 关键：把普通函数赋给类属性后，它会**被绑定为方法**，调用时
        # Python 自动传入实例作为第一个位置参数。因此 wrapper 的签名必须
        # 显式以 self 开头，且内部调用原函数时也要显式传 self。
        # 曾用 `def fn(*args)` + `args[:6]`，把 self 当成了 clicks，
        # 造成参数整体错位（表现为 pbs 收到 10 个参数、clicks 收到 int）。
        #
        # ⚠️ 另：wrapper 内**不要**闭包捕获 board/s/drv 等大对象，
        # 只从 _STATE 取本局标量与计数容器（防泄漏，见上方注释）。
        def wr_arm(self, clicks, clicks9, res_list, cell_value, ck, num10):
            st = _STATE["stats"]
            t0 = time.perf_counter()
            st["calls"] += 1
            try:
                case = make_case(clicks, clicks9, res_list, cell_value,
                                 ck, num10, self.a, self.w, self.h)
                cell, info = decide_with_arm(_STATE["arm"], case,
                                             _STATE["world_cap"],
                                             _STATE["node_cap"])
                ms = (time.perf_counter() - t0) * 1000.0
                st["ms"] += ms
                st["max_ms"] = max(st["max_ms"], ms)
                st["nodes"] += info["nodes"]
                st["worlds"].append(info["n_worlds"])
                # 返回形状与 win_rate 一致：number5_1 只从 clicks 里按序取格，
                # 因此把选中格的那一位置 1、其余置 0 即可定向选择。
                order = [tuple(map(int, p)) for p in clicks] + \
                        [tuple(map(int, p)) for p in clicks9]
                vals = np.zeros(len(order), dtype=float)
                for i, p in enumerate(order):
                    if tuple(p) == tuple(cell):
                        vals[i] = 1.0
                if vals.sum() == 0:
                    raise PolicyFallback("选中格不在候选集")
                c2p = {tuple(p): float(vals[i]) for i, p in enumerate(order)}
                return (vals, order, int(len(order)), c2p)
            except PolicyFallback:
                st["fallback"] += 1
                return orig_wr(self, clicks, clicks9, res_list, cell_value,
                               ck, num10)

        prob.ProbabilityMixin.win_rate = wr_arm

        def pbs_arm(self, total, num9, num10, clicks, clicks9, res_list, ck,
                    cell_value, pos):
            """大情况分支也用同一臂。

            ⚠️ 生产有两**种返回形态**（见 utils/probability.py）：
              ① return cell_value                      —— 内部重扫路径
              ② return pos, confidence, total, _prob   —— 正常返回
            本 wrapper 只在"能成功用臂决策"时走②；任何异常一律回退原实现，
            因此①路径的控制流**完全不变**（不会被计入一次候选选择）。
            """
            st = _STATE["stats"]
            st["calls"] += 1
            try:
                case = make_case(clicks, clicks9, res_list, cell_value, ck,
                                 num10, self.a, self.w, self.h)
                cell, _info = decide_with_arm(_STATE["arm"], case,
                                              _STATE["world_cap"],
                                              _STATE["node_cap"])
                prob_map = {tuple(map(int, q)): 1.0 for q in
                            list(clicks) + list(clicks9)}
                return ([cell], 1.0, int(total), prob_map)
            except PolicyFallback:
                st["fallback"] += 1
                return orig_pbs(self, total, num9, num10, clicks, clicks9,
                                res_list, ck, cell_value, pos)

        prob.ProbabilityMixin.process_bigger_situation = pbs_arm

    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
    for i in range(1, w + 1):
        for j in range(1, h + 1):
            cell_value[j, i] = U

    steps = 0
    max_steps = max_steps or w * h * 3
    stalls = 0
    t0 = time.perf_counter()
    while not board.dead and not board.won and steps < max_steps:
        steps += 1
        try:
            # ⚠️ 下面这段必须与 sim/eval_harness.py:297-357 **逐行同构**，
            # 否则台架不等价、整局 A/B 读数无效。首版漏了三处，实测 12 局
            # 有 5 局步数不同（胜负相同但步数偏移）：
            #   ① 每步开头 drv.in_decision = False（免费推理阶段标记）
            #   ② number5_1 之后把 10/11 标记格回写（保持标记状态一致）
            #   ③ stall 判据是"连续 3 次无点击"才 break，不是 1 次
            drv.in_decision = False
            cell_value = s.complete_scan(cell_value, True)
            sum2 = np.sum(cell_value)
            try:
                cell_value = s.mine_clear1(cell_value, 0, 0)
            except ValueError:
                continue
            cell_value = s.mine_clear3_1(cell_value, 0, 0)
            sum3 = np.sum(cell_value)
            if board.dead or board.won:
                break
            if sum3 == sum2:
                _pre = cell_value.copy()
                drv.in_decision = True
                cell_value = s.number5_1(cell_value)
                for _flag in (10, 11):
                    _m = (_pre == _flag) & (cell_value == UNOPENED)
                    cell_value[_m] = _flag
                drv.in_decision = False
                if board.dead or board.won:
                    break
            if drv.n_click == 0 and not board.dead and not board.won:
                stalls += 1
                if stalls >= 3:
                    break
            else:
                stalls = 0
        except GameOver:
            break
        except Exception as e:                              # noqa: BLE001
            if os.environ.get("AB_RAISE"):
                raise
            return {"seed": seed, "won": False, "died": False,
            "first_num": first_num, "steps": steps,
                    "error": f"{type(e).__name__}: {e}"}

    drv.flush_clicks()
    ws = arm_stats["worlds"]
    # ⚠️ 局末必须还原 monkey-patch（闭包链泄漏修复的另一半）。
    # 不还原的后果：下一局的 play_one 又会套一层 wrapper，形成长度 = 局数的
    # 闭包链，每环捕获当届 Solver/Board ⇒ 2000 局 = 2000 局对象常驻 + O(N²) 调用开销。
    if arm != "baseline":
        try:
            if hasattr(prob, "_AB_ORIG"):
                prob.ProbabilityMixin.win_rate = prob._AB_ORIG["win_rate"]
                prob.ProbabilityMixin.process_bigger_situation = \
                    prob._AB_ORIG["pbs"]
        finally:
            pass
    result = {
        "seed": seed, "won": bool(board.won), "died": bool(board.dead),
        "first_num": first_num,
        "steps": steps, "opened": board.opened_count,
        "target": w * h - a, "n_click": drv.n_click,
        "ms": (time.perf_counter() - t0) * 1000.0,
        "arm_calls": arm_stats["calls"],
        "arm_fallback": arm_stats["fallback"],
        "arm_ms_total": arm_stats["ms"],
        "arm_ms_max": arm_stats["max_ms"],
        "arm_nodes": arm_stats["nodes"],
        "arm_worlds_max": max(ws) if ws else 0,
        "arm_worlds_med": (sorted(ws)[len(ws) // 2] if ws else 0),
        # 各闸门触发次数（可观测性：任何降级都必须能被统计）
        # 生产侧 node cap 用「本局前快照 → 当前累计」的差值，不做清零
        "gate_stats": gate_stats(_prod_cap_before),
    }
    # 主动断引用：避免 _STATE / 驱动 / 棋盘在本局返回后仍被闭包链持有
    _STATE.clear()
    return result


# ---------------------------------------------------------------------------
def _run_chunk(cfg, seeds, out_path):
    from sim.eval_harness import install_stubs
    install_stubs(cfg["state_dir"], True)
    rows = []
    for sd in seeds:
        try:
            rows.append(play_one(sd, cfg["w"], cfg["h"], cfg["a"], cfg["arm"],
                                 cfg["limit"], cfg["world_cap"],
                                 cfg["node_cap"]))
        except Exception as e:                              # noqa: BLE001
            rows.append({"seed": sd, "error": f"{type(e).__name__}: {e}"})
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)


def run_parallel(seeds, cfg, nw, state_dir):
    tmp = os.path.join(state_dir, "chunks")
    os.makedirs(tmp, exist_ok=True)
    chunks = [[] for _ in range(nw)]
    for i, s in enumerate(seeds):
        chunks[i % nw].append(s)
    procs = []
    for ci, ch in enumerate(chunks):
        if not ch:
            continue
        cp = os.path.join(tmp, f"cfg{ci}.json")
        sp = os.path.join(tmp, f"seeds{ci}.json")
        op = os.path.join(tmp, f"out{ci}.json")
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(ch, f)
        procs.append((ci, sp, op, cp))
    running = []
    for ci, sp, op, cp in procs:
        lo = open(os.path.join(tmp, f"log{ci}.txt"), "w", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["QT_QPA_PLATFORM"] = "offscreen"
        p = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--chunk", cp, sp, op],
            cwd=ROOT, stdout=lo, stderr=lo, stdin=subprocess.DEVNULL, env=env)
        running.append((p, op, lo, ci))
    results = []
    for p, op, lo, ci in running:
        p.wait()
        lo.close()
        if os.path.exists(op):
            with open(op, encoding="utf-8") as f:
                results.extend(json.load(f))
        else:
            print(f"  ! chunk {ci} 无产出")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--arm", default="baseline")
    ap.add_argument("--w", type=int, default=30)
    ap.add_argument("--h", type=int, default=16)
    ap.add_argument("--a", type=int, default=99)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--world-cap", type=int, default=4096)
    ap.add_argument("--node-cap", type=int, default=200000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk", nargs=3, default=None,
                    metavar=("CFG", "SEEDS", "OUT"))
    args = ap.parse_args()

    state_dir = os.path.join(ROOT, "state", "ab")
    os.makedirs(state_dir, exist_ok=True)

    if args.chunk:
        cp, sp, op = args.chunk
        with open(cp, encoding="utf-8") as f:
            cfg = json.load(f)
        with open(sp, encoding="utf-8") as f:
            seeds = json.load(f)
        _run_chunk(cfg, seeds, op)
        return

    nw = args.workers or max(1, (os.cpu_count() or 4) - 1)
    nw = min(nw, args.games)
    out = args.out or os.path.join(state_dir, f"{args.arm}.json")
    cfg = {"w": args.w, "h": args.h, "a": args.a, "limit": args.limit,
           "arm": args.arm, "state_dir": state_dir,
           "world_cap": args.world_cap, "node_cap": args.node_cap}
    seeds = [args.seed + i for i in range(args.games)]

    print("=" * 84)
    print(f"整局配对 A/B：臂={args.arm}  局数={args.games}  并行={nw}")
    print(f"棋盘 {args.w}x{args.h} {args.a} 雷；world_cap={args.world_cap} "
          f"node_cap={args.node_cap}")
    print("=" * 84)
    t0 = time.time()
    if nw <= 1:
        from sim.eval_harness import install_stubs
        install_stubs(state_dir, True)
        results = []
        for i, sd in enumerate(seeds):
            results.append(play_one(sd, args.w, args.h, args.a, args.arm,
                                    args.limit, args.world_cap, args.node_cap))
            if (i + 1) % max(1, args.games // 10) == 0:
                w = sum(1 for r in results if r.get("won"))
                print(f"  ... {i+1}/{args.games} 胜率 {w/(i+1)*100:.2f}%",
                      flush=True)
    else:
        results = run_parallel(seeds, cfg, nw, state_dir)
    dt = time.time() - t0
    results.sort(key=lambda r: r["seed"])

    ok = [r for r in results if not r.get("error")]
    wins = sum(1 for r in ok if r["won"])
    lo, hi = wilson(wins, len(ok))
    print(f"\n胜率 {wins}/{len(ok)} = {wins/max(1,len(ok))*100:.2f}%  "
          f"95%CI [{lo*100:.2f}%, {hi*100:.2f}%]  耗时 {dt:.1f}s")
    fb = sum(r.get("arm_fallback", 0) for r in ok)
    calls = sum(r.get("arm_calls", 0) for r in ok)
    if calls:
        print(f"臂决策调用 {calls} 次，回退生产 {fb} 次 "
              f"（{fb/calls*100:.1f}%），累计决策耗时 "
              f"{sum(r.get('arm_ms_total',0) for r in ok)/1000:.1f}s，"
              f"单次最大 {max((r.get('arm_ms_max',0) for r in ok), default=0):.0f}ms")
        ws = [r.get("arm_worlds_max", 0) for r in ok]
        print(f"世界数最大 {max(ws) if ws else 0}，中位 {sorted(ws)[len(ws)//2] if ws else 0}")
    errs = [r for r in results if r.get("error")]
    if errs:
        print(f"错误 {len(errs)} 局，例：{errs[0].get('error')}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"arm": args.arm, "games": args.games, "seed": args.seed,
                   "w": args.w, "h": args.h, "a": args.a,
                   "world_cap": args.world_cap, "node_cap": args.node_cap,
                   "won": wins, "n": len(ok),
                   "ci": [lo, hi], "seconds": dt,
                   "results": results}, f, ensure_ascii=False)
    print(f"结果已写 {out}")


if __name__ == "__main__":
    main()
