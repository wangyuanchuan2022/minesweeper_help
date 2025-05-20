#!/usr/bin/python3
# -*- coding: utf-8 -*-
# https://github.com/wangyuanchuan2022/minesweeper_help
import functools
import json
import multiprocessing
import os
import re
import sys
import time

import cv2
import numpy as np
import pyautogui
import win32gui
import win32ui
import win32con
from PIL import ImageGrab
from PyQt5 import QtGui, QtWidgets, sip, QtCore
from PyQt5.Qt import QInputDialog, QDialog, QFileDialog, QPixmap
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QMainWindow,
    QApplication,
    QMessageBox,
    QCheckBox,
    QPushButton,
)

from ui import Ui_Dialog, Ui_form, Ui_MainWindow
from utils import (
    minesweeper_run,
    ClientToScreen,
    ScreenToClient,
    GetMousePosition,
    MouseWindowTread,
    set_top_window,
    Solver
)
import setting


class MyMainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()

        self.value = None  # 现在进度条数值 float
        self.value_per_interval = None  # 每隔0.1s增加的数值 float
        self.list_p = None  # 帮助页面上提示框中提到的横纵坐标的列表
        self.is_show = True  # 是否展示提示limit不足
        self.warning_window = None
        self.info_window = None
        # 提示框起始点坐标
        self._x = 391
        self._y = 119
        self.bx = None
        self.by = None
        self.with_help = True
        self.clicks = []  # 已知不是雷的方格
        self.mines = []  # 已知是雷的方格
        # 加载config
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        # assign values to the variables
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self._bx = self.cfg["bx"]
        self._by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]

        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        self.show()

        # 设置plainTextEdit滑动条样式，
        self.plainTextEdit.verticalScrollBar().setStyleSheet(
            """QScrollBar:vertical
        {
            margin:30px 0px 30px 0px;
            background-color:rgba(255, 255, 255, 0);
            border: none;
            width:20px;    
        }
        QScrollBar::handle:vertical
        {
            background-color:rgb(192, 192, 192);
            width:30px;
            border-radius:7px;
            min-height:40px;
            max-width:10px
        }
        QScrollBar::handle:vertical:hover
        {
            background-color:rgb(209, 209, 209);
            width:30px;
            border-radius:7px;
        }
        QScrollBar::add-line:vertical
        {
            subcontrol-origin: margin;
            border:none;
            height:0px;
        }
        
        QScrollBar::sub-line:vertical
        {
           subcontrol-origin: margin;
            border:none;
            height:0px;
        }
        QScrollBar::add-page:vertical
        {
          background-color:rgba(0, 0, 0, 0);
        }
        
        QScrollBar::sub-page:vertical
        {
            background-color:rgba(0, 0, 0, 0); 
        }
        QScrollBar::up-arrow:vertical
        {
          border:0px;
          width:0px;
          height:0px;
        }
        
        QScrollBar::up-arrow:vertical:pressed
        {
            border:0px;
            width:0px;
            height:0px;
        }
        QScrollBar::down-arrow:vertical
        {
            border:0px;
            width:0px;
            height:0px;
        }"""
        )

        # 更新屏幕
        self.timer = QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.update_screen)
        self.timer.start()

        # 绑定左侧
        self.auto_play.clicked.connect(self.auto_play_func)
        self.setting_2.clicked.connect(self.get_setting)
        self.help_human.clicked.connect(self.update_image)

        self.label.setPixmap(QtGui.QPixmap("image/example.png"))

        self.stackedWidget.setCurrentIndex(0)

        self.auto_play_thread = Solver()

        # 设置一个值表示进度条的当前进度
        self.pv = 0
        self.pgb.setValue(self.pv)
        self.pgb.setVisible(False)
        self.pv_p3 = 0
        self.pgb_p3.setValue(self.pv_p3)
        self.pgb_p3.setVisible(False)
        self.plainTextEdit.setReadOnly(True)
        self.plainTextEdit.setPlaceholderText("这是一个程序输出窗口")
        self.return_to_main_page.setVisible(False)
        self.return_to_main_page.clicked.connect(
            lambda: self.stackedWidget.setCurrentIndex(0)
        )

        self.end_help_thread.setVisible(False)
        self.end_auto_play_thread.setVisible(False)

        self.end_auto_play_thread.clicked.connect(self.end_a_func)
        self.end_help_thread.clicked.connect(self.end_h_func)

        self.auto_play_thread.pv_signal.connect(lambda v: self.pgb.setValue(v))
        self.auto_play_thread.text_signal.connect(self.update_text)
        self.auto_play_thread.Visible_signal.connect(lambda v: self.pgb.setVisible(v))
        self.auto_play_thread.Visible_signal.connect(
            lambda v: self.end_auto_play_thread.setVisible(v)
        )
        self.auto_play_thread.warning_signal.connect(
            lambda s: QMessageBox.warning(self, "错误", s)
        )
        self.auto_play_thread.start_signal.connect(
            functools.partial(self.update_pgb, name="pgb")
        )
        self.auto_play_thread.end_signal.connect(self.end_timer)

        self.help_thread = Solver()
        self.help_thread.set_args(0)  # 设置为帮助模式。
        self.help_thread.pv_signal.connect(lambda v: self.pgb_p3.setValue(v))
        self.help_thread.Visible_signal.connect(lambda v: self.pgb_p3.setVisible(v))
        self.help_thread.Visible_signal.connect(
            lambda v: self.end_help_thread.setVisible(v)
        )
        self.help_thread.update_btn_list_signal.connect(self.update_btn_list)
        self.help_thread.warning_signal.connect(
            lambda s: QMessageBox.about(self, "提示", s)
        )
        self.help_thread.warning_signal_2.connect(self.help_thread_warning)
        self.help_thread.start_signal.connect(
            functools.partial(self.update_pgb, name="pgb_p3")
        )
        self.help_thread.end_signal.connect(self.end_timer)
        self.return_to_main_p3.clicked.connect(
            lambda: self.stackedWidget.setCurrentIndex(0)
        )
        self.click_all.clicked.connect(self.click_all_func)

        self.up_pgb_timer = QTimer()
        self.up_pgb_timer.setInterval(100)
        self.up_pgb_timer.timeout.connect(self.end_func)

        # 初始化帮助页面的提示块
        self.btn_list = []
        self.row_list = []
        self.col_list = []

        self.btn_width = 24
        for j in range(self.h):
            btn = QtWidgets.QPushButton(str(j + 1), self.frame_2)
            btn.setGeometry(0, (j + 1) * self.btn_width, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.col_list.append(btn)
            temp = []
            for i in range(self.w):
                btn = QtWidgets.QPushButton("", self.frame_2)
                btn.setGeometry(
                    i * self.btn_width + self.btn_width,
                    j * self.btn_width + self.btn_width,
                    self.btn_width,
                    self.btn_width,
                )
                btn.setStyleSheet(
                    "QPushButton{border-radius:0px;border: none;"
                    'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(255, 255, 255, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
                temp.append(btn)
            self.btn_list.append(temp)
        self.set_btn_list_enable(False)
        for i in range(self.w):
            btn = QtWidgets.QPushButton(str(i + 1), self.frame_2)
            btn.setGeometry((i + 1) * self.btn_width, 0, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.row_list.append(btn)

        self.screenshot_help.clicked.connect(self.screenshot_help_func)

    def end_timer(self, s):
        # 已经计算完成
        self.up_pgb_timer.stop()
        self.pgb.setValue(100)
        self.pgb_p3.setValue(100)

    def update_pgb(self, t, name):
        # 根据上一次计算的速度更新进度条
        pgb = getattr(self, name, None)
        total, speed, _ = t
        self.value_per_interval = 0.1 / (total / speed) * 100
        self.value = 0
        pgb.setValue(0)
        self.up_pgb_timer.start()

    def end_func(self):
        # 根据上一次计算的速度更新进度条
        self.pgb_p3.setValue(int(self.value))
        self.pgb.setValue(int(self.value))
        self.value += self.value_per_interval
        if self.value >= 100:
            self.value = 100
            self.up_pgb_timer.stop()

    def screenshot_help_func(self):
        # Reload the game
        self.reload()
        try:
            # Run the game and get the window handle
            hwnd = minesweeper_run(self.path)
        except:
            # Show an error message if the path is not correct
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            return

        # Disable help
        self.with_help = False
        self.update_image()

        # Create a new instance of the ScreenShot class
        self.screen_shot = ScreenShot(hwnd)
        # Set the window modality to application modal
        self.screen_shot.setWindowModality(Qt.ApplicationModal)
        # Show the window
        self.screen_shot.show()
        # Wait for the window to be created
        time.sleep(0.05)
        # Find the window handle of the "截图帮助" window
        hwnd = win32gui.FindWindow(None, "截图帮助")
        # Show the window
        win32gui.ShowWindow(hwnd, 1)
        # Set the top window
        set_top_window(hwnd)

    def end_h_func(self):
        # 终止帮助程序的计算
        self.pgb_p3.setVisible(False)
        self.help_thread.terminate()
        self.end_help_thread.setVisible(False)
        self.set_btns_Enabled(True)
        self.return_to_main_p3.setVisible(True)
        self.up_pgb_timer.stop()
        QMessageBox.about(self, "提示", "您可以降低设置中的limit来减少计算时间\n" "但减少limit可能会导致结果不全面")

    def end_a_func(self):
        # 终止自动程序的计算
        self.pgb_p3.setVisible(False)
        self.help_thread.terminate()
        self.end_help_thread.setVisible(False)
        self.set_btns_Enabled(True)
        self.pgb.setVisible(False)
        self.auto_play_thread.terminate()
        self.end_auto_play_thread.setVisible(False)
        self.up_pgb_timer.stop()
        QMessageBox.about(self, "提示", "您可以降低设置中的limit来减少计算时间\n" "但减少limit可能会导致结果不全面")

    def set_btns_Enabled(self, v: bool):
        # 设置左侧按钮Enable or not
        self.screenshot_help.setEnabled(v)
        self.help_human.setEnabled(v)
        self.auto_play.setEnabled(v)
        self.setting_2.setEnabled(v)

    def reload(self):
        # 重新加载
        with open("cfg.json", encoding="utf-8") as f:
            cfg = json.load(f)
        self.w = cfg["w"]
        self.h = cfg["h"]
        self._bx = cfg["bx"]  #
        self._by = cfg["by"]  #
        self.cell_width = cfg["cell_width"]
        self.a = cfg["a"]  # 总雷数
        self.path = cfg["path"]

    def update_text(self, text):
        # 更新文本
        self.plainTextEdit.insertPlainText(text)
        self.plainTextEdit.moveCursor(self.plainTextEdit.textCursor().End)

    def set_btn_list_enable(self, b: bool):
        # 设置帮助页面按钮Enable or not
        for j in range(len(self.btn_list)):
            for i in range(len(self.btn_list[0])):
                self.btn_list[j][i].setEnabled(b)

    def get_setting(self):
        self.edit_window = EditWindow()
        self.edit_window.setWindowModality(Qt.ApplicationModal)
        self.edit_window.show()

    def reset_btn_list(self):
        # 重置按钮列表
        for j in range(len(self.btn_list)):
            for i in range(len(self.btn_list[0])):
                self.btn_list[j][i].deleteLater()
                sip.delete(self.btn_list[j][i])
        for i in range(len(self.row_list)):
            self.row_list[i].deleteLater()
            sip.delete(self.row_list[i])
        for j in range(len(self.col_list)):
            self.col_list[j].deleteLater()
            sip.delete(self.col_list[j])

        self.btn_list = []
        self.row_list = []
        self.col_list = []
        for j in range(self.h):
            temp = []
            for i in range(self.w):
                btn = QtWidgets.QPushButton("", self.frame_2)
                btn.setGeometry(
                    i * self.btn_width + self.btn_width,
                    j * self.btn_width + self.btn_width,
                    self.btn_width,
                    self.btn_width,
                )
                btn.setStyleSheet(
                    "QPushButton{border-radius:0px;border: none;"
                    'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(255, 255, 255, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
                btn.show()
                temp.append(btn)
            self.btn_list.append(temp)

        for j in range(self.h):
            btn = QtWidgets.QPushButton(str(j + 1), self.frame_2)
            btn.setGeometry(0, (j + 1) * self.btn_width, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体"}'
            )
            btn.show()
            self.col_list.append(btn)

        for i in range(self.w):
            btn = QtWidgets.QPushButton(str(i + 1), self.frame_2)
            btn.setGeometry((i + 1) * self.btn_width, 0, self.btn_width, self.btn_width)
            btn.setStyleSheet(
                "QPushButton{border-radius:0px;border: 1px solid rgb(210, 210, 210);"
                'background-color:rgba(255, 255, 255, 255);font: 9pt "楷体";}'
            )
            btn.show()
            self.row_list.append(btn)

    def update_image(self):
        # 启动帮助程序
        self.click_all.setEnabled(False)
        self.return_to_main_p3.setVisible(False)
        self.reload()
        self.clicks = []
        self.mines = []

        self.label_p3.setText("")
        self.reload()
        try:
            hwnd = minesweeper_run(self.path)
        except:
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            self.set_btns_Enabled(True)
            return

        self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)
        w = self.w * self.cell_width
        h = self.h * self.cell_width

        self.reset_btn_list()
        self.label_p3.lower()
        self.set_btn_list_enable(False)

        self.stackedWidget.setCurrentIndex(2)

        if self.with_help:
            self.set_btns_Enabled(False)
            self.help_thread.start()

        self.with_help = True

        pil_img = ImageGrab.grab(
            (
                self.bx + 0.5 * self.cell_width,
                self.by + 0.5 * self.cell_width,
                w + self.bx + 0.5 * self.cell_width,
                h + self.by + 0.5 * self.cell_width,
            )
        )
        pil_img.save("image/save.png")
        self.label_p3.setPixmap(
            QtGui.QPixmap("image/save.png").scaled(
                self.w * self.btn_width, self.h * self.btn_width
            )
        )
        self.label_p3.move(self.btn_width, self.btn_width)
        w = (self.w + 1) * self.btn_width
        h = (self.h + 1) * self.btn_width
        self.label_p3.resize(self.w * self.btn_width, self.h * self.btn_width)
        self.label_p3.setMinimumSize(self.w * self.btn_width, self.h * self.btn_width)
        self.frame_2.setMinimumSize(w, h)

        w, h = self.w, self.h
        cell_value = np.zeros((h + 2, w + 2), dtype="int32")
        for i in range(1, w + 1):
            for j in range(1, h + 1):
                cell_value[j, i] = 9
        cell_value = self.help_thread.complete_scan(cell_value, False)
        for u in range(1, w + 1):
            for v in range(1, h + 1):
                i, j = u - 1, v - 1
                self.btn_list[j][i].setEnabled(True)
                self.btn_list[j][i].setText(str(cell_value[j + 1, i + 1]))
                self.btn_list[j][i].setStyleSheet(
                    "QPushButton{border-radius:0px; border: 0px solid rgb(255, 255, 255);"
                    'background-color:rgba(255, 255, 255, 120);font: 10pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )

    def update_btn_list(self, pos_dict_list):
        # 根据计算结果更新btn_list
        for pos_dict in pos_dict_list:
            (i, j) = pos_dict["pos"]
            string = (
                pos_dict["exp"]
                + f"\n({i}, {j})不是雷的概率：{pos_dict['confidence'] * 100:0.2f}%"
            )
            i, j = i - 1, j - 1
            confidence = pos_dict["confidence"]
            self.btn_list[j][i].setEnabled(True)
            self.btn_list[j][i].setText(str(pos_dict["num"]))
            self.btn_list[j][i].clicked.connect(functools.partial(self.info, string))

            if pos_dict["is_mine"] or confidence <= 0.01:
                self.mines.append((i + 1, j + 1))
                self.btn_list[j][i].setStyleSheet(
                    "QPushButton{border-radius:0px; border: 2px solid rgb(0, 0, 255);"
                    'background-color:rgba(255, 0, 0, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
            elif pos_dict["is_best"] or confidence >= 0.99:
                self.clicks.append((i + 1, j + 1))
                self.btn_list[j][i].setStyleSheet(
                    "QPushButton{border-radius:0px; border: 2px solid rgb(0, 0, 255);"
                    'background-color:rgba(0, 255, 0, 255);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
            elif pos_dict["is_recommend"]:
                self.btn_list[j][i].setStyleSheet(
                    f"QPushButton{{border-radius:0px; border: 3px solid rgb(255, 255,"
                    f" 82);background-color:rgba({255 * (1 - confidence)},"
                    f' {confidence * 255}, 0, 255);font: 9pt "楷体"}}'
                    "QPushButton:disabled{border: none;"
                    "background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
            else:
                self.btn_list[j][i].setStyleSheet(
                    f"QPushButton{{border-radius:0px; border: none;"
                    f"background-color:rgba({255 * (1 - confidence)},"
                    f' {confidence * 255}, 0, 255);font: 9pt "楷体"}}'
                    "QPushButton:disabled{border: none;"
                    "background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )

        self.click_all.setEnabled(True)
        self.return_to_main_p3.setVisible(True)

        self.set_btns_Enabled(True)

    def info(self, s):
        # 提示info
        template = re.compile(r"\((\d+),\s(\d+)\)")
        self.list_p = re.findall(template, s.split("\n")[0])
        if self.list_p:
            for i, j in self.list_p:
                i = int(i) - 1
                j = int(j) - 1
                self.btn_list[j][i].setEnabled(True)
                self.btn_list[j][i].setStyleSheet(
                    "QPushButton{border-radius:0px; border: 3px solid rgb(211, 51, 255);"
                    'background-color:rgba(0, 0, 0, 0);font: 9pt "楷体"}'
                    "QPushButton:disabled{border: none;background-color:rgba(0, 0, 0, 0);"
                    "color:rgba(210, 210, 210, 0);}"
                )
        self.info_window = MyMessageBox(
            self, message=s, x=self._x + self.x(), y=self._y + self.y()
        )
        self.info_window.signal.connect(self.set_window_xy)

    def set_window_xy(self, t):
        self._x = t[0] - self.x()
        self._y = t[1] - self.y()
        if self.list_p:
            for i, j in self.list_p:
                i = int(i) - 1
                j = int(j) - 1
                self.btn_list[j][i].setEnabled(False)

    def help_thread_warning(self, s):
        if self.is_show:
            self.warning_window = MyMessageBox(
                self, title="警告", message=s, checked=True
            )
            self.warning_window.signal.connect(self.set_is_show)

    def set_is_show(self, t):
        self.is_show = not t[2]

    def click_all_func(self):
        try:
            hwnd = win32gui.FindWindow(None, setting.win_name)
            win32gui.ShowWindow(hwnd, 1)
            self.reload()
            self.bx, self.by = ClientToScreen(hwnd, self._bx, self._by)
            set_top_window(hwnd)
            if len(self.clicks) != 0:
                for i, j in self.clicks:
                    # 点击确定的方格
                    pyautogui.click(
                        self.cell_width * i + self.bx, self.cell_width * j + self.by
                    )
                self.clicks = []

            if len(self.mines) != 0:
                pyautogui.keyDown("ctrl")
                for i, j in self.mines:
                    # 将是雷的方格标雷
                    pyautogui.rightClick(
                        self.cell_width * i + self.bx, self.cell_width * j + self.by
                    )
                pyautogui.keyUp("ctrl")
                self.mines = []

            self.click_all.setEnabled(False)
        except pyautogui.FailSafeException:
            pyautogui.keyUp("ctrl", _pause=False)
            return

    def update_screen(self):
        if self.auto_play_thread.isRunning() and self.stackedWidget.currentIndex() == 1:
            self.auto_play.setText("已启动")
            self.set_btns_Enabled(False)

        elif (
            self.auto_play_thread.isFinished()
            and self.stackedWidget.currentIndex() == 1
        ):
            self.set_btns_Enabled(True)
            self.auto_play.setText("自动")
            if self.stackedWidget.currentIndex() == 1:
                self.return_to_main_page.setVisible(True)
        elif self.help_thread.isRunning() and self.stackedWidget.currentIndex() == 2:
            self.set_btns_Enabled(False)

        elif (
            self.auto_play_thread.isFinished()
            and self.stackedWidget.currentIndex() == 2
        ):
            self.set_btns_Enabled(True)
            if self.stackedWidget.currentIndex() == 2:
                self.return_to_main_p3.setVisible(True)

    def auto_play_func(self):
        # 启动自动程序
        self.reload()
        value, ok = QInputDialog.getInt(
            self, "总局数", "请输入要玩的总局数\n\n请输入整数:", 1, 1, 10000, 1
        )
        if ok:
            try:
                self.plainTextEdit.clear()
                self.reload()
                minesweeper_run(self.path)
                self.auto_play_thread.set_args(value)
                self.auto_play_thread.start()
                self.return_to_main_page.setVisible(False)
                self.stackedWidget.setCurrentIndex(1)
            except:
                QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
                self.return_to_main_page.setVisible(True)


class MyMessageBox(QMessageBox):
    signal = QtCore.pyqtSignal(tuple)

    def __init__(
        self, parent=None, title="消息", message="", x=None, y=None, checked=False
    ):
        super(MyMessageBox, self).__init__(parent)
        if x and y:
            self.move(int(x), int(y))
        self._x = x
        self._y = y
        self.setModal(True)
        # 设置窗口标题 及样式
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        # 设置提示图标
        self.setIcon(QMessageBox.Icon.Information)
        # 设置自定义提示图片
        # self.setIconPixmap(QPixmap("./imgs/dog.jpg").scaled(50, 50))

        # 设置标题
        self.setText(message)
        # 设置勾选框
        self.cb = None
        if checked:
            self.cb = QCheckBox("下次不在显示", self)
            self.setCheckBox(self.cb)
        # 详情文本
        # self.setDetailedText("详细信息")

        # 添加标准按钮
        # self.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        # yes_button = self.button(QMessageBox.StandardButton.Yes)  # 返回按钮对象

        # 自定义按钮
        confirm_btn = self.addButton("确定", QMessageBox.ButtonRole.YesRole)
        # 移除按钮
        # self.removeButton(some_btn)

        # 设置默认按钮
        self.setDefaultButton(confirm_btn)

        # 设置 ESC键 对应的按钮
        # self.setEscapeButton(cancle_btn)
        # 信号
        self.buttonClicked.connect(self.set_x_y)

        self.show()

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        self._x = self.x()
        self._y = self.y()
        param = (
            (self._x, self._y, self.cb.isChecked()) if self.cb else (self._x, self._y)
        )
        self.signal.emit(param)

    def set_x_y(self):
        self._x = self.x()
        self._y = self.y()
        param = (
            (self._x, self._y, self.cb.isChecked()) if self.cb else (self._x, self._y)
        )
        self.signal.emit(param)


class EditWindow(QDialog, Ui_Dialog):
    def __init__(self):
        super().__init__()
        self.x0 = None
        self.y0 = None
        self.x1 = None
        self.y1 = None
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self.bx = self.cfg["bx"]
        self.by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]
        self.limit = self.cfg["limit"]

        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        self.setWindowTitle("设置")

        self.edit_w.clicked.connect(self.get_int_w)
        self.edit_h.clicked.connect(self.get_int_h)
        self.edit_a.clicked.connect(self.get_int_a)
        self.edit_bx.clicked.connect(self.get_int_bx)
        self.pushButton.clicked.connect(self.get_ms_path)
        self.edit_limit.clicked.connect(self.get_int_limit)

        self.label_w.setText(str(self.w))
        self.label_h.setText(str(self.h))
        self.label_a.setText(str(self.a))
        self.label_bx.setText(str(self.bx))
        self.label_by.setText(str(self.by))
        self.label_cell_width.setText(str(self.cell_width))
        self.label_path.setText(
            str(self.path[-30:] if len(self.path) > 30 else self.path)
        )
        self.label_limit.setText(str(self.limit))

        self.get_pos_1 = GetMousePosition()
        self.get_pos_2 = GetMousePosition()
        self.get_pos_1.pos_signal.connect(self.get_pos_1_func)
        self.get_pos_2.pos_signal.connect(self.get_pos_2_func)

        self.mouse_window = None

    def set_btns_enabled(self, enabled: bool):
        self.edit_w.setEnabled(enabled)
        self.edit_h.setEnabled(enabled)
        self.edit_a.setEnabled(enabled)
        self.pushButton.setEnabled(enabled)
        self.edit_limit.setEnabled(enabled)
        self.edit_bx.setEnabled(enabled)

    def get_int_w(self):
        value, ok = QInputDialog.getInt(self, "w", "宽度\n\n请输入整数:", self.w, 0, 10000, 2)
        if ok:
            self.cfg["w"] = value
            self.w = value
            self.label_w.setText(str(value))

    def get_int_h(self):
        value, ok = QInputDialog.getInt(self, "h", "高度\n\n请输入整数:", self.h, 0, 10000, 2)
        if ok:
            self.cfg["h"] = value
            self.h = value
            self.label_h.setText(str(value))

    def get_int_a(self):
        value, ok = QInputDialog.getInt(self, "a", "总雷数\n\n请输入整数:", self.a, 0, 10000, 2)
        if ok:
            self.cfg["a"] = value
            self.a = value
            self.label_a.setText(str(value))

    def get_int_bx(self):
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()

        self.set_btns_enabled(False)

        try:
            minesweeper_run(self.path)
            self.mouse_window = MouseWindowTread()
            self.mouse_window.start()
        except:
            self.set_btns_enabled(True)
            self.label_bx.setText(str(self.bx))
            QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
            return

        self.label_bx.setText("请将鼠标移到扫雷区域的左上角，并静止。")
        self.get_pos_1.start()

    def get_pos_1_func(self, pos):
        self.x0, self.y0 = pos
        hwnd = win32gui.FindWindow(None, setting.win_name)
        self.x0, self.y0 = ScreenToClient(hwnd, self.x0, self.y0)
        hwnd = win32gui.FindWindow(None, "设置")
        self.label_bx.setText("请将鼠标移到扫雷区域的右下角，并静止。")
        self.get_pos_2.start()
        set_top_window(hwnd)

    def get_pos_2_func(self, pos):
        self.x1, self.y1 = pos
        hwnd = win32gui.FindWindow(None, setting.win_name)
        self.x1, self.y1 = ScreenToClient(hwnd, self.x1, self.y1)

        cell_width = (self.x1 - self.x0) / self.w
        self.cell_width = round(cell_width)
        if abs(self.cell_width - cell_width) >= 0.11:
            QMessageBox.about(self, "提示", "现在每个格子的宽度不是整数，请调整扫雷窗口大小。")
            self.set_btns_enabled(True)
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
            return
        self.bx, self.by = round(self.x0 - self.cell_width * 0.5), round(
            self.y0 - self.cell_width * 0.5
        )
        self.cfg["cell_width"] = self.cell_width
        self.cfg["bx"] = self.bx
        self.cfg["by"] = self.by

        self.label_bx.setText(str(self.bx))
        self.label_by.setText(str(self.by))
        self.label_cell_width.setText(str(self.cell_width))
        self.set_btns_enabled(True)
        self.mouse_window.terminate()
        del self.mouse_window
        self.mouse_window = None

    def get_int_limit(self):
        value, ok = QInputDialog.getInt(
            self,
            "limit",
            "limit\n注：但limit每增加1计算所需的时间增加1倍\n\n请输入整数:",
            self.limit,
            0,
            10000,
            2,
        )
        if ok:
            self.cfg["limit"] = value
            self.limit = value
            self.label_limit.setText(str(value))

    def get_ms_path(self):
        a = QFileDialog.getOpenFileName(self, "选择文件", os.getcwd(), "exe(*.exe)")
        if a[0] != "":
            self.cfg["path"] = a[0]
            self.path = a[0]
            self.label_path.setText(
                str(self.path[-30:] if len(self.path) > 30 else self.path)
            )

    def accept(self):
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
        with open(r"cfg.json", "w") as file:
            json.dump(self.cfg, file, cls=MyEncoder)
        QDialog.accept(self)

    def closeEvent(self, a0: QtGui.QCloseEvent) -> None:
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_
            self.mouse_window = None
        with open(r"cfg.json", "w") as file:
            json.dump(self.cfg, file, cls=MyEncoder)
        self.close()

    def reject(self) -> None:
        if self.get_pos_1.isRunning():
            self.get_pos_1.terminate()
        if self.get_pos_2.isRunning():
            self.get_pos_2.terminate()
        if self.mouse_window:
            self.mouse_window.terminate()
            del self.mouse_window
            self.mouse_window = None
        QDialog.reject(self)


class ScreenShot(QtWidgets.QWidget, Ui_form):
    def __init__(self, hwnd):
        super().__init__()
        self.setupUi(self)
        self.setWindowIcon(QtGui.QIcon(":/icons/icon.ico"))
        self.setWindowTitle("截图帮助")

        self.img = None
        with open("cfg.json", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.a = self.cfg["a"]
        self.w = self.cfg["w"]
        self.h = self.cfg["h"]
        self.bx = self.cfg["bx"]
        self.by = self.cfg["by"]
        self.cell_width = self.cfg["cell_width"]
        self.path = self.cfg["path"]
        self.limit = self.cfg["limit"]

        h = int(self.cell_width * 7 / 9)
        w = int(self.cell_width * 5 / 9)
        self.hwndDC = win32gui.GetWindowDC(hwnd)
        self.mfcDC = win32ui.CreateDCFromHandle(self.hwndDC)
        self.saveDC = self.mfcDC.CreateCompatibleDC()
        self.saveBM = win32ui.CreateBitmap()
        self.saveBM.CreateCompatibleBitmap(self.mfcDC, w, h)
        self.saveDC.SelectObject(self.saveBM)

        self._name_list = [
            "0.bmp",
            "0_1.bmp",
            "0_2.bmp",
            "1.bmp",
            "1_1.bmp",
            "1_2.bmp",
            "2.bmp",
            "2_1.bmp",
            "3.bmp",
            "3_1.bmp",
            "4.bmp",
            "4_1.bmp",
            "5.bmp",
            "5_1.bmp",
            "6.bmp",
            "6_1.bmp",
            "7.bmp",
            "7_1.bmp",
            "8.bmp",
            "8_1.bmp",
            "9.bmp",
            "9_1.bmp",
            "9_2.bmp",
            "10.bmp",
            "10_1.bmp",
        ]
        for n in self._name_list:
            self.comboBox_name.addItem(n)
        self.re_screenshot.clicked.connect(self.screen_shot)
        self.save_img.clicked.connect(self._save_img)

    def screen_shot(self):
        try:
            i = int(self.lineEdit_i.text())
            j = int(self.lineEdit_j.text())
            x = i * self.cell_width
            y = j * self.cell_width
            h = int(self.cell_width * 7 / 9)
            w = int(self.cell_width * 5 / 9)

            hwnd = win32gui.FindWindow(None, setting.win_name)
            bx, by = ClientToScreen(hwnd, self.bx, self.by)
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
            self.img = cv2.cvtColor(pil_img, cv2.COLOR_RGB2BGR)

            self.img = self.img[
                y - self.cell_width // 2 - h // 2 : y - self.cell_width // 2 + h // 2,
                x - self.cell_width // 2 - w // 2 : x - self.cell_width // 2 + w // 2,
                :,
            ]
            cv2.imwrite("image/temp.bmp", self.img)
            self.img_label.setText("")
            self.img_label.setPixmap(QPixmap("image/temp.bmp").scaled(100, 140))
            self.img_label.repaint()
        except win32ui.error:
            QMessageBox.about(self, "错误", "找不到扫雷窗口哦。\n将要重新打开扫雷窗口。")
            try:
                minesweeper_run(self.path)
            except:
                QMessageBox.warning(self, "错误", "请检查设置中扫雷exe的路径是否输入正确")
        except ValueError as e:
            QMessageBox.about(self, "错误", "横纵坐标都要为整数哦。")

    def _save_img(self):
        if self.img is not None:
            name = self._name_list[self.comboBox_name.currentIndex()]
            cv2.imwrite(f"image/{name}", self.img)
            QMessageBox.about(self, "信息", f'图片一保存至"./image/{name}"')
            self.img = None
        else:
            QMessageBox.about(self, "错误", "请先截图才能保存哦。")


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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    ex = MyMainWindow()
    sys.exit(app.exec_())
