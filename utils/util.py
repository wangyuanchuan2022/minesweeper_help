# Minesweeper Arbiter
import json
import math
import random
import time
from collections import defaultdict

import networkx as nx
from PIL import ImageGrab
import numpy as np
import pyautogui
import win32gui
import win32ui
import cv2 as cv
from PyQt5 import QtCore
from PyQt5.QtCore import QThread

import setting
from .mm0 import ClientToScreen

pyautogui.PAUSE = setting.sleep
pyautogui.MINIMUM_SLEEP = 0.001


def sort_clicks(clicks, cs):
    if len(cs) <= 1:
        return clicks

    G = nx.Graph()
    for i in range(len(cs)):
        x0, y0 = cs[i]
        for j in range(i + 1, len(cs)):
            x1, y1 = cs[j]
            if abs(x0 - x1) <= 2 and abs(y0 - y1) <= 2:
                G.add_edge((x0, y0), (x1, y1), weight=abs(x0 - x1) + abs(y0 - y1))
    mst = nx.minimum_spanning_tree(G)

    flag = defaultdict(bool)

    def dfs(pos, mst, sorted_clicks):
        if flag[pos]:
            return sorted_clicks
        flag[pos] = True
        i, j = pos
        for u in range(i - 1, i + 2):
            for v in range(j - 1, j + 2):
                if (u, v) in clicks and (u, v) not in sorted_clicks:
                    sorted_clicks.append((u, v))

        if len(sorted_clicks) == len(clicks):
            return sorted_clicks

        for _p in mst.neighbors(pos):
            sorted_clicks = dfs(_p, mst, sorted_clicks)
        return sorted_clicks

    degree_one_nodes = [node for node in mst.nodes() if mst.degree(node) == 1 or mst.degree(node) == 0]
    sorted_clicks = dfs(degree_one_nodes[0], mst, [])
    return sorted_clicks


# 0：已开方格
# 1~8 ： 1~8
# 9：未开方格
# 10：雷
def C(a, b, start_from=None):
    """
    从a中选出b个数
    :param start_from: array
    :param a: must be larger than
    :param b:
    :return: list

    Examples
    --------
    >>> for i in C(4, 2):
    ...     print(i)
    [0, 1]
    [0, 2]
    [0, 3]
    [1, 2]
    [1, 3]
    [2, 3]
    """
    ck = range(a - b, a)
    num = list(range(b))
    if start_from is not None:
        num = start_from
    while True:
        yield num
        num[-1] += 1
        for i in range(b):
            i *= -1
            if num[i] > ck[i]:
                num[i - 1] += 1
        if num[0] > ck[0]:
            break

        for i in range(b):
            if num[i] > ck[i]:
                num[i] = num[i - 1] + 1


def C_num(a, b):
    result = 1
    for i in range(b):
        result *= a - i
        result /= i + 1
    return result


def get_list(a, num, listnum, start=0, stop=-1):
    """
    :param stop:
    :param start:
    :param a: 小于num的正整数
    :param num: 小于listnum的正整数
    :param listnum: 列表的长度
    :return: 索引组成的列表

    Examples
    --------
    >>> for i in get_list(1, 4, 6):
    ...     print(i)
    [0]
    [1]
    [2]
    [3]
    [4]
    [5]
    [0, 1]
    [0, 2]
    [0, 3]
    [0, 4]
    [0, 5]
    [1, 2]
    [1, 3]
    [1, 4]
    [1, 5]
    [2, 3]
    [2, 4]
    [2, 5]
    [3, 4]
    [3, 5]
    [4, 5]
    [0, 1, 2]
    [0, 1, 3]
    [0, 1, 4]
    [0, 1, 5]
    [0, 2, 3]
    [0, 2, 4]
    [0, 2, 5]
    [0, 3, 4]
    [0, 3, 5]
    [0, 4, 5]
    [1, 2, 3]
    [1, 2, 4]
    [1, 2, 5]
    [1, 3, 4]
    [1, 3, 5]
    [1, 4, 5]
    [2, 3, 4]
    [2, 3, 5]
    [2, 4, 5]
    [3, 4, 5]
    [0, 1, 2, 3]
    [0, 1, 2, 4]
    [0, 1, 2, 5]
    [0, 1, 3, 4]
    [0, 1, 3, 5]
    [0, 1, 4, 5]
    [0, 2, 3, 4]
    [0, 2, 3, 5]
    [0, 2, 4, 5]
    [0, 3, 4, 5]
    [1, 2, 3, 4]
    [1, 2, 3, 5]
    [1, 2, 4, 5]
    [1, 3, 4, 5]
    [2, 3, 4, 5]
    """
    if a < 1:
        a = 1

    if num > listnum - 1:
        num = listnum - 1

    if num < 1:
        num = 1

    if num < a:
        a = num

    total = [0]
    for i in range(a, num + 1):
        total.append(total[-1] + C_num(listnum, i))
    yield total[-1]
    if stop == -1:
        stop = total[-1]
    start_index = a + get_index_from_list(start, total) - 1
    returned = 0
    left_num = start - total[start_index - a]
    counter = 0
    for c in C(listnum, start_index):
        if counter >= left_num:
            yield c.copy()
            returned += 1
        counter += 1
        if returned >= stop - start:
            break

    for i in range(start_index + 1, num + 1):
        for c in C(listnum, i):
            if returned >= stop - start:
                break
            yield c.copy()
            returned += 1


def get_index_from_list(num, _list):
    for i in range(len(_list)):
        if num < _list[i]:
            return i
    return -1


def A(ck: list):
    """
    全排列
    :param ck: 列表中每一项长度
    :return: 索引组成的列表

    Examples
    --------
    >>> for i in A([2,1,3]):
    ...     print(i)
    [0 0 0]
    [0 0 1]
    [0 0 2]
    [1 0 0]
    [1 0 1]
    [1 0 2]
    """
    num = np.zeros(len(ck), dtype=np.int32)
    while True:
        yield num
        num[-1] += 1
        for i in range(len(ck)):
            i *= -1
            if num[i] >= ck[i]:
                num[i - 1] += 1
        if num[0] >= ck[0]:
            break

        for i in range(len(ck)):
            if num[i] >= ck[i]:
                num[i] = 0


def p_of_c(x: int, n: int):
    x = int(x)
    n = int(n)
    if n == 0:
        return 1
    assert n >= x >= 0
    k = n // 2
    if x >= k:
        x = n - x
    res = 1
    for i in range(k - x):
        res *= x + 1 + i
        res /= n - x - i
    return res


class AutoPlayThread(QThread):
    pv_signal = QtCore.pyqtSignal(int)
    text_signal = QtCore.pyqtSignal(str)
    Visible_signal = QtCore.pyqtSignal(bool)
    warning_signal = QtCore.pyqtSignal(str)
    update_btn_list_signal = QtCore.pyqtSignal(list)
    start_signal = QtCore.pyqtSignal(tuple)
    end_signal = QtCore.pyqtSignal(str)
    warning_signal_2 = QtCore.pyqtSignal(str)

    def __init__(self):
        super(AutoPlayThread, self).__init__()
        self.value = None
        self.is_play = True

    def set_args(self, value):
        self.value = value
        self.is_play = self.value != 0


class Solver(AutoPlayThread):
    def __init__(self):
        super().__init__()
        self.images = None
        self.by = None
        self.bx = None
        self.num = 0
        self.cell_value = None
        self.img = None

        self.count = 0
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]  #
        self._by = self.cfg["by"]  #
        self.cell_width = self.cfg["cell_width"]
        self.a = self.cfg["a"]  # 总雷数
        self.limit = self.cfg["limit"]
        self.p = self.a / (self.w * self.h)
        self.speed = self.cfg["speed"]

        self.screenshot_h = int(self.cell_width * 7 / 9)
        self.screenshot_w = int(self.cell_width * 5 / 9)
        self.load_img()

        self.pos_dict_list = []
        self.appended_pos = set()

        self.checked = {}

    def run(self):
        self.reload()
        if self.is_play:
            self.play(self.value)
        else:
            self.help()

    def reload(self):
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]  #
        self._by = self.cfg["by"]  #
        self.cell_width = self.cfg["cell_width"]
        self.a = self.cfg["a"]  # 总雷数
        self.limit = self.cfg["limit"]
        self.speed = self.cfg["speed"]

        self.screenshot_h = int(self.cell_width * 7 / 9)
        self.screenshot_w = int(self.cell_width * 5 / 9)
        self.load_img()

    def load_img(self):
        img0 = cv.imread("image/0.bmp")
        img0_1 = cv.imread("image/0_1.bmp")
        img0_2 = cv.imread("image/0_2.bmp")
        img1 = cv.imread("image/1.bmp")
        img1_1 = cv.imread("image/1_1.bmp")
        img1_2 = cv.imread("image/1_2.bmp")
        img2 = cv.imread("image/2.bmp")
        img2_1 = cv.imread("image/2_1.bmp")
        img3 = cv.imread("image/3.bmp")
        img3_1 = cv.imread("image/3_1.bmp")
        img4 = cv.imread("image/4.bmp")
        img4_1 = cv.imread("image/4_1.bmp")
        img5 = cv.imread("image/5.bmp")
        img5_1 = cv.imread("image/5_1.bmp")
        img6 = cv.imread("image/6.bmp")
        img6_1 = cv.imread("image/6_1.bmp")
        img7 = cv.imread("image/7.bmp")
        img7_1 = cv.imread("image/7_1.bmp")
        img8 = cv.imread("image/8.bmp")
        img8_1 = cv.imread("image/8_1.bmp")
        img9 = cv.imread("image/9.bmp")
        img9_1 = cv.imread("image/9_1.bmp")
        img9_2 = cv.imread("image/9_2.bmp")
        img10 = cv.imread("image/10.bmp")
        img10_1 = cv.imread("image/10_1.bmp")
        self.images = [
            [img0, img0_1, img0_2],
            [img1, img1_1, img1_2],
            [img2, img2_1],
            [img3, img3_1],
            [img4, img4_1],
            [img5, img5_1],
            [img6, img6_1],
            [img7, img7_1],
            [img8, img8_1],
            [img9, img9_1, img9_2],
            [img10, img10_1],
        ]

    @staticmethod
    def locate_exit():
        tem = cv.imread("exit.bmp")
        h, w, _ = tem.shape
        bg = ImageGrab.grab()
        bg = np.array(bg)
        bg = cv.cvtColor(bg, cv.COLOR_RGB2BGR)
        res = cv.matchTemplate(bg, tem, cv.TM_SQDIFF_NORMED)
        x, y = cv.minMaxLoc(res)[2]
        x, y = x + w / 2, y + w / 2
        return x, y

    @staticmethod
    def _locate(f):
        tem = cv.imread(f)
        h, w, _ = tem.shape
        bg = ImageGrab.grab()
        bg = np.array(bg)
        bg = cv.cvtColor(bg, cv.COLOR_RGB2BGR)
        res = cv.matchTemplate(bg, tem, cv.TM_SQDIFF_NORMED)
        _min = np.min(res)
        x, y = cv.minMaxLoc(res)[2]
        x, y = x + w / 2, y + h / 2
        return _min < 0.015, x, y

    def play(self, limit):
        try:
            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 开始时点击的坐标
            start_i = int(w / 2)
            start_j = int(h / 2)
            # start_i = 1
            # start_j = 1

            pyautogui.click(
                self.bx + start_i * self.cell_width, self.by + start_j * self.cell_width
            )
            time.sleep(setting.sleep)

            # 初始化cell_value
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9

            win = 0
            total = 0

            while True:
                if self.count >= 3:
                    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                    self.count = 0
                    return

                if win32gui.FindWindow(None, "游戏胜利") > 0:
                    time.sleep(3)
                    win += 1
                    total += 1
                    exit_i, exit_j = self.locate_exit()
                    pyautogui.click(exit_i, exit_j)
                    self.text_signal.emit(
                        f"已玩 {str(total)} 局。 {str(win)} 局获胜。胜率："
                        f"{str(round(win / total, 4) * 100)}%\n"
                    )
                    if total == limit:
                        break
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked

                    time.sleep(0.1)

                elif win32gui.FindWindow(None, "游戏失败") > 0:
                    time.sleep(3)
                    exit_i, exit_j = self.locate_exit()
                    pyautogui.click(exit_i, exit_j)
                    total += 1
                    self.text_signal.emit(
                        f"已玩 {str(total)} 局。 {str(win)} 局获胜。胜率："
                        f"{str(round(win / total, 4) * 100)}%\n"
                    )
                    if total == limit:
                        break
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked

                _ok, x, y = self._locate("./image/ok.png")
                if _ok:
                    print("ok", x, y)
                    pyautogui.click(x, y)
                    time.sleep(0.1)
                    _, x, y = self._locate("./image/exit.png")
                    pyautogui.click(x, y)
                    time.sleep(0.1)

                _win, x, y = self._locate("./image/win.bmp")
                if _win:
                    time.sleep(3)
                    win += 1
                    total += 1
                    pyautogui.click(x, y)
                    self.text_signal.emit(
                        f"已玩 {str(total)} 局。 {str(win)} 局获胜。胜率："
                        f"{str(round(win / total, 4) * 100)}%\n"
                    )
                    if total == limit:
                        break
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked

                    time.sleep(0.1)

                _lose, x, y = self._locate("./image/lose.bmp")
                if _lose:
                    time.sleep(3)
                    pyautogui.click(x, y)
                    total += 1
                    self.text_signal.emit(
                        f"已玩 {str(total)} 局。 {str(win)} 局获胜。胜率："
                        f"{str(round(win / total, 4) * 100)}%\n"
                    )
                    if total == limit:
                        break
                    cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                    for i in range(1, w + 1):
                        for j in range(1, h + 1):
                            cell_value[j, i] = 9
                    time.sleep(1.0)
                    hwnd = win32gui.FindWindow(None, setting.win_name)
                    win32gui.ShowWindow(hwnd, 1)
                    time.sleep(0.5)
                    pyautogui.click(
                        self.bx + start_i * self.cell_width,
                        self.by + start_j * self.cell_width,
                    )
                    self.checked = {}  # 重置checked

                    time.sleep(0.1)

                cell_value = self.complete_scan(cell_value, True)
                sum2 = np.sum(cell_value)
                try:
                    cell_value = self.mine_clear1(cell_value)
                except ValueError:
                    continue
                cell_value = self.mine_clear3_1(cell_value)
                sum3 = np.sum(cell_value)
                if sum3 == sum2:
                    try:
                        cell_value = self.number5_1(cell_value)
                    except ImportError:
                        pass

        except pyautogui.FailSafeException:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return

    def help(self):
        try:
            self.num = 1
            self.checked = {}
            self.pos_dict_list = []
            self.appended_pos = set()

            hwnd = win32gui.FindWindow(None, setting.win_name)
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)

            w = self.w
            h = self.h

            # 初始化cell_value
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9

            self.cell_value = self.complete_scan(cell_value.copy(), False)
            try:
                for _ in range(2):
                    cell_value = self.complete_scan(cell_value)
                    cell_value = self.mine_clear1(cell_value)
                    cell_value = self.mine_clear3_1(cell_value)
                cell_value = self.mine_clear1(cell_value)
            except ValueError:
                self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                self.Visible_signal.emit(False)
                return

            if len(self.pos_dict_list) == 0:
                flag = 4
                for flag in range(5):
                    self.num += 1
                    cell_value = self.number5_1(cell_value)
                    if len(self.pos_dict_list) != 0:
                        flag = 0
                        break
                if flag == 4:
                    self.warning_signal.emit("请检查设置中总雷数，宽度，长度是否输入正确")
                    self.Visible_signal.emit(False)
                    return

            self.update_btn_list_signal.emit(self.pos_dict_list)

        except pyautogui.FailSafeException:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return
        except win32ui.error:
            self.pv_signal.emit(0)
            self.Visible_signal.emit(False)
            return

    def try_solve(self, i, j, cell_value, clicks, num9, num10):
        res = 0

        for mine_num in range(num10, num10 + num9 + 1):
            if mine_num == 0:
                res += 8 * (1 - self.p) ** num9
                continue
            cell_value[j, i] = mine_num

            appended_pos = set()

            try:
                for _ in range(2):
                    cell_value = self.mine_clear1(cell_value)
                    cell_value = self.mine_clear3_1(cell_value)
                cell_value = self.mine_clear1(cell_value)
            except:
                pass

            # 计算结果
            res += (
                    len(appended_pos)
                    * (1 - self.p) ** (num9 - mine_num + num10)
                    * (self.p) ** (mine_num - num10)
                    * C_num(num9, mine_num - num10)
            )

        res /= num9 + 1
        return res

    def cell_screenshot(self, i, j):
        x = i * self.cell_width
        y = j * self.cell_width
        w = self.screenshot_w
        h = self.screenshot_h
        img = self.img[
              y - self.cell_width // 2 - h // 2: y - self.cell_width // 2 + h // 2,
              x - self.cell_width // 2 - w // 2: x - self.cell_width // 2 + w // 2,
              :,
              ]

        return img

    def cell_around(self, i, j, cell_value):
        cnt9 = 0
        cnt10 = 0
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if cell_value[n, m] == 9:
                        cnt9 += 1
                    if cell_value[n, m] == 10:
                        cnt10 += 1
        return cnt9, cnt10

    def mine_clear1(self, cell_value, clicks=None):
        if clicks:
            for i, j in clicks:
                cell_value = self.number0(i, j, cell_value)
            return cell_value

        for j in range(1, self.h + 1):
            if (
                    (9 in cell_value[j - 1])
                    or (9 in cell_value[j])
                    or (9 in cell_value[j + 1])
            ):
                for i in range(1, self.w + 1):
                    if 0 < cell_value[j, i] < 8:
                        cell_value = self.number0(i, j, cell_value)

        return cell_value

    def number0(self, i, j, cell_value):
        c = False
        cnt9, cnt10 = self.cell_around(i, j, cell_value)
        if not cnt10 <= cell_value[j, i] <= cnt9 + cnt10:
            raise ValueError("识别错误")
        if (cnt9 + cnt10) == cell_value[j, i] and cnt9 != 0:
            for n in range(j - 1, j + 2):
                for m in range(i - 1, i + 2):
                    if cell_value[n, m] == 9:
                        cell_value[n, m] = 10
                        if not self.is_play:
                            if (
                                    tuple((m, n)) not in self.appended_pos
                                    and self.cell_value[n, m] != 10
                            ):
                                c = True
                                self.pos_dict_list.append(
                                    {
                                        "pos": (m, n),
                                        "confidence": 0,
                                        "num": self.num,
                                        "is_mine": True,
                                        "is_best": False,
                                        "exp": f"由({i}, {j})得出",
                                        "is_recommend": False,
                                    }
                                )
                                self.appended_pos.add(tuple((m, n)))

        elif cnt10 == cell_value[j, i] and cnt9 >= 1:
            for n in range(j - 1, j + 2):
                for m in range(i - 1, i + 2):
                    if cell_value[n, m] == 9:
                        if self.is_play:
                            c = True
                            pyautogui.click(
                                self.bx + m * self.cell_width,
                                self.by + n * self.cell_width,
                            )
                        else:
                            if tuple((m, n)) not in self.appended_pos:
                                c = True
                                cell_value[n, m] = 11
                                self.pos_dict_list.append(
                                    {
                                        "pos": (m, n),
                                        "confidence": 1,
                                        "num": self.num,
                                        "is_mine": False,
                                        "is_best": True,
                                        "exp": f"由({i}, {j})得出",
                                        "is_recommend": False,
                                    }
                                )
                                self.appended_pos.add(tuple((m, n)))
        if c:
            if not self.is_play:
                self.num += 1
            else:
                cell_value = self.small_square_scan(i, j, cell_value)

        return cell_value

    def number_3_1(self, i, j, cell_value):
        x1 = cell_value[j, i]
        a, cnt10 = self.get_set(i, j, cell_value)
        x1 -= cnt10
        if x1 <= 0:
            return cell_value
        for x in range(i - 2, i + 3):
            for y in range(j - 2, j + 3):
                x2 = cell_value[y, x]
                if y > self.h or y < 1 or x > self.w or x < 1 or (x == i and y == j):
                    continue
                elif 0 < x2 < 8:
                    c = False
                    b, cnt10_x2 = self.get_set(x, y, cell_value)
                    x2 -= cnt10_x2
                    if x2 < 0:
                        continue
                    bj = a | b
                    x_set = bj - b
                    z_set = bj - a
                    y_set = a & b
                    if x1 - x2 == len(x_set):
                        for u, v in x_set:
                            cell_value[v, u] = 10
                            if not self.is_play:
                                if (
                                        tuple((u, v)) not in self.appended_pos
                                        and self.cell_value[v, u] != 10
                                ):
                                    c = True
                                    self.pos_dict_list.append(
                                        {
                                            "pos": (u, v),
                                            "confidence": 0,
                                            "num": self.num,
                                            "is_mine": True,
                                            "is_best": False,
                                            "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                   f"{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                   f"中至多有{x2}个雷，{list(x_set | y_set)}中"
                                                   f"有{x1}个雷，所以{list(x_set)}是雷。",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple((u, v)))

                        for u, v in z_set:
                            if cell_value[v, u] == 9:
                                if self.is_play:
                                    pyautogui.click(
                                        self.bx + u * self.cell_width,
                                        self.by + v * self.cell_width,
                                    )
                                    c = True
                                else:
                                    cell_value[v, u] = 11
                                    if tuple((u, v)) not in self.appended_pos:
                                        c = True
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (u, v),
                                                "confidence": 1,
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": True,
                                                "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                       f"{str(f'已经可以判断出{list(x_set)}是雷。') if len(x_set) != 0 else str('')}"
                                                       f"现在{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                       f"中有{x2}个雷，{list(z_set | y_set)}中有"
                                                       f"{x2}个雷，所以{list(z_set)}不是雷。",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple((u, v)))

                    if x2 - x1 == len(z_set):
                        for u, v in z_set:
                            cell_value[v, u] = 10
                            if not self.is_play:
                                if (
                                        tuple((u, v)) not in self.appended_pos
                                        and self.cell_value[v, u] != 10
                                ):
                                    c = True
                                    self.pos_dict_list.append(
                                        {
                                            "pos": (u, v),
                                            "confidence": 0,
                                            "is_mine": True,
                                            "num": self.num,
                                            "is_best": False,
                                            "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                   f"{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                   f"中至多有{x1}个雷，{list(z_set | y_set)}中"
                                                   f"有{x2}个雷，所以{list(z_set)}是雷。",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple((u, v)))
                        for u, v in x_set:
                            if cell_value[v, u] == 9:
                                if self.is_play:
                                    pyautogui.click(
                                        self.bx + u * self.cell_width,
                                        self.by + v * self.cell_width,
                                    )
                                    c = True
                                else:
                                    cell_value[v, u] = 11
                                    if tuple((u, v)) not in self.appended_pos:
                                        c = True
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (u, v),
                                                "confidence": 1,
                                                "is_mine": False,
                                                "num": self.num,
                                                "is_best": True,
                                                "exp": f"由({i}, {j}), ({x}, {y})得出\n"
                                                       f"{str(f'已经可以判断出{list(z_set)}是雷。') if len(z_set) != 0 else str('')}"
                                                       f"现在{list(y_set) if len(y_set) > 0 else str('公共区域')}"
                                                       f"中有{x1}个雷，{list(x_set | y_set)}中有"
                                                       f"{x1}个雷，所以{list(x_set)}不是雷。",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple((u, v)))

                    if c:
                        if not self.is_play:
                            self.num += 1
                        else:
                            cell_value = self.small_square_scan(
                                i + 1, j + 1, cell_value
                            )

        return cell_value

    def mine_clear3_1(self, cell_value, clicks=None):
        if clicks:
            for i, j in clicks:
                cell_value = self.number_3_1(i, j, cell_value)
            return cell_value
        for i in range(2, self.w):
            for j in range(2, self.h):
                if 0 < cell_value[j, i] < 8:
                    if self.cell_around(i, j, cell_value)[0] > 0:
                        cell_value = self.number_3_1(i, j, cell_value)
        return cell_value

    def number5_1(self, cell_value):
        """
        5.1 数字统计
        :param cell_value:格子值
        :return:
        """
        confidence = 0
        w = cell_value.shape[1] - 2
        h = cell_value.shape[0] - 2

        num9 = 0  # 所有未开方格的数量
        num10 = 0
        for index in range(1, w + 1):
            for j in range(1, h + 1):
                if cell_value[j, index] == 10:
                    num10 += 1

        # 找到所有未开方格
        bg = np.zeros((h, w), dtype=np.uint8)
        for index in range(w):
            for j in range(h):
                if (
                        cell_value[j + 1, index + 1] == 9
                        or cell_value[j + 1, index + 1] == 10
                ):
                    bg[j, index] = 255

        # 计算255到0的最小距离，以找到最边缘的9与10
        res = cv.distanceTransform(bg, cv.DIST_L12, 0)  # cv.DIST_L12: 平方相加开根号。

        clicks = []  # 外围未开方格
        clicks9 = []  # 内部未开方格

        for i in range(w):
            for j in range(h):
                if cell_value[j + 1, i + 1] == 9:
                    num9 += 1
                if res[j, i] <= 1.5 and cell_value[j + 1, i + 1] == 9:
                    clicks.append(tuple((i + 1, j + 1)))
                elif res[j, i] > 1.5 and cell_value[j + 1, i + 1] == 9:
                    clicks9.append(tuple((i + 1, j + 1)))
        if len(clicks9) == 0 and len(clicks) == 0:  # 识别错误或没检测到胜利/失败窗口
            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
            for i in range(1, w + 1):
                for j in range(1, h + 1):
                    cell_value[j, i] = 9
            cell_value = self.complete_scan(cell_value)
            self.count += 1
            return cell_value

        if len(clicks) == 0:  # 没有可以判断的格子
            total = 0
            res = []
            pos = [random.choice(clicks9)]  # 随机选择
            confidence = 1 - (self.a - num10) / len(clicks9)  # 不是雷的概率
            self.pos_dict_list.append(
                {
                    "pos": pos[0],
                    "confidence": round(confidence, 4),
                    "num": self.num,
                    "is_mine": False,
                    "is_best": False,
                    "exp": "随机选择。",
                    "is_recommend": False,
                }
            )
        else:
            # 将clicks分组，组与组之间没有公共区域（没有公共数字格）
            click_list = [tuple([tuple(clicks[0])])]
            set_list = [
                self.get_set_1(clicks[0][0], clicks[0][1], cell_value)
            ]  # click_list没一个坐标组对应的周边数字格
            for pos in clicks[1:]:
                pos = tuple(pos)
                x, y = pos
                indexes = []
                se = self.get_set_1(x, y, cell_value)
                for index in range(len(click_list)):
                    if len(se & set_list[index]) > 0:  # &：交集 |：并集
                        indexes.append(index)

                if len(indexes) == 0:  # 与click_list中的任意一组都没有交集，单分一组
                    set_list.append(se)
                    click_list.append(tuple([pos]))
                else:
                    # 将所有与pos有交集的组合并
                    temp = []
                    b = 0
                    for index in indexes:
                        index -= b  # pop掉了b个，故要减去b
                        for p in click_list.pop(index):
                            temp.append(p)
                        b += 1
                    temp.append(pos)
                    click_list.append(tuple(temp))

                    # 更新set_list
                    temp = set()
                    b = 0
                    for index in indexes:
                        index -= b
                        temp = temp | set_list.pop(index)  # |：并集运算符
                        b += 1
                    temp = temp | se
                    set_list.append(temp)

            # 同序化clicks与click_list
            clicks = []
            for poses in click_list:
                for i, j in poses:
                    clicks.append(tuple((i, j)))

            # 计算limit
            temp = np.zeros(len(click_list))
            for i in range(len(click_list)):
                temp[i] = len(click_list[i])
            temp = temp >= 15
            t_sum = temp.sum()
            limit = (
                self.limit - int(math.log2(t_sum) / 2) if t_sum != 0 else self.limit
            )  # 20大约20s 19 10s 18 5s
            res_list = []
            canopen_res = np.array([])
            ck = []  # res_list中res的长度
            total = 1

            self.Visible_signal.emit(True)

            is_removed = False
            for index in range(len(click_list)):
                # 运算
                try:
                    self.pv_signal.emit(0)
                    _res, _canopen_res = self.checked[tuple(click_list[index])]
                    _total = len(_res)
                    canopen_res = np.hstack((canopen_res, _canopen_res))
                    total *= _total
                    res_list.append(_res)
                    ck.append(_total)
                    self.pv_signal.emit(100)
                except KeyError:
                    if len(click_list[index]) > limit + 3:  # 大于limit时因为算量过大而无法判断
                        is_removed = True
                        for pos in click_list[index]:
                            clicks.remove(pos)
                            clicks9.append(pos)
                    else:
                        for li in list(self.checked.keys()):
                            if len(set(li) & set(tuple(click_list[index]))) != 0:
                                self.checked.pop(li)
                        try:
                            _res, _total, _canopen_res = self.part_solve(
                                click_list[index],
                                cell_value,
                                num10,
                                num9 - len(click_list[index]),
                                set_list[index],
                                False,
                            )
                        except KeyError:
                            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                            for i in range(1, w + 1):
                                for j in range(1, h + 1):
                                    cell_value[j, i] = 9
                            cell_value = self.complete_scan(cell_value)
                            self.count += 1
                            self.Visible_signal.emit(False)
                            return cell_value

                        if len(_res) == 0:
                            cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                            for i in range(1, w + 1):
                                for j in range(1, h + 1):
                                    cell_value[j, i] = 9
                            cell_value = self.complete_scan(cell_value)
                            self.count += 1
                            self.Visible_signal.emit(False)
                            return cell_value

                        canopen_res = np.hstack((canopen_res, _canopen_res))
                        total *= _total
                        res_list.append(_res)
                        ck.append(_total)

                        self.checked[tuple(click_list[index])] = (_res, _canopen_res)

            if is_removed and (not self.is_play):
                self.warning_signal_2.emit(
                    "由于计算量的限制，一部分情况未枚举，结果可能不准确\n"
                    "您可以通过增加设置中的limit使枚举更全面，但limit\n"
                    "每增加1计算所需的时间增加1倍"
                )

            if len(clicks) == 0:
                self.Visible_signal.emit(False)
                res = []
                total = 0
                pos = [random.choice(clicks9)]
                confidence = 1 - (self.a - num10) / len(clicks9)
                if not self.is_play:
                    self.pos_dict_list.append(
                        {
                            "pos": pos[0],
                            "confidence": round(confidence, 4),
                            "num": self.num,
                            "is_mine": False,
                            "is_best": False,
                            "exp": "随机选择。",
                            "is_recommend": False,
                        }
                    )

            else:
                pos = []
                if total > 150000:  # total太大全排列计算量太大
                    self.Visible_signal.emit(False)
                    mine_num = 0
                    res = np.array([])
                    for res_l in res_list:
                        min_mine_num = min([sum(x) for x in res_l])
                        _all = len(set(clicks) | set(clicks9)) - len(res_l[0])
                        _all = int(_all)
                        min_mine_num = p_of_c(self.a - min_mine_num - num10, _all)
                        _total = 0
                        _res_s = []
                        for _res in res_l:
                            mine_num = sum(_res)
                            p = p_of_c(self.a - mine_num - num10, _all) / min_mine_num
                            __res = _res * p
                            _res_s.append(__res)
                            _total += p
                        res_l = np.array(_res_s)
                        res_l = res_l.sum(axis=0)
                        mine_num += res_l.sum() / _total
                        res_l /= _total
                        res_l = 1 - res_l
                        res = np.hstack((res, res_l))
                else:
                    res = []
                    _total = 0
                    min_val = self.a - len(clicks9)
                    mine_num = []

                    o_value = 0
                    num = 0
                    self.pv_signal.emit(0)
                    for index_list in A(ck):
                        _mine_num = 0  # 一个方案中的雷数
                        r = np.array([])
                        for i in range(len(index_list)):
                            _mine_num += res_list[i][index_list[i]].sum()
                            r = np.hstack([r, res_list[i][index_list[i]]])

                        if min_val <= (_mine_num + num10) <= self.a:
                            mine_num.append(_mine_num)
                            _total += 1
                            res.append(r)
                        n_value = int((num / total) * 100)
                        if n_value - o_value >= 1:
                            self.pv_signal.emit(n_value)
                            o_value = n_value
                        num += 1

                    self.pv_signal.emit(100)
                    self.Visible_signal.emit(False)
                    total = 0
                    min_mine_num = min(mine_num)
                    min_mine_num = p_of_c(self.a - min_mine_num - num10, len(clicks9))
                    __res = np.zeros(len(clicks), dtype=np.float32)

                    for i in range(len(mine_num)):
                        p = p_of_c(self.a - mine_num[i] - num10, len(clicks9)) / min_mine_num
                        if i == 0:
                            __res = res[i].astype(np.float32) * p
                        else:
                            __res += res[i].astype(np.float32) * p
                        total += p
                    res = __res.copy()
                    res = res / total
                    res = 1 - res
                    mine_num = min(mine_num)

                if 1 in res:  # 有确定不为雷的地方
                    for index in range(len(res)):
                        if res[index] >= 0.99:
                            pos.append(clicks[index])
                            confidence = 1
                            if not self.is_play:
                                if tuple(clicks[index]) not in self.appended_pos:
                                    self.pos_dict_list.append(
                                        {
                                            "pos": clicks[index],
                                            "num": self.num,
                                            "confidence": 1,
                                            "is_mine": False,
                                            "is_best": True,
                                            "exp": "枚举得出",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple(clicks[index]))
                else:
                    if len(res) == 0:
                        cell_value = np.zeros((h + 2, w + 2), dtype="int32")
                        for i in range(1, w + 1):
                            for j in range(1, h + 1):
                                cell_value[j, i] = 9
                        cell_value = self.complete_scan(cell_value)
                        self.count += 1
                        return cell_value

                    max_loc = np.argmax(res)
                    max_val = res[max_loc]  # 最大值
                    max_open = 0  # 最大可确定格数
                    _a = np.arange(len(clicks))
                    np.random.shuffle(_a)
                    for p in _a:
                        if 0.005 >= max_val - res[p] >= -0.005:
                            if canopen_res[p] >= max_open:
                                pos = clicks[p]
                                max_open = canopen_res[p]

                    pos = [pos]
                    mine9 = self.a - mine_num - num10  # 剩余雷数

                    is_recommend = True
                    confidence = round(max_val, 5)  # 不是雷的概率
                    if len(clicks9) != 0:
                        _confidence = round(1 - (mine9 / len(clicks9)), 5)
                        if _confidence > confidence:  # 剩余未开方格不是雷的概率大于最大概率
                            is_recommend = False
                            pos = random.choice(clicks9)
                            opennum_res = np.zeros(len(clicks9))
                            pos = [pos]
                            if not self.is_play:
                                for k, (i, j) in enumerate(clicks9):
                                    if (i, j) in pos:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (i, j),
                                                "confidence": _confidence,
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"枚举得出, 预计可以确定的方格数：{round(opennum_res[k], 2)}",
                                                "is_recommend": True,
                                            }
                                        )
                                    else:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": (i, j),
                                                "confidence": _confidence,
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"枚举得出, 预计可以确定的方格数：{round(opennum_res[k], 2)}",
                                                "is_recommend": False,
                                            }
                                        )
                            confidence = _confidence  # 剩余未开方格不是雷的概率
                            if confidence == 1:
                                pos = clicks9

                    if not self.is_play:
                        for p in range(len(clicks)):
                            if 0.005 >= max_val - res[p] >= -0.005:
                                if tuple(clicks[p]) not in self.appended_pos:
                                    if tuple(clicks[p]) == pos[0]:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": clicks[p],
                                                "confidence": round(res[p], 5),
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"枚举得出, 预计可以确定的方格数：{round(canopen_res[p], 2)}",
                                                "is_recommend": True
                                                if is_recommend
                                                else False,
                                            }
                                        )
                                        self.appended_pos.add(tuple(clicks[p]))
                                    else:
                                        self.pos_dict_list.append(
                                            {
                                                "pos": clicks[p],
                                                "confidence": round(res[p], 5),
                                                "num": self.num,
                                                "is_mine": False,
                                                "is_best": False,
                                                "exp": f"枚举得出, 预计可以确定的方格数：{round(canopen_res[p], 2)}",
                                                "is_recommend": False,
                                            }
                                        )
                                        self.appended_pos.add(tuple(clicks[p]))
                            else:
                                if tuple(clicks[p]) not in self.appended_pos:
                                    self.pos_dict_list.append(
                                        {
                                            "pos": clicks[p],
                                            "confidence": round(res[p], 5),
                                            "num": self.num,
                                            "is_mine": False,
                                            "is_best": False,
                                            "exp": f"枚举得出, 预计可以确定的方格数：{round(canopen_res[p], 2)}",
                                            "is_recommend": False,
                                        }
                                    )
                                    self.appended_pos.add(tuple(clicks[p]))

        self.text_signal.emit(f"共{total}种解。")
        if total == 0:
            self.text_signal.emit("随机选择。\n")
            self.text_signal.emit("您可以通过增加设置中的limit使枚举更加全面，但limit每增加1计算所需的时间增加1倍")
        else:
            self.text_signal.emit("\n")
        self.text_signal.emit(str(pos))
        self.text_signal.emit(f" confidence: {(confidence * 100): 0.2f}%\n")

        for p in pos:
            if self.is_play:
                # pass
                pyautogui.click(
                    self.bx + p[0] * self.cell_width, self.by + p[1] * self.cell_width
                )

        if 0 in res:
            for i in range(len(res)):
                if res[i] == 0:
                    x, y = clicks[i]
                    cell_value[y, x] = 10
                    if not self.is_play and self.cell_value[y, x] != 10:
                        if tuple((x, y)) not in self.appended_pos:
                            self.pos_dict_list.append(
                                {
                                    "pos": (x, y),
                                    "confidence": 0,
                                    "num": self.num,
                                    "is_mine": True,
                                    "is_best": False,
                                    "exp": "枚举得出。",
                                    "is_recommend": False,
                                }
                            )
                            self.appended_pos.add(tuple((x, y)))
        self.count = 0

        cell_value = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                cell_value[j, i] = 9
        cell_value = self.complete_scan(cell_value)

        return cell_value

    def best_solve(self, clicks, clicks9, res, cell_value):
        pass

    def part_solve_single(self, clicks, cell_value, num10, num9, cs, _try=True):
        """
        根据点击的坐标，计算出可能的值
        :param _try:
        :param clicks: 点击的坐标
        :param cell_value: 格子中的值
        :param num10: 10的个数
        :param num9: 9的个数
        :param cs: 雷的坐标
        :return: 可能的值
        """
        canopen_res = np.zeros(len(clicks))
        res_list = []
        list_getter = get_list(self.a - num10 - num9, self.a - num10, len(clicks))
        _total = next(list_getter)
        num = 0
        num_solve = 0
        o_value = 0
        self.pv_signal.emit(0)
        solver = Solver()
        for index_list in list_getter:
            # copy 防止改变原数组
            value = cell_value.copy()
            # 将尝试的坐标设为雷。
            for loc in index_list:
                value[clicks[loc][1], clicks[loc][0]] = 10

            flag = 0  # 0 符合条件 -1 不符合条件
            for i, j in cs:
                if value[j, i] != solver.cell_around(i, j, value)[1]:
                    flag = -1
                    break

            res = np.zeros(len(clicks), dtype=np.int32)
            if flag == 0:  # 符合条件
                if _try:
                    for loc in set(range(len(clicks))) - set(index_list):
                        _value = cell_value.copy()
                        num9 = 0
                        num10 = 0
                        i, j = clicks[loc]
                        for u in range(i - 1, i + 2):
                            for v in range(j - 1, j + 2):
                                if value[v, u] == 9 and ((u, v) not in clicks):
                                    num9 += 1
                                elif value[v, u] == 10:
                                    num10 += 1
                        can_open = solver.try_solve(i, j, _value, clicks, num9, num10)
                        canopen_res[loc] += can_open

                num_solve += 1
                for loc in index_list:
                    res[loc] += 1
                res_list.append(res)

            n_value = int((num / _total) * 100)
            if n_value - o_value >= 1:
                self.pv_signal.emit(n_value)
                o_value = n_value
            num += 1

        # 没有雷的情况
        value = cell_value.copy()

        flag = 0
        for i, j in cs:
            if value[j, i] != self.cell_around(i, j, value)[1]:  # 不符合条件的
                flag = -1
                break

        if flag == 0:  # 符合条件
            res_list.append(np.zeros(len(clicks), dtype=np.int32))

        if num_solve != 0:
            canopen_res /= num_solve

        self.pv_signal.emit(100)
        return res_list, len(res_list), canopen_res

    def part_solve(self, clicks, cell_value, num10, num9, cs, _try=False):
        """
        根据点击的坐标，计算出可能的值
        :param _try:
        :param clicks: 点击的坐标
        :param cell_value: 格子中的值
        :param num10: 10的个数
        :param num9: 9的个数
        :param cs: 雷的坐标
        :return: 可能的值
        """
        start = time.time()
        solver = Solver()
        _cs = defaultdict(list)
        for i, j in clicks:
            for u in range(i - 1, i + 2):
                for v in range(j - 1, j + 2):
                    if 1 <= cell_value[v, u] <= 8:
                        _cs[(i, j)].append((u, v))
        _cs = dict(_cs)

        clicks = list(clicks)
        print(len(clicks))

        def f(cell_value, state: list, clicks: list, res: list, completed=0, depth=1):
            if len(clicks) == 1:
                x, y = clicks[0]
                value = cell_value.copy()

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    if value[j, i] != self.cell_around(i, j, value)[1]:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(0)
                    _state = np.array(_state)
                    res.append(_state)

                completed += 1 / 2 ** depth

                value[y, x] = 10
                num10 = len(np.argwhere(value == 10))
                if num10 > self.a:
                    return res, completed

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    if value[j, i] != self.cell_around(i, j, value)[1]:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(1)
                    _state = np.array(_state)
                    res.append(_state)

                completed += 1 / 2 ** depth

                return res, completed

            else:
                x, y = clicks[0]
                _clicks = clicks.copy()
                _clicks.pop(0)
                value = cell_value.copy()

                value[y, x] = 11

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    _num9, _num10 = self.cell_around(i, j, value)
                    if value[j, i] > _num9 + _num10 or value[j, i] < _num10:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(0)
                    res, completed = f(value.copy(), _state, _clicks, res, completed, depth + 1)
                else:
                    completed += 1 / 2 ** depth

                self.pv_signal.emit(int(completed * 100))

                value[y, x] = 10
                num10 = len(np.argwhere(value == 10))
                if num10 > self.a:
                    return res, completed

                flag = 0  # 0 符合条件 -1 不符合条件
                for i, j in _cs[(x, y)]:
                    _num9, _num10 = self.cell_around(i, j, value)
                    if value[j, i] > _num9 + _num10 or value[j, i] < _num10:
                        flag = -1
                        break

                if flag == 0:
                    _state = state.copy()
                    _state.append(1)
                    res, completed = f(value.copy(), _state, _clicks, res, completed, depth + 1)
                else:
                    completed += 1 / 2 ** depth
                self.pv_signal.emit(int(completed * 100))

                return res, completed

        canopen_res = np.zeros(len(clicks))
        # clicks = sorted(clicks, key=lambda x: x[1] + x[0])
        res_l, _ = f(cell_value, [], clicks, [])

        if _try:
            # 计算每个方格可以开的格子数
            for index_list in res_l:
                # copy 防止改变原数组
                value = cell_value.copy()
                # 将尝试的坐标设为雷。
                for loc in index_list:
                    value[clicks[loc][1], clicks[loc][0]] = 10

                for loc in set(range(len(clicks))) - set(index_list):
                    _value = cell_value.copy()
                    num9 = 0
                    num10 = 0
                    i, j = clicks[loc]
                    for u in range(i - 1, i + 2):
                        for v in range(j - 1, j + 2):
                            if value[v, u] == 9 and ((u, v) not in clicks):
                                num9 += 1
                            elif value[v, u] == 10:
                                num10 += 1
                    can_open = solver.try_solve(i, j, _value, clicks, num9, num10)
                    canopen_res[loc] += can_open

        num_solve = len(res_l)
        if num_solve != 0:
            canopen_res /= num_solve

        total_time = time.time() - start
        with open("data.json") as f:
            data = json.load(f)
        try:
            data[str(len(clicks))].append(total_time)
        except KeyError:
            data[str(len(clicks))] = [total_time]

        with open("data.json", "w") as f:
            json.dump(data, f)

        return res_l, num_solve, canopen_res

    def get_set_1(self, i, j, cell_value):
        result = set()
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if 0 < cell_value[n, m] < 8:
                        result.add((m, n))
        return result

    def get_set(self, i, j, cell_value):
        result = set()
        cnt10 = 0
        for n in range(j - 1, j + 2):
            for m in range(i - 1, i + 2):
                if 0 <= m <= self.w + 1 and 0 <= n <= self.h + 1:
                    if cell_value[n, m] == 9:
                        result.add((m, n))
                    elif cell_value[n, m] == 10:
                        cnt10 += 1
        return result, cnt10

    def compare_img(self, img, no_10):
        result = np.ones((11, 3)) * 100
        for i in range(len(self.images)):
            for j in range(len(self.images[i])):
                try:
                    res = cv.matchTemplate(img, self.images[i][j], cv.TM_SQDIFF_NORMED)
                    result[i, j] = np.sum(res)
                except:
                    pass

        res = np.unravel_index(np.argmin(result, axis=None), result.shape)[0]
        if no_10:
            if res == 10:
                res = 9
        return res

    def complete_scan(self, cell_value, no_10=True):
        pyautogui.moveTo(10, 640, _pause=False)
        hwnd = win32gui.FindWindow(None, setting.win_name)
        bx, by = ClientToScreen(hwnd, self._bx, self._by)
        pil_img = ImageGrab.grab(
            (
                bx + 0.5 * self.cell_width,
                by + 0.5 * self.cell_width,
                self.w * self.cell_width + bx + 0.5 * self.cell_width,
                self.h * self.cell_width + by + 0.5 * self.cell_width,
            )
        )
        pil_img = np.array(pil_img)
        pil_img.reshape(self.w * self.cell_width, self.h * self.cell_width, 3)
        self.img = cv.cvtColor(pil_img, cv.COLOR_RGB2BGR)

        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                if cell_value[y, x] == 9:
                    img = self.cell_screenshot(x, y)
                    cell_value[y, x] = self.compare_img(img, no_10)

        return cell_value

    def small_square_scan(self, i, j, cell_value):
        hwnd = win32gui.FindWindow(None, setting.win_name)
        bx, by = ClientToScreen(hwnd, self._bx, self._by)
        pil_img = ImageGrab.grab(
            (
                bx + 0.5 * self.cell_width,
                by + 0.5 * self.cell_width,
                self.w * self.cell_width + bx + 0.5 * self.cell_width,
                self.h * self.cell_width + by + 0.5 * self.cell_width,
            )
        )
        pil_img = np.array(pil_img)
        pil_img.reshape(self.w * self.cell_width, self.h * self.cell_width, 3)
        self.img = cv.cvtColor(pil_img, cv.COLOR_RGB2BGR)
        sx0 = i - 2
        if sx0 < 1:
            sx0 = 1
        sx1 = i + 2
        if sx1 > self.w:
            sx1 = self.w
        sy0 = j - 2
        if sy0 < 1:
            sy0 = 1
        sy1 = j + 2
        if sy1 > self.h:
            sy1 = self.h
        pyautogui.moveTo(10, 640, _pause=False)
        for j in range(sy0, sy1 + 1):
            for i in range(sx0, sx1 + 1):
                img = self.cell_screenshot(i, j)
                if cell_value[j, i] == 9:
                    cell_value[j, i] = self.compare_img(img, no_10=True)
        return cell_value


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
    for row in cell_value:
        for i in row:
            print((2 - len(str(int(i)))) * " ", int(i), end="")
        print()


if __name__ == "__main__":
    solver = Solver()
    solver.a = 10
    solver.w = 6
    solver.h = 5
    cell_value = [[0, 0, 0, 0, 0, 0, 9, 0],
                  [0, 9, 9, 9, 9, 9, 9, 0],
                  [0, 9, 1, 9, 9, 3, 9, 0],
                  [0, 9, 9, 2, 2, 9, 9, 0],
                  [0, 9, 9, 9, 9, 1, 9, 0],
                  [0, 9, 9, 9, 9, 9, 9, 0],
                  [0, 0, 0, 0, 0, 0, 0, 0]
                  ]
    cell_value = np.array(cell_value)
    clicks = [(1, 1), (2, 1), (1, 2), (3, 1), (1, 3), (4, 1), (3, 2), (2, 3), (5, 1), (4, 2), (2, 4), (6, 1), (3, 4), (6, 2),
              (5, 3), (4, 4), (6, 3), (4, 5), (5, 5), (6, 4), (6, 5)]

    start = time.time()
    res_list, t, _ = solver.part_solve(clicks, cell_value, 0, 26, [(2, 2), (3, 3), (4, 3), (5, 2), (5, 4)], _try=False)
    res_list = np.array(res_list)
    res_list = res_list.sum(axis=0)
    res_list = res_list / t
    print(res_list, t)
    print(_)
    print("time", time.time() - start)

    start = time.time()
    res_list, t, _ = solver.part_solve_single(clicks, cell_value, 0, 26, [(2, 2), (3, 3), (4, 3), (5, 2), (5, 4)],
                                              _try=False)
    res_list = np.array(res_list)
    res_list = res_list.sum(axis=0)
    res_list = res_list / t
    print(res_list, t)
    print(_)
    print("time", time.time() - start)
