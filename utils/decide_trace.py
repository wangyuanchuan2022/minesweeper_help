# -*- coding: utf-8 -*-
"""决策记录点（只观测、不改行为）：把每次 C++ decide 的真实输入/输出落盘。

==================== 为什么需要它 ====================
本会话已用离线数据（模拟器 + 复算）得出几条争议结论：
  · limitation = 0.8*n9 + log2(world_upper) 与真实耗时方向相反（rho≈-0.446）
  · use_win_rate（limitation<=wr_threshold）在生产 6348 点中 0 次触发
  · 内存估算 world_upper*candidates*2304B 漏掉 C(n9, rem) 项 ⇒ 假放行
这些都必须用**真实端到端运行**复核，而不是继续在离线样本上推演。

本模块只做三件事：
  ① 读盘面统计（num9/num10/nF/n9，native.split_frontier 现成可用）
  ② 记录 decide 返回的全部关键字段 + 实测耗时
  ③ 追加写 JSONL（带会话 id 与序号，便于按局分组）

==================== 纪律 ====================
· **零行为影响**：任何异常一律吞掉只记 debug 日志；绝不返回值、绝不改 cell_value。
· **可关闭**：环境变量 MSW_DECIDE_TRACE=0 即全局静默；默认开启。
· **有上限**：文件超过 _MAX_BYTES 后自动停止追加（避免长跑把磁盘写满）。
· **不碰内存**：本模块只做计数与读写小文本，不构造任何世界集合。
"""
import json
import os
import sys
import time

from logger import get_logger

logger = get_logger(__name__)

# 开关：默认开启；MSW_DECIDE_TRACE=0 关闭（用户跑完即可关）
_ENABLED = os.environ.get("MSW_DECIDE_TRACE", "1") != "0"

# 落盘路径：仓库根/state/decide_trace.jsonl（与 data_time.json 同样按 __file__ 定位）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "state", "decide_trace.jsonl")

_MAX_BYTES = 20 * 1024 * 1024     # 20MB 上限，超过后停止追加
# schema 版本（评审 S-17 建议④）：v1=原始字段集；v2=新增 cost_log2/schema 字段。
# 分析器据此区分「旧记录无该字段」与「字段存在但值为 0」。
_SCHEMA_VERSION = 2
_SESSION_ID = time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"
_seq = [0]
_stopped = [False]


def _board_stats(cell_value, a):
    """盘面规模统计；任何失败返回空 dict（绝不影响决策）。

    nF/n9 走 native.split_frontier（C++、O(480)，实测微秒级）；
    不可用时退回纯 numpy 计数（nF/n9 记 -1）。
    """
    out = {}
    try:
        import numpy as np
        cv = np.asarray(cell_value)
        inner = cv[1:-1, 1:-1]
        out["num9"] = int((inner == 9).sum())
        out["num10"] = int((inner == 10).sum())
        out["num11"] = int((inner == 11).sum())
        out["rem_mines"] = int(a) - out["num10"]
    except Exception as e:      # noqa: BLE001
        logger.debug("decide_trace 盘面统计失败（忽略）: %s", e)
        return out
    try:
        from . import native
        if hasattr(native, "split_frontier") and native.mscore is not None:
            sf = native.split_frontier(cell_value)
            out["nF"] = len(sf[0])
            out["n9"] = len(sf[1])
            out["cand"] = 1 + out["nF"] + out["n9"]
    except Exception as e:      # noqa: BLE001
        logger.debug("decide_trace split_frontier 失败（忽略）: %s", e)
    return out


def record(cell_value, a, w, h, r, ms, wr_threshold=None, node_cap=None,
           is_play=None, tag=""):
    """记录一次 decide。**只观不动**：无返回值，异常全吞。

    Args:
        cell_value: decide 输入的棋盘（只读统计，不修改）
        r:          decide 返回的 dict（可能为 None / 无有效坐标）
        ms:         本次 decide 实测墙钟耗时（毫秒）
        wr_threshold/node_cap/is_play: 本次调用传入的参数（用于复现门槛）
        tag:        调用位置标记（"first" 首轮 / "loop" 循环内）
    """
    if not _ENABLED or _stopped[0]:
        return
    try:
        # 体积上限检查（每 200 条检查一次，避免每行都 stat）
        if _seq[0] % 200 == 0 and os.path.exists(_PATH):
            if os.path.getsize(_PATH) > _MAX_BYTES:
                _stopped[0] = True
                logger.warning("decide_trace 已达 %.0fMB 上限，停止追加：%s",
                               _MAX_BYTES / 1e6, _PATH)
                return
        _seq[0] += 1

        rec = {
            # schema 版本号（评审 S-17 建议④）：用于区分「旧记录没有该字段」
            # 与「字段存在但值为 0」。v1=原始字段集；v2=新增 cost_log2 等。
            "schema": _SCHEMA_VERSION,
            "ts": round(time.time(), 3),
            "session": _SESSION_ID,
            "seq": _seq[0],
            "tag": tag,
            "is_play": None if is_play is None else bool(is_play),
            "w": int(w), "h": int(h), "a": int(a),
            "wr_threshold": None if wr_threshold is None else float(wr_threshold),
            "node_cap": None if node_cap is None else int(node_cap),
            "ms": round(float(ms), 3),
        }
        rec.update(_board_stats(cell_value, a))

        # decide 返回值：逐字段白名单，避免把 prob/tied 大对象写进日志
        if isinstance(r, dict):
            for k in ("reason", "x", "y", "path", "confidence",
                      "world_upper", "estimated_bytes", "limitation",
                      # cost_log2 = 前瞻层真实成本量 log2(world_upper × C(n9,rem))。
                      # ⚠️ 2026-11-09 补录（评审 S-17）：该字段是 C++ 前瞻准入门槛
                      # 的唯一输入，此前 C++ 计算并返回、Python 零消费、trace 不落盘
                      # ⇒ 本轮「limitation→cost_log2」门槛改动无法端到端复核。
                      # 此处补录后，分析器可直接验证「前瞻准入是否按真实成本生效」。
                      "cost_log2",
                      # S-18 建议的诊断字段（若 C++ 未来补齐则自动落盘，缺则跳过）
                      "fallback_from", "cost_gate_pass"):
                if k in r:
                    v = r[k]
                    rec[k] = (v if isinstance(v, (int, float, str, bool, type(None)))
                              else str(v))
            for k in ("prob", "tied", "weighted", "transitions"):
                if k in r:
                    try:
                        rec[k + "_len"] = len(r[k]) if r[k] is not None else 0
                    except Exception:      # noqa: BLE001
                        rec[k + "_len"] = None
            # 估算内存 vs 硬预算的比例（用于验证"假放行"）
            eb = r.get("estimated_bytes")
            if isinstance(eb, (int, float)) and eb >= 0:
                rec["mem_pen"] = round(float(eb) / 1610612736.0 * 100.0, 4)
        else:
            rec["reason"] = None
            rec["decide_none"] = True

        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:          # noqa: BLE001 —— 记录失败绝不影响决策
        logger.debug("decide_trace 写入失败（忽略）: %s", e)
