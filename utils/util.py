# Minesweeper Arbiter
# -*- coding: utf-8 -*-
"""兼容层：原 ``utils/util.py``（2113 行）已按职责拆分，本文件只做 re-export，
保持 ``from utils.util import ...`` 的旧导入路径继续可用。

拆分后的模块：

- ``utils.combinatorics``: 组合数学工具（C、C_num、get_list、A、p_of_c、combination_ratio 等）
- ``utils.clicking``:      点击顺序优化（sort_clicks）
- ``utils.misc``:          杂项（MyEncoder、print_board）
- ``utils.vision``:        视觉识别（BoardVisionMixin：模板加载、截图、扫描）
- ``utils.deduction``:     基础规则推理（DeductionMixin：number0、number_3_1 等）
- ``utils.probability``:   概率枚举与胜率（ProbabilityMixin：number5_1、part_solve、win_rate 等）
- ``utils.solver``:        AutoPlayThread 与 Solver（play/help 主循环）

原先在本模块执行的 ``pyautogui.PAUSE`` 全局配置已移至 ``utils/__init__.py``。
"""
from .combinatorics import (
    C,
    C_num,
    get_list,
    get_index_from_list,
    A,
    p_of_c,
    combination_ratio,
)
from .clicking import sort_clicks
from .misc import MyEncoder, print_board
from .solver import AutoPlayThread, Solver

__all__ = [
    "C",
    "C_num",
    "get_list",
    "get_index_from_list",
    "A",
    "p_of_c",
    "combination_ratio",
    "sort_clicks",
    "MyEncoder",
    "print_board",
    "AutoPlayThread",
    "Solver",
]
