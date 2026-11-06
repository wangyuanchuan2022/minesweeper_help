# -*- coding: utf-8 -*-
"""原生 C++ 加速模块加载器（pybind11，带自动回退）。

加载 ``utils/mscore.cp310-win_amd64.pyd``（由 ``cpp/build.bat`` 构建的 C++ 实现，
覆盖 part_solve / part_solve_single / win_rate / pbs_compute 四个热点）。

回退语义（硬性保证）：
- 任何加载失败（.pyd 缺失 / Python 版本不匹配 / 依赖 DLL 缺失）都在导入期静默吞掉，
  ``available`` 为 False，上层自动使用纯 Python 实现，程序功能不受影响；
- 运行期 C++ 调用抛出的任何异常同样由上层（probability.py）捕获并回退到纯 Python
  原实现重算，结果与纯 Python 完全一致。

环境变量 ``MSW_DISABLE_NATIVE=1`` 可强制禁用（用于新旧实现的对比测试）。
"""
import os

import numpy as np

try:
    from logger import get_logger
except ImportError:  # 允许脱离项目单独导入（如测试环境）
    import logging

    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger(__name__)

mscore = None          # C++ 模块（不可用时为 None）
available = False      # 是否可用
_load_error = None

if os.environ.get("MSW_DISABLE_NATIVE"):
    _load_error = "disabled by MSW_DISABLE_NATIVE"
else:
    try:
        from . import mscore as _mscore_mod
        mscore = _mscore_mod
        available = True
    except Exception as e:  # ImportError 及任何依赖/ABI 问题 → 静默回退
        mscore = None
        available = False
        _load_error = e


def as_groups(res_list):
    """将 res_list 规整为 C++ 可接受的 int32 连续 2D 矩阵列表。

    兼容多种历史形态：C++ part_solve 返回的 2D 解矩阵、纯 Python 实现返回的
    「1D 数组列表」（可能被 self.checked 缓存）、混合回退产物。
    """
    if mscore is None:
        return res_list
    return [np.ascontiguousarray(g, dtype=np.int32) for g in res_list]


def report_load_error():
    """供诊断：打印加载失败原因（仅日志，不影响运行）。"""
    if _load_error is not None:
        logger.info("C++ 加速模块不可用，使用纯 Python 实现：%s", _load_error)


def estimate_mem(worlds, candidates, budget=1536 * 1024 * 1024):
    """调用 C++ 决策层的保守内存预检；不可用时返回 None。"""
    if mscore is None or not hasattr(mscore, "estimate_mem"):
        return None
    return mscore.estimate_mem(int(worlds), int(candidates), int(budget))


def split_frontier(cell_value):
    """调用 C++ 决策层的前沿/内部格切分；不可用时返回 None。"""
    if mscore is None or not hasattr(mscore, "split_frontier"):
        return None
    return mscore.split_frontier(np.ascontiguousarray(cell_value, dtype=np.int32))


def decision_preflight(cell_value, worlds, budget=1536 * 1024 * 1024):
    """原子执行前沿切分与内存闸门，避免调用方漏过预检。"""
    if mscore is None or not hasattr(mscore, "decision_preflight"):
        return None
    return mscore.decision_preflight(
        np.ascontiguousarray(cell_value, dtype=np.int32), int(worlds), int(budget)
    )


def packed_board_storage(cells):
    """返回 C++ 4-bit Board 的 payload/对象容量诊断。"""
    if mscore is None or not hasattr(mscore, "packed_board_storage"):
        return None
    return mscore.packed_board_storage(int(cells))


def decide(cell_value, a, w, h):
    """统一 C++ 三层决策（OPTIMAL 精确穷举 / 自适应前瞻 / pbs+PSEQ）。

    返回 dict：x, y, confidence, path, reason, prob, tied, weighted。
    仅 MSW_TRIAL_DECIDE 构建可用；生产旧 pyd 无此导出时返回 None。
    """
    if mscore is None or not hasattr(mscore, "decide"):
        return None
    return mscore.decide(cell_value, a=a, w=w, h=h)


def has_decide():
    """三层 decide() 是否可用（构建宏 MSW_TRIAL_DECIDE 才导出）。"""
    return mscore is not None and hasattr(mscore, "decide")


# ---------------------------------------------------------------------------
# 决策阈值放宽（按实测校准，仅 C++ 可用时生效；纯 Python 自动恢复原阈值）
#
# 原阈值按纯 Python 耗时标定，C++ 提速后在同等墙钟时间预算下可以搜得更深：
#   part_solve   提速 ~424x（test_33, 33 格组 976ms → 2.3ms）→ log2≈8.7，取 8 留余量
#   win_rate     提速 ~6x  （test_4 派生局面 112 板 439ms → 78ms）→ log2≈2.6，取 2
#
# 对比测试需要新旧路径决策一致时，设 MSW_NATIVE_TUNE=0 强制保持原阈值。
# ---------------------------------------------------------------------------
LIMIT_BONUS_PART_SOLVE = 8   # part_solve 组枚举上限加成（等墙钟时间预算）
LIMIT_BONUS_WIN_RATE = 2     # win_rate 触发阈值加成（limitation <= 6 → 8）


def tuned():
    """决策阈值是否按 C++ 提速放宽（默认开；MSW_NATIVE_TUNE=0 关闭）。"""
    return available and os.environ.get("MSW_NATIVE_TUNE", "1") != "0"
