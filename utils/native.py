# -*- coding: utf-8 -*-
"""原生 C++ 加速模块加载器（pybind11，带自动回退）。

加载 ``utils/mscore.cp310-win_amd64.pyd``（由 ``cpp/build.bat`` 构建的 C++ 实现，
覆盖 part_solve / part_solve_single / win_rate / pbs_compute 四个热点，
以及三层统一决策 ``decide``）。

回退语义（硬性保证，按层区分——2026-11-07 方案 A 后语义收窄）：
- 任何加载失败（.pyd 缺失 / Python 版本不匹配 / 依赖 DLL 缺失）都在导入期静默吞掉，
  ``available`` 为 False，上层自动使用纯 Python 实现，程序功能不受影响；
- **热点方法层**（part_solve / win_rate / pbs_compute 等）：运行期 C++ 调用抛出的
  任何异常同样由上层（probability.py）捕获并回退到纯 Python 原实现重算，结果与
  纯 Python 完全一致；
- **决策层 ``decide`` 不回退**（用户批准的方案 A 硬裁决）：``decide`` 抛异常、
  返回无效坐标、或 ``has_decide()`` 为 False（旧 pyd / ``MSW_DISABLE_NATIVE=1`` /
  无 decide 导出）时，由 ``utils/probability.py`` 的 ``_decide_unavailable`` 统一
  处理成「ERROR 日志 + ``warning_signal`` + 全盘安全重扫 + 显式 return」，
  **绝不**静默回退到 Python 决策链（自动模式已无第二套决策实现；帮助模式保留的
  ``mine_clear1``/``mine_clear3_1`` 属推理提示，不是决策兜底）。

  ⇒ 后果（必须显式告知）：旧 pyd 备份或 ``MSW_DISABLE_NATIVE=1`` 在自动模式下
  **不再能自动扫雷**，只会每轮报「决策不可用 + 安全重扫」。要恢复旧行为必须同时
  revert 方案 A 的代码改动，只换 .pyd 不够。

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


# 2026-11-10 精简：as_groups / estimate_mem / decision_preflight /
# packed_board_storage 四个包装已删除（生产零调用；对应 C++ 导出
# estimate_mem / decision_preflight / packed_board_storage 已一并移除）。
# 本模块只保留 decide 两层决策链真正需要的包装。


def split_frontier(cell_value):
    """调用 C++ 决策层的前沿/内部格切分；不可用时返回 None。"""
    if mscore is None or not hasattr(mscore, "split_frontier"):
        return None
    return mscore.split_frontier(np.ascontiguousarray(cell_value, dtype=np.int32))


def decide(cell_value, a, w, h, wr_threshold=None, node_cap=None, is_play=True):
    """统一 C++ 决策（OPTIMAL 精确穷举 + pbs/PSEQ）。

    返回 dict：x, y, confidence, path, reason, prob, tied, weighted。
    仅 MSW_TRIAL_DECIDE 构建可用；生产旧 pyd 无此导出时返回 None——**此时调用方
    必须走「决策不可用」路径（``probability._decide_unavailable``），不得回退到
    Python 决策链**（用户批准方案 A）。

    ⚠️ 2026-11-09：``wr_threshold`` 形参**保留以兼容旧调用方**，但**不再转发给
    C++**（用户裁决「winrate 分支都取消了，应该取消这个参数」）。C++ 侧已无该
    参数；它的原语义 `use_win_rate = limitation <= wr_threshold` 随 LAYER 2 删除。
    传了也不生效，且**不再有任何效果**——保留形参只是为了防止既有的 bench/测试
    调用点一次性全部 TypeError。

    node_cap：调用方覆盖（帮助模式放宽用）；缺省走 pybind 默认。
    is_play：False=帮助模式 → C++ OPTIMAL 层 DSTAR ×3（覆盖更大残局）。
    """
    if mscore is None or not hasattr(mscore, "decide"):
        return None
    kwargs = {"is_play": bool(is_play)}
    # wr_threshold 不再转发（C++ 已删该参数）；保留形参仅为兼容
    _ = wr_threshold
    if node_cap is not None:
        kwargs["node_cap"] = int(node_cap)
    try:
        return mscore.decide(cell_value, a=a, w=w, h=h, **kwargs)
    except TypeError as e:
        # 旧 pyd（无 is_play 参数）兼容：去掉新参数重试。旧行为 = 自动模式
        # 基线（DSTAR=400），不影响正确性只影响帮助放宽。
        # ⚠️ 非静默：帮助模式的 DSTAR×3 放宽在此路径失效，必须可见
        # （否则帮助模式「看似放宽实则没生效」的接线问题会再次被掩盖）。
        if "is_play" not in str(e):
            raise
        kwargs.pop("is_play", None)
        logger.warning("native.decide 旧签名兼容回退（pyd 无 is_play 形参）："
                       "帮助模式放宽参数（DSTAR×3）本次不生效")
        return mscore.decide(cell_value, a=a, w=w, h=h, **kwargs)


def has_decide():
    """三层 decide() 是否可用（构建宏 MSW_TRIAL_DECIDE 才导出）。"""
    return mscore is not None and hasattr(mscore, "decide")


def transitions_for(cell_value, a, w, h, x, y):
    """按「鼠标悬停的格子 (x,y)」算真实状态转移（帮助页预览用）。

    C++ 侧复用最近一次 decide 缓存的世界集，命中时只跑 click_outcomes
    （实测 ~1.5ms）；盘面变化会按指纹自动重建。返回 dict 含
    ``transitions`` / ``transition_meta``（含 cell=present 悬停格）/ ``reason``。
    旧 pyd 无该导出时返回 None，调用方应显示准确原因，绝不合成概率。
    """
    if mscore is None or not hasattr(mscore, "transitions_for"):
        return None
    try:
        return mscore.transitions_for(
            np.ascontiguousarray(cell_value, dtype=np.int32),
            a=a, w=w, h=h, x=int(x), y=int(y))
    except Exception as e:  # noqa: BLE001 —— 预览失败不得打断 UI
        logger.debug("transitions_for 失败（忽略）: %s", e)
        return None


def has_transitions_for():
    """是否支持按任意格算状态转移（新 pyd 才有该导出）。"""
    return mscore is not None and hasattr(mscore, "transitions_for")


# ---------------------------------------------------------------------------
# 决策阈值放宽（按实测校准，仅 C++ 可用时生效；纯 Python 自动恢复原阈值）
#
# 原阈值按纯 Python 耗时标定，C++ 提速后在同等墙钟时间预算下可以搜得更深：
#   part_solve   提速 ~424x（test_33, 33 格组 976ms → 2.3ms）→ log2≈8.7，取 8 留余量
#   win_rate     提速 ~6x  （test_4 派生局面 112 板 439ms → 78ms）→ log2≈2.6，取 2
#
# 对比测试需要新旧路径决策一致时，设 MSW_NATIVE_TUNE=0 强制保持原阈值。
# ---------------------------------------------------------------------------
LIMIT_BONUS_WIN_RATE = 2     # win_rate 触发阈值加成（limitation <= 6 → 8）


def tuned():
    """决策阈值是否按 C++ 提速放宽（默认开；MSW_NATIVE_TUNE=0 关闭）。"""
    return available and os.environ.get("MSW_NATIVE_TUNE", "1") != "0"
