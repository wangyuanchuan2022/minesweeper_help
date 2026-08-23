# -*- coding: utf-8 -*-
"""测试辅助工具：Solver 构造、测试数据加载、棋盘构造。"""
import json
import os

import numpy as np

from utils.util import Solver

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def make_solver(w=5, h=5, a=3, is_play=False):
    """构造一个「轻量」Solver 实例，用于不发射信号的纯逻辑方法。

    使用 ``Solver.__new__`` 跳过 ``__init__``（避免读取 cfg.json 与加载模板图片），
    并手动初始化被测方法所需的属性。
    """
    solver = Solver.__new__(Solver)
    solver.w = w
    solver.h = h
    solver.a = a
    solver.is_play = is_play
    solver.cell_width = 24
    solver.screenshot_w = int(solver.cell_width * 5 / 9)
    solver.screenshot_h = int(solver.cell_width * 7 / 9)
    solver.pos_dict_list = []
    solver.appended_pos = set()
    solver.num = 0
    solver.checked = {}
    solver.memory = {}
    solver.cell_value = None
    solver.img = None
    solver.bx = 0
    solver.by = 0
    solver._bx = 0
    solver._by = 0
    solver.count = 0
    solver.p = (a / (w * h)) if (w * h) else 0.0
    solver.images = None
    solver._images_cell_width = None
    solver._images_mtime = None
    solver._locate_templates = {}
    solver.stats_data = None
    solver._stats_updates = 0
    return solver


def make_full_solver(w=5, h=5, a=3):
    """构造一个「完整」Solver 实例（含 Qt 信号），用于会发射信号的方法。

    这些方法（number5_1/part_solve/win_rate 等）依赖 QThread 初始化出的 pyqtSignal。
    """
    solver = Solver()
    solver.w = w
    solver.h = h
    solver.a = a
    solver.is_play = False
    solver.pos_dict_list = []
    solver.appended_pos = set()
    solver.num = 0
    solver.checked = {}
    solver.cell_value = None
    return solver


def make_board(h, w, fill=0):
    """构造带零边框的 (h+2, w+2) 棋盘，内部区域填 fill。"""
    board = np.full((h + 2, w + 2), fill, dtype=np.int32)
    board[0, :] = 0
    board[-1, :] = 0
    board[:, 0] = 0
    board[:, -1] = 0
    return board


def load_test_data(name):
    """加载 tests/data 目录下的 JSON 测试数据。"""
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)
