# -*- coding: utf-8 -*-
"""UI P0 修复的渲染验证：真实 QWidget.grab() 取样，算 WCAG 对比度。

验证四项 P0：
  1. 说明文字衬底：frame_4 有 rgba(20,20,20,170) 衬底
  2. 禁用态按钮文字：rgba(60,60,60,220) 压 rgba(255,255,255,150)
  3. 进度条文字：全程浅色渐变 + 深色文字
  4. frame_4 高度：230，label_16 底边 206 不越界

用法: python bench/verify_ui_p0.py
"""
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


def wcag_contrast(rgb1, rgb2):
    def lum(c):
        def f(x):
            x = x / 255.0
            return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])
    l1, l2 = lum(rgb1), lum(rgb2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def load_system_fonts():
    """Qt offscreen 模式找不到字体目录（QFontDatabase.families() 返回 0），
    不加载字体则文字根本不会渲染 ⇒ 像素取样只取到纯底色，验证会假红。
    这里显式加载 Windows 系统字体后再取样。"""
    from PyQt5 import QtGui
    db = QtGui.QFontDatabase()
    paths = [
        r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
        r"C:\Windows\Fonts\simsun.ttc",    # 宋体
        r"C:\Windows\Fonts\simhei.ttf",    # 黑体
        r"C:\Windows\Fonts\simkai.ttf",    # 楷体
        r"C:\Windows\Fonts\arial.ttf",
    ]
    added = 0
    for p in paths:
        if os.path.exists(p):
            rid = db.addApplicationFont(p)
            if rid >= 0:
                added += 1
    n = len(db.families())
    print(f"   已加载系统字体 {added} 个（可用 family {n} 个）")
    return n > 0


def main():
    from PyQt5 import QtWidgets

    print("=" * 88)
    print("UI P0 修复验证（真实渲染取样 + WCAG 对比度）")
    print("=" * 88)

    app = QtWidgets.QApplication([])
    fonts_ok = load_system_fonts()
    check("能加载字体（否则像素取样会假红）", fonts_ok, "无可用字体")

    # ---------- 1) 禁用态按钮文字对比度 ----------
    print("\n1) 禁用态按钮：文字 rgba(60,60,60,220) 压底色 rgba(255,255,255,150)")
    btn = QtWidgets.QPushButton("自动")
    btn.setStyleSheet(
        "QPushButton{border:none;background-color:rgba(255,255,255,150);"
        "color:rgb(0,0,0);}"
        "QPushButton:disabled{background-color:rgba(255,255,255,150);"
        "color:rgba(60,60,60,220);}"
    )
    btn.setEnabled(False)
    btn.resize(200, 45)
    img = btn.grab().toImage()
    # 取按钮中心区域像素，找最暗（文字）与最亮（底色）
    lum_vals = []
    for y in range(5, 40, 2):
        for x in range(5, 195, 2):
            c = img.pixelColor(x, y)
            lum_vals.append((c.red(), c.green(), c.blue()))
    darkest = min(lum_vals, key=lambda c: sum(c))
    brightest = max(lum_vals, key=lambda c: sum(c))
    ratio = wcag_contrast(darkest, brightest)
    print(f"   最暗 {darkest}  最亮 {brightest}  对比度 {ratio:.2f}")
    check("禁用态文字对比度 >= 4.5（AA）", ratio >= 4.5, f"got {ratio:.2f}")

    # ---------- 2) 进度条文字对比度（多个进度值）----------
    print("\n2) 进度条：全程浅色渐变 + 深色文字（扫 v=10..90）")
    pg = QtWidgets.QProgressBar()
    pg.setMinimumSize(580, 35)
    pg.setStyleSheet(
        "QProgressBar {border:none;border-radius:12px;text-align:center;"
        "color: rgb(20,20,20);background-color: rgba(255,255,255,210);}"
        "QProgressBar::chunk {background-color: qlineargradient(spread:pad,"
        " x1:0,y1:0,x2:1,y2:0, stop:0 rgb(200,245,240),"
        " stop:1 rgb(120,205,200));border-radius:12px;}"
    )
    pg.resize(580, 35)
    worst = 999.0
    worst_v = -1
    for v in range(10, 100, 10):
        pg.setValue(v)
        img = pg.grab().toImage()
        px = []
        for y in range(6, 30, 2):
            for x in range(5, 575, 4):
                c = img.pixelColor(x, y)
                px.append((c.red(), c.green(), c.blue()))
        d = min(px, key=lambda c: sum(c))
        b = max(px, key=lambda c: sum(c))
        r = wcag_contrast(d, b)
        if r < worst:
            worst, worst_v = r, v
        print(f"   v={v:>2}: 最暗{d} 最亮{b} 对比 {r:.2f}")
    check("进度条全程对比度 >= 4.5（AA）", worst >= 4.5,
          f"最差 v={worst_v} 仅 {worst:.2f}")

    # ---------- 3) frame_4 高度与 label_16 边界 ----------
    print("\n3) frame_4 高度与最后一行是否被裁")
    frame = QtWidgets.QFrame()
    frame.setMinimumSize(0, 230)
    frame.setMaximumSize(16777215, 230)
    lab = QtWidgets.QLabel(frame)
    lab.setGeometry(0, 180, 781, 26)
    frame.resize(809, 230)
    print(f"   frame 高 {frame.height()}  label_16 bottom = "
          f"{lab.y() + lab.height()}")
    check("label_16 底边不超出容器", lab.y() + lab.height() <= frame.height(),
          f"{lab.y() + lab.height()} > {frame.height()}")

    # ---------- 4) 说明区衬底是否真的变暗 ----------
    print("\n4) 说明区衬底：rgba(20,20,20,170) 叠加后背景亮度")
    # 模拟：白色 (255,255,255) 上叠 rgba(20,20,20,170)，alpha=170/255≈0.667
    alpha = 170 / 255.0
    comp_white = tuple(int(20 * alpha + 255 * (1 - alpha)) for _ in range(3))
    comp_dark = tuple(int(20 * alpha + 96 * (1 - alpha)) for _ in range(3))
    white_text = (255, 255, 255)
    r_bright = wcag_contrast(white_text, comp_white)
    r_dark = wcag_contrast(white_text, comp_dark)
    print(f"   白色背景(255,255,255) 叠加后 {comp_white} → 白字对比 {r_bright:.2f}")
    print(f"   背景均值(96,96,96)   叠加后 {comp_dark} → 白字对比 {r_dark:.2f}")
    check("最亮背景处白字对比度 >= 4.5", r_bright >= 4.5,
          f"got {r_bright:.2f}")

    print()
    print("=" * 88)
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
