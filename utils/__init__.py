import pyautogui

import setting

# 全局点击节奏配置（原为 utils/util.py 的模块级副作用，拆分后集中于此；
# 导入 utils 包或其任意子模块时都会先执行本文件，因此行为不变）。
pyautogui.PAUSE = setting.sleep
pyautogui.MINIMUM_SLEEP = 0.001

from .mm0 import (
    minesweeper_run,
    ClientToScreen,
    ScreenToClient,
    GetMousePosition,
    MouseWindowTread,
    set_top_window
)
from .solver import Solver
