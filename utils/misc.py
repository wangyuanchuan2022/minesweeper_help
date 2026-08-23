# -*- coding: utf-8 -*-
"""杂项工具：JSON 编码器与棋盘调试打印。

自 utils/util.py 拆分而来。
"""
import json

from logger import get_logger

logger = get_logger(__name__)


class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return str(obj, encoding="utf-8")
        if isinstance(obj, int):
            return int(obj)
        elif isinstance(obj, float):
            return float(obj)
        else:
            return super(MyEncoder, self).default(obj)


def print_board(cell_value):
    """将棋盘以文本形式写入日志（用于调试）。"""
    lines = [
        "".join((2 - len(str(int(i)))) * " " + str(int(i)) for i in row)
        for row in cell_value
    ]
    logger.debug("棋盘:\n%s", "\n".join(lines))
