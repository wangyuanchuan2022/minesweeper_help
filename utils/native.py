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
