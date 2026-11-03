# -*- coding: utf-8 -*-
"""验证帮助页图例色带与现场色带同源（P1-1）。"""
import os
import re
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


def parse_stops(qss):
    """从 QSS 里解析 [(pos, (r,g,b))]。"""
    out = []
    for m in re.finditer(
            r"stop:(\d*\.?\d+)\s+rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
            qss):
        out.append((float(m.group(1)),
                    (int(m.group(2)), int(m.group(3)), int(m.group(4)))))
    return out


def main():
    from PyQt5 import QtWidgets, QtGui

    app = QtWidgets.QApplication([])
    db = QtGui.QFontDatabase()
    for p in (r"C:\Windows\Fonts\msyh.ttc",):
        if os.path.exists(p):
            db.addApplicationFont(p)

    import main as M
    import ui.window as W

    print("=" * 88)
    print("验证图例色带与现场色带同源（P1-1）")
    print("=" * 88)

    # 现场色带真源
    site = M.oklch_grad
    print(f"  现场色带 oklch_grad：{len(site)} 阶，"
          f"左 {site[0]} 中 {site[len(site) // 2]} 右 {site[-1]}")

    # 图例色带
    qss = W._make_gradient_qss()
    stops = parse_stops(qss)
    print(f"  图例色带：{len(stops)} 个采样点")
    for pos, rgb in stops:
        print(f"    pos={pos:.2f} rgb{rgb}")

    check("图例不再使用写死的红→绿线性渐变",
          "rgba(255,0,0,255)" not in qss.replace(" ", ""),
          "仍含硬编码红")
    check("图例采样点数量 >= 10", len(stops) >= 10, f"只有 {len(stops)}")
    check("图例端点与现场端点一致",
          stops[0][1] == tuple(site[0]) and stops[-1][1] == tuple(site[-1]),
          f"图例 {stops[0][1]}..{stops[-1][1]} vs 现场 "
          f"{tuple(site[0])}..{tuple(site[-1])}")

    # 中点一致性：图例中间停靠点应与现场中点接近（容差 12/255）
    mid_stop = stops[len(stops) // 2][1]
    mid_site = tuple(site[len(site) // 2])
    dist = max(abs(a - b) for a, b in zip(mid_stop, mid_site))
    print(f"  图例中点 {mid_stop} vs 现场中点 {mid_site}，最大分差 {dist}")
    check("图例中点与现场中点接近（<=12/255）", dist <= 12,
          f"分差 {dist}")

    # 与旧线性渐变对比，证明差异是真实的
    old_mid = (0 * 0 + 255 // 2, 0, 0)  # 旧线性中点近似 rgb(128,127,0)
    old_mid = (128, 127, 0)
    dist_old = max(abs(a - b) for a, b in zip(old_mid, mid_site))
    print(f"  对照：旧线性渐变中点 {old_mid} 与现场中点分差 {dist_old}"
          f"（这就是原来读错色的幅度）")
    check("能证明旧图例确实偏离现场（分差 > 50）", dist_old > 50,
          f"只差 {dist_old}")

    print()
    print(f"结果：PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
