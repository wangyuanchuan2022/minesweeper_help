# -*- coding: utf-8 -*-
"""验证主界面能真实实例化，且 P0 修复到位（不依赖单元测试）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main():
    from PyQt5 import QtWidgets, QtGui

    app = QtWidgets.QApplication([])
    db = QtGui.QFontDatabase()
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simkai.ttf"):
        if os.path.exists(p):
            db.addApplicationFont(p)

    import ui.window as W

    # ⚠️ 必须保留 QMainWindow 的引用，否则被 GC 回收后再访问子控件会报
    # "wrapped C/C++ object has been deleted"（我第一次就是这么踩的）。
    win = QtWidgets.QMainWindow()
    ui = W.Ui_MainWindow()
    ui.setupUi(win)
    print("  setupUi 执行成功")

    check("frame_4 的 min 与 max 不再自相矛盾",
          ui.frame_4.minimumHeight() <= ui.frame_4.maximumHeight(),
          f"min={ui.frame_4.minimumHeight()} max={ui.frame_4.maximumHeight()}")
    check("frame_4 高度足以容纳 7 行（>= 206）",
          ui.frame_4.minimumHeight() >= 206,
          f"min={ui.frame_4.minimumHeight()}")
    check("label_16 底边不超出 frame_4",
          ui.label_16.y() + ui.label_16.height() <= ui.frame_4.minimumHeight(),
          f"bottom={ui.label_16.y() + ui.label_16.height()}")
    check("frame_4 有半透明深色衬底",
          "rgba(20, 20, 20" in ui.frame_4.styleSheet(),
          ui.frame_4.styleSheet()[:60])
    check("进度条 QSS 显式设置了 color",
          "color:" in ui.pgb.styleSheet(),
          ui.pgb.styleSheet()[:60])
    check("进度条 chunk 渐变全程浅色（无深墨绿末端）",
          "rgb(4, 126, 127)" not in ui.pgb.styleSheet(),
          "仍含深墨绿")
    check("禁用态按钮文字不再是白色",
          ui.auto_play.styleSheet().count("255, 255, 255, 175") == 0,
          "仍是白色 175")

    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    win.deleteLater()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
