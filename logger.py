# -*- coding: utf-8 -*-
"""统一的日志配置。

用法::

    from logger import get_logger
    logger = get_logger(__name__)
    logger.debug("调试信息")
    logger.info("普通信息")
    logger.warning("警告")
    logger.error("错误")

日志同时写入文件 ``logs/minesweeper.log``（滚动）与控制台（stderr）。
DEBUG 级别仅写入文件，INFO 及以上同时输出到控制台。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_DEFAULT_LOG_FILE = os.path.join("logs", "minesweeper.log")
_configured = False


def setup_logging(log_file=None, level=logging.DEBUG):
    """初始化根 logger（幂等，可重复调用）。"""
    global _configured
    if _configured:
        return logging.getLogger()

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 文件处理器：滚动，避免日志无限增长
    log_file = log_file or _DEFAULT_LOG_FILE
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # 文件不可写时退化为仅控制台输出

    # 控制台处理器：仅 INFO 及以上，避免刷屏
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    _configured = True
    return root


def get_logger(name=None):
    """获取指定名称的 logger（首次调用时自动完成基本配置）。"""
    setup_logging()
    return logging.getLogger(name)


# 模块导入即完成基本配置，保证独立脚本（如测试）也能直接使用
setup_logging()
