import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from multiprocessing import Process

from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel
from PyQt5 import QtCore
from PyQt5.QtCore import QThread, Qt, QTimer, QProcess
from PIL import ImageGrab, ImageDraw, Image

import cv2
import numpy
import win32gui
from win32com import client
import setting


def pil_to_cv(img):
    return cv2.cvtColor(numpy.asarray(img), cv2.COLOR_RGB2BGR)


def minesweeper_run(mine_dir):
    class_name = None
    title_name = setting.win_name

    hwnd = win32gui.FindWindow(class_name, title_name)
    if hwnd == 0:
        subprocess.Popen(mine_dir)
        time.sleep(1)
        hwnd = win32gui.FindWindow(class_name, title_name)
    win32gui.ShowWindow(hwnd, 1)
    shell = client.Dispatch("WScript.Shell")
    shell.SendKeys("%")
    win32gui.SetForegroundWindow(hwnd)

    return hwnd


def ClientToScreen(hwnd, x, y):
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(
        ctypes.wintypes.HWND(hwnd),
        ctypes.byref(rect),
    )
    win_x, win_y, _, _ = rect.left, rect.top, rect.right, rect.bottom
    return win_x + x, win_y + y


def ScreenToClient(hwnd, x, y):
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(
        ctypes.wintypes.HWND(hwnd),
        ctypes.byref(rect),
    )
    win_x, win_y, _, _ = rect.left, rect.top, rect.right, rect.bottom
    return x - win_x, y - win_y


class GetMousePosition(QThread):
    pos_signal = QtCore.pyqtSignal(tuple)

    def __init__(self):
        super().__init__()

    def run(self):
        count = 0
        time.sleep(1.3)
        pci = ctypes.wintypes.POINT(0, 0)
        pci = ctypes.pointer(pci)
        ctypes.windll.user32.GetCursorPos(pci)
        o_x, o_y = (pci.contents.x, pci.contents.y)
        while True:
            time.sleep(0.1)
            pci = ctypes.wintypes.POINT(0, 0)
            pci = ctypes.pointer(pci)
            ctypes.windll.user32.GetCursorPos(pci)
            n_x, n_y = (pci.contents.x, pci.contents.y)
            if n_x == o_x and n_y == o_y:
                count += 1
            else:
                count = 0
            if count >= 8:
                self.pos_signal.emit(tuple((n_x, n_y)))
                return
            o_x, o_y = n_x, n_y


class MouseWindowTread(Process):
    def __init__(self):
        super().__init__()

    def run(self):
        app = QApplication(sys.argv)
        self.mw = MouseWindow()
        app.exec()


class MouseWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(90, 90)
        self.setMinimumSize(90, 90)
        self.setMaximumSize(90, 90)
        self.move(0, 0)
        self.setWindowTitle("aaa")
        self.hwnd = None

        self.label = QLabel(self)
        self.label.setText("")
        self.label.setGeometry(0, 0, 90, 90)

        self.timer = QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._update)
        self.timer.start()

        self.show()

    def _update(self):
        if not self.hwnd or self.hwnd == 0:
            self.hwnd = win32gui.FindWindow(None, "aaa")

        pci = ctypes.wintypes.POINT(0, 0)
        pci = ctypes.pointer(pci)
        ctypes.windll.user32.GetCursorPos(pci)
        o_x, o_y = (pci.contents.x, pci.contents.y)
        img = ImageGrab.grab((o_x - 4, o_y - 4, o_x + 5, o_y + 5))
        img = img.resize((90, 90), resample=Image.NEAREST)
        draw = ImageDraw.Draw(img)
        draw.line([45, 0, 45, 100], fill="green", width=5)
        draw.line([0, 45, 100, 45], fill="green", width=5)
        self.label.setPixmap(img.toqpixmap())
        self.label.repaint()

        if self.hwnd != 0:
            hwnd = ctypes.wintypes.HWND(self.hwnd)
            ctypes.windll.user32.ShowWindow(hwnd, ctypes.c_int(1))
            if ctypes.windll.user32.GetForegroundWindow() != self.hwnd:
                ctypes.windll.user32.SetWindowPos(
                    hwnd,
                    ctypes.wintypes.HWND(-1),
                    ctypes.c_int(0),
                    ctypes.c_int(0),
                    ctypes.c_int(0),
                    ctypes.c_int(0),
                    0x0002 | 0x0001,
                )
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.MoveWindow(
                hwnd,
                ctypes.c_int(o_x + 10),
                ctypes.c_int(o_y + 10),
                ctypes.c_int(90),
                ctypes.c_int(90),
                ctypes.c_bool(True),
            )

    def closeEvent(self, a0) -> None:
        self.timer.stop()
        self.close()


def set_top_window(hwnd):
    hwnd = ctypes.wintypes.HWND(hwnd)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


if __name__ == "__main__":
    hwnd = win32gui.FindWindow(None, setting.win_name)
    print(ClientToScreen(hwnd, 54 + 18, 114 + 18))
